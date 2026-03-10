"""Disk-backed podping queue using SQLite for crash resilience."""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import aiosqlite
from pydantic import HttpUrl

DEDUP_WINDOW_SECONDS = 180  # ignore duplicate URLs within this window
PURGE_SENT_AFTER_SECONDS = 86400  # 24 hours

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_podpings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL,
    medium      TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    received_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_podpings (
    url     TEXT NOT NULL,
    medium  TEXT NOT NULL,
    reason  TEXT NOT NULL,
    trx_id  TEXT NOT NULL,
    sent_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sent_url_time ON sent_podpings(url, sent_at);
"""


class PodpingQueue:
    """Async SQLite-backed queue for podping URLs."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # ``_db`` is None until ``open`` is called; every method that uses it
        # asserts its presence to satisfy mypy.
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        pending = await self.count_pending()
        if pending:
            logging.info(f"Queue opened with {pending} pending items from previous run")

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def count_pending(self) -> int:
        assert self._db is not None
        async with self._db.execute("SELECT COUNT(*) FROM pending_podpings") as cur:
            row = await cur.fetchone()
            if row is None:
                return 0
            return row[0]

    async def enqueue(self, url: HttpUrl, medium: str, reason: str) -> int:
        """Insert a URL into the pending queue.

        Before inserting we perform the RC-style dedup check against the
        ``sent_podpings`` table.  If the URL was sent within the dedup window
        we log and return ``0`` to indicate nothing was added.  This ensures the
        expensive query is done once at enqueue time instead of on every
        dequeue/peek that touches the backlog.
        """
        assert self._db is not None
        url_str = str(url)
        now = time.time()

        # dedup against sent records
        cutoff = now - DEDUP_WINDOW_SECONDS
        async with self._db.execute(
            "SELECT 1 FROM sent_podpings WHERE url = ? AND sent_at > ? LIMIT 1",
            (url_str, cutoff),
        ) as cur:
            recently = await cur.fetchone()
        if recently:
            logging.debug(f"Dedup skip: {url_str} was sent within last {DEDUP_WINDOW_SECONDS}s")
            return 0

        async with self._db.execute(
            "INSERT INTO pending_podpings (url, medium, reason, received_at) VALUES (?, ?, ?, ?)",
            (url_str, medium, reason, now),
        ) as cur:
            row_id = cur.lastrowid
        await self._db.commit()
        return row_id

    async def dequeue_batch(
        self, medium: str | None = None, reason: str | None = None
    ) -> List[Dict[str, Any]]:
        """Fetch and remove pending items, optionally filtering by medium/reason.

        The behaviour is unchanged from legacy versions: matching rows are
        deduplicated, returned, and removed from the pending table _before_
        the caller has a chance to send them.  This is convenient when the
        caller doesn't need to retry on failure but is unsafe when dispatch
        operations may fail (because items are lost).

        Returns a list of dicts with keys: id, url, medium, reason,
        received_at.  Items are ordered by insertion id.  Duplicates (within
        the batch or against recently sent) are dropped silently.
        """
        assert self._db is not None

        # delegate to peek_batch and then delete the returned ids
        to_send, all_ids = await self.peek_batch(medium=medium, reason=reason)
        if all_ids:
            await self.remove_pending(all_ids)
        return to_send

    async def peek_batch(
        self, medium: str | None = None, reason: str | None = None
    ) -> tuple[List[Dict[str, Any]], List[int]]:
        """Return a batch of pending items without deleting them.

        This is the non‑destructive counterpart to :meth:`dequeue_batch`.
        It performs the same filtering and deduplication logic but leaves the
        rows in the database.  The caller can later remove the rows via
        :meth:`remove_pending` on successful dispatch.  ``all_ids`` contains
        every row id that matched the query, not just those that passed
        deduplication, mirroring the old behaviour.
        """
        assert self._db is not None

        now = time.time()
        cutoff = now - DEDUP_WINDOW_SECONDS

        # build query with optional filters
        base: str = "SELECT id, url, medium, reason, received_at FROM pending_podpings"
        clauses: List[str] = []
        params: List[Any] = []
        if medium is not None:
            clauses.append("medium = ?")
            params.append(medium)
        if reason is not None:
            clauses.append("reason = ?")
            params.append(reason)
        if clauses:
            base += " WHERE " + " AND ".join(clauses)
        base += " ORDER BY id"

        async with self._db.execute(base, params) as cur:
            rows = await cur.fetchall()

        if not rows:
            return [], []

        to_send: List[Dict[str, Any]] = []
        all_ids: List[int] = []
        seen_in_batch: Set[str] = set()

        for row_id, url, medium, reason, received_at in rows:
            all_ids.append(row_id)

            # skip any URL we’ve already processed this batch (either because it
            # was marked sent or because it was queued to send).  doing it here
            # prevents repeated log lines.
            if url in seen_in_batch:
                continue

            seen_in_batch.add(url)
            to_send.append(
                {
                    "id": row_id,
                    "url": url,
                    "medium": medium,
                    "reason": reason,
                    "received_at": received_at,
                }
            )

        return to_send, all_ids

    async def remove_pending(self, ids: List[int]):
        """Delete pending rows by their ``id``.

        This is primarily intended for use after a successful dispatch of the
        items returned by :meth:`peek_batch`.
        """
        if not ids:
            return
        assert self._db is not None
        placeholders = ",".join("?" for _ in ids)
        await self._db.execute(
            f"DELETE FROM pending_podpings WHERE id IN ({placeholders})",
            ids,
        )
        await self._db.commit()

    async def mark_sent(self, url: str, medium: str, reason: str, trx_id: str):
        """Record a successfully sent podping for dedup tracking."""
        assert self._db is not None
        now = time.time()
        await self._db.execute(
            "INSERT INTO sent_podpings (url, medium, reason, trx_id, sent_at) VALUES (?, ?, ?, ?, ?)",
            (url, medium, reason, trx_id, now),
        )
        await self._db.commit()

    async def purge_old_sent(self):
        """Remove sent records older than 24 hours."""
        assert self._db is not None
        cutoff = time.time() - PURGE_SENT_AFTER_SECONDS
        await self._db.execute("DELETE FROM sent_podpings WHERE sent_at < ?", (cutoff,))
        await self._db.commit()

    # ------------------------------------------------------------------
    # Convenience helpers for background batching logic
    # ------------------------------------------------------------------

    async def oldest_pending(self, reason: str | None = None) -> float | None:
        """Return the oldest ``received_at`` timestamp for pending rows.

        If ``reason`` is provided, only rows matching that reason are
        considered.  Returns ``None`` when there are no pending items.
        This allows callers (e.g. the processing loop in :mod:`api`) to decide
        whether the oldest entry has been sitting around long enough to send a
        batch.
        """
        assert self._db is not None
        if reason is not None:
            query = "SELECT MIN(received_at) FROM pending_podpings WHERE reason = ?"
            params = (reason,)
        else:
            query = "SELECT MIN(received_at) FROM pending_podpings"
            params = ()

        async with self._db.execute(query, params) as cur:
            row = await cur.fetchone()
        if not row or row[0] is None:
            return None
        return row[0]

    async def ready_to_send(self, reason: str, interval: float) -> bool:
        """Return ``True`` when a batch for ``reason`` should be dequeued.

        This is *independent* of the deduplication logic: we simply look at the
        timestamp of the oldest pending row and ensure it has been in the queue
        for at least ``interval`` seconds.  It keeps the batching semantics local
        to the queue layer so that the API's background loop remains clean.
        """
        oldest = await self.oldest_pending(reason)
        if oldest is None:
            return False
        return time.time() - oldest >= interval

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

        Before inserting we perform deduplication in two places:

        * ``sent_podpings`` – if the URL was broadcast within the recent
          ``DEDUP_WINDOW_SECONDS`` we return ``0`` and do not enqueue another
          copy.  This mirrors the previous behaviour and avoids firing the
          same podping too frequently.
        * ``pending_podpings`` – if the URL already exists in the pending
          table we also return ``0``.  This protects against clients queuing
          the same podcast multiple times while the first request is still
          waiting to be sent.  Unlike the sent check there is no time window –
          once an item is queued another identical request is considered a
          duplicate until the original row is removed.

        Returning ``0`` is how the caller (and the API layer) knows a duplicate
        was detected.  Performing these checks here keeps the expensive
        database work out of the dequeue/peek paths and makes the behaviour
        crash-safe.
        """
        assert self._db is not None
        url_str = str(url)
        now = time.time()

        # dedup against sent records. previously we only considered the
        # URL, which meant a rapid pair of ``live``/``liveEnd`` podpings would
        # be treated as duplicates.  the user reported exactly that issue, so
        # we now include **medium** and **reason** in the check.  the stored
        # tables already record those fields, so the queries are extended
        # accordingly.
        cutoff = now - DEDUP_WINDOW_SECONDS
        async with self._db.execute(
            "SELECT 1 FROM sent_podpings WHERE url = ? AND medium = ? AND reason = ? "
            "AND sent_at > ? LIMIT 1",
            (url_str, medium, reason, cutoff),
        ) as cur:
            recently = await cur.fetchone()
        if recently:
            logging.debug(
                f"Dedup skip: {url_str} ({medium},{reason}) was sent within last "
                f"{DEDUP_WINDOW_SECONDS}s"
            )
            return 0

        # also avoid enqueueing an identical tuple that is already pending.
        # this check still has no time window – once a particular combination
        # is queued we don't need another copy until the first one is either
        # sent or removed.
        async with self._db.execute(
            "SELECT 1 FROM pending_podpings WHERE url = ? AND medium = ? AND reason = ? LIMIT 1",
            (url_str, medium, reason),
        ) as cur:
            already = await cur.fetchone()
        if already:
            logging.debug(f"Dedup skip: {url_str} ({medium},{reason}) already pending")
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
        # previously we only de‑duplicated on ``url`` within a batch; this had
        # the same flaw as the enqueue logic.  use a tuple of all three fields
        # so that different reasons/mediums are treated separately.
        seen_in_batch: Set[tuple] = set()

        for row_id, url, medium, reason, received_at in rows:
            all_ids.append(row_id)

            key = (url, medium, reason)
            # skip any triple we’ve already processed this batch (either because
            # it was marked sent or because we’ve already queued it). doing it
            # here prevents repeated log lines.
            if key in seen_in_batch:
                continue

            seen_in_batch.add(key)
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
        """Remove sent records older than 24 hours.

        If the queue has already been closed, simply do nothing.  The
        background loop may call this during shutdown and should not raise
        assertions in that case.
        """
        if self._db is None:
            return
        cutoff = time.time() - PURGE_SENT_AFTER_SECONDS
        await self._db.execute("DELETE FROM sent_podpings WHERE sent_at < ?", (cutoff,))
        await self._db.commit()

    # ------------------------------------------------------------------
    # Convenience helpers for background batching logic
    # ------------------------------------------------------------------

    async def oldest_pending(self, reason: str | None = None) -> float | None:
        """Return the oldest ``received_at`` timestamp for pending rows.

        If ``reason`` is provided, only rows matching that reason are
        considered.  Returns ``None`` when there are no pending items.  If the
        queue has already been closed (``_db is None``), ``None`` is returned
        rather than asserting – callers should treat this as "no work".
        """
        if self._db is None:
            return None
        if reason is not None:
            query = "SELECT MIN(received_at) FROM pending_podpings WHERE reason = ?"
            params = (reason,)
        else:
            query = "SELECT MIN(received_at) FROM pending_podpings"
            params = ()

        try:
            async with self._db.execute(query, params) as cur:
                row = await cur.fetchone()
        except Exception:
            # database is gone or closed; behave as if there are no pending rows
            return None
        if not row or row[0] is None:
            return None
        return row[0]

    async def ready_to_send(self, reason: str, interval: float) -> bool:
        """Return ``True`` when a batch for ``reason`` should be dequeued.

        This is *independent* of the deduplication logic: we simply look at the
        timestamp of the oldest pending row and ensure it has been in the queue
        for at least ``interval`` seconds.  It keeps the batching semantics local
        to the queue layer so that the API's background loop remains clean.

        If the database has been closed the helper returns ``False`` so the loop
        can exit gracefully during shutdown.
        """
        oldest = await self.oldest_pending(reason)
        if oldest is None:
            return False
        return time.time() - oldest >= interval

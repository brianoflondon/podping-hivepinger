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
        """Insert a URL into the pending queue. Returns the row id."""
        assert self._db is not None
        now = time.time()
        async with self._db.execute(
            "INSERT INTO pending_podpings (url, medium, reason, received_at) VALUES (?, ?, ?, ?)",
            (url, medium, reason, now),
        ) as cur:
            row_id = cur.lastrowid
        await self._db.commit()
        return row_id

    async def dequeue_batch(
        self, medium: str | None = None, reason: str | None = None
    ) -> List[Dict[str, Any]]:
        """Fetch pending items, optionally filtering by medium/reason.

        Only rows matching the provided ``medium`` and/or ``reason`` values are
        returned.  The selected items are deduplicated against recently sent
        records and removed from the pending table just as before.

        Returns a list of dicts with keys: id, url, medium, reason, received_at.
        Items are ordered by insertion id.  Duplicates (within the batch or
        against recently sent) are dropped silently.
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

        # grab everything pending that matches the filters
        async with self._db.execute(base, params) as cur:
            rows = await cur.fetchall()

        if not rows:
            return []

        to_send: List[Dict[str, Any]] = []
        all_ids: List[int] = []
        seen_in_batch: Set[str] = set()

        for row_id, url, medium, reason, received_at in rows:
            all_ids.append(row_id)

            # skip if we already picked this URL in this batch
            if url in seen_in_batch:
                logging.debug(f"Dedup skip (intra-batch): {url}")
                continue

            # check dedup against recently sent
            async with self._db.execute(
                "SELECT 1 FROM sent_podpings WHERE url = ? AND sent_at > ? LIMIT 1",
                (url, cutoff),
            ) as cur:
                already_sent = await cur.fetchone()
            if already_sent:
                logging.info(f"Dedup skip: {url} was sent within last {DEDUP_WINDOW_SECONDS}s")
            else:
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

        # remove ALL pending rows (including dupes) in one statement
        if all_ids:
            placeholders = ",".join("?" for _ in all_ids)
            await self._db.execute(
                f"DELETE FROM pending_podpings WHERE id IN ({placeholders})",
                all_ids,
            )
            await self._db.commit()

        return to_send

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

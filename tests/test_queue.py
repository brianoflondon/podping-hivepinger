"""Tests for PodpingQueue — enqueue, dedup, mark_sent, purge."""

import time

import pytest
import pytest_asyncio

from app.podping_queue import DEDUP_WINDOW_SECONDS, PURGE_SENT_AFTER_SECONDS, PodpingQueue


@pytest_asyncio.fixture
async def queue(tmp_path):
    db_path = str(tmp_path / "test_queue.db")
    q = PodpingQueue(db_path)
    await q.open()
    yield q
    await q.close()


@pytest.mark.asyncio
async def test_enqueue_and_dequeue(queue: PodpingQueue):
    row_id = await queue.enqueue("https://example.com/feed.xml", "podcast", "update")
    assert row_id is not None

    batch = await queue.dequeue_batch()
    assert len(batch) == 1
    assert batch[0]["url"] == "https://example.com/feed.xml"
    assert batch[0]["medium"] == "podcast"
    assert batch[0]["reason"] == "update"

    # pending should now be empty
    batch2 = await queue.dequeue_batch()
    assert len(batch2) == 0

    # enqueue two different reasons and ensure filtering works
    await queue.enqueue("https://example.org/one", "podcast", "update")
    await queue.enqueue("https://example.org/two", "podcast", "live")
    upd = await queue.dequeue_batch(reason="update")
    assert len(upd) == 1 and upd[0]["reason"] == "update"
    live = await queue.dequeue_batch(reason="live")
    assert len(live) == 1 and live[0]["reason"] == "live"


@pytest.mark.asyncio
async def test_dedup_blocks_recently_sent(queue: PodpingQueue):
    url = "https://example.com/live.xml"
    await queue.enqueue(url, "music", "live")

    # simulate having sent it just now
    await queue.mark_sent(url, "music", "live", "abc123")

    # enqueue the same URL again
    await queue.enqueue(url, "music", "live")

    # dequeue should return nothing (dedup window of 180s)
    batch = await queue.dequeue_batch()
    assert len(batch) == 0


@pytest.mark.asyncio
async def test_dedup_allows_after_window(queue: PodpingQueue, monkeypatch):
    url = "https://example.com/old.xml"

    # mark as sent with a timestamp older than the dedup window
    old_time = time.time() - DEDUP_WINDOW_SECONDS - 10
    await queue._db.execute(
        "INSERT INTO sent_podpings (url, medium, reason, trx_id, sent_at) VALUES (?, ?, ?, ?, ?)",
        (url, "podcast", "update", "old_trx", old_time),
    )
    await queue._db.commit()

    await queue.enqueue(url, "podcast", "update")
    batch = await queue.dequeue_batch()
    assert len(batch) == 1
    assert batch[0]["url"] == url


@pytest.mark.asyncio
async def test_mark_sent_records_trx(queue: PodpingQueue):
    await queue.mark_sent("https://example.com/feed.xml", "podcast", "update", "deadbeef1234")

    async with queue._db.execute(
        "SELECT trx_id FROM sent_podpings WHERE url = ?",
        ("https://example.com/feed.xml",),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "deadbeef1234"


@pytest.mark.asyncio
async def test_purge_old_sent(queue: PodpingQueue):
    old_time = time.time() - PURGE_SENT_AFTER_SECONDS - 10
    recent_time = time.time()

    await queue._db.execute(
        "INSERT INTO sent_podpings (url, medium, reason, trx_id, sent_at) VALUES (?, ?, ?, ?, ?)",
        ("https://old.example.com", "podcast", "update", "old", old_time),
    )
    await queue._db.execute(
        "INSERT INTO sent_podpings (url, medium, reason, trx_id, sent_at) VALUES (?, ?, ?, ?, ?)",
        ("https://new.example.com", "podcast", "update", "new", recent_time),
    )
    await queue._db.commit()

    await queue.purge_old_sent()

    async with queue._db.execute("SELECT COUNT(*) FROM sent_podpings") as cur:
        row = await cur.fetchone()
    assert row[0] == 1  # only the recent one remains


@pytest.mark.asyncio
async def test_crash_recovery(tmp_path):
    """Pending items survive close+reopen (simulating a crash)."""
    db_path = str(tmp_path / "crash_test.db")

    q1 = PodpingQueue(db_path)
    await q1.open()
    await q1.enqueue("https://example.com/survive.xml", "music", "live")
    await q1.close()

    # reopen — the row should still be pending
    q2 = PodpingQueue(db_path)
    await q2.open()
    batch = await q2.dequeue_batch()
    assert len(batch) == 1
    assert batch[0]["url"] == "https://example.com/survive.xml"
    await q2.close()


@pytest.mark.asyncio
async def test_multiple_urls_partial_dedup(queue: PodpingQueue):
    """When some URLs are dupes and some are new, only new ones are returned."""
    await queue.mark_sent("https://example.com/dup.xml", "podcast", "update", "trx1")

    await queue.enqueue("https://example.com/dup.xml", "podcast", "update")
    await queue.enqueue("https://example.com/new.xml", "music", "live")

    batch = await queue.dequeue_batch()
    urls = [item["url"] for item in batch]
    assert "https://example.com/new.xml" in urls
    assert "https://example.com/dup.xml" not in urls

    # pending table should be fully drained
    batch2 = await queue.dequeue_batch()
    assert len(batch2) == 0

    # verify filtering still works after a partial-dedup operation
    await queue.enqueue("https://example.com/a", "podcast", "update")
    await queue.enqueue("https://example.com/b", "podcast", "live")

    upd = await queue.dequeue_batch(reason="update")
    assert len(upd) == 1 and upd[0]["url"].endswith("/a")
    live = await queue.dequeue_batch(reason="live")
    assert len(live) == 1 and live[0]["url"].endswith("/b")

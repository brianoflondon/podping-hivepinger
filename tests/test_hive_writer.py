"""Tests for the extracted hive_writer module."""

import pytest

from hivepinger.hive_actions import CustomJsonSendError
from hivepinger.hive_writer import send_podping_to_hive
from models.podping import CURRENT_PODPING_VERSION, Medium, Podping, Reason


class FakeQueue:
    """In-memory stand-in for PodpingQueue."""

    def __init__(self):
        self.marked_sent = []
        self.removed_pending = []

    async def mark_sent(self, url, medium, reason, trx_id):
        self.marked_sent.append((url, medium, reason, trx_id))

    async def remove_pending(self, ids):
        self.removed_pending.extend(ids)


class FakeRpc:
    url = "https://fake.rpc.node"


class FakeHiveClient:
    rpc = FakeRpc()


@pytest.mark.asyncio
async def test_send_podping_to_hive_success(monkeypatch):
    """On success, items should be marked sent and removed."""
    from hivepinger import hive_writer

    async def mock_send(**kwargs):
        return {"trx_id": "abc123"}

    monkeypatch.setattr(hive_writer, "send_custom_json", mock_send)

    queue = FakeQueue()
    podping = Podping(
        version=CURRENT_PODPING_VERSION,
        medium=Medium.PODCAST,
        reason=Reason.UPDATE,
        iris=["https://a.com", "https://b.com"],
        timestampNs=1,
        sessionId=42,
    )
    batch_items = [
        {"id": 1, "url": "https://a.com", "medium": "podcast", "reason": "update"},
        {"id": 2, "url": "https://b.com", "medium": "podcast", "reason": "update"},
    ]

    result = await send_podping_to_hive(
        podping_obj=podping,
        json_id="pp_podcast_update",
        hive_account_name="testaccount",
        hive_client=FakeHiveClient(),
        hive_posting_key="5Jxxx",
        queue=queue,
        batch_items=batch_items,
    )

    assert result.success is True
    assert result.should_renew_client is False
    assert result.fail_reason == ""
    assert len(queue.marked_sent) == 2
    assert queue.removed_pending == [1, 2]


@pytest.mark.asyncio
async def test_send_podping_to_hive_custom_json_error(monkeypatch):
    """CustomJsonSendError should be caught and returned as a failed result."""
    from hivepinger import hive_writer

    async def mock_send(**kwargs):
        raise CustomJsonSendError("test error")

    monkeypatch.setattr(hive_writer, "send_custom_json", mock_send)

    queue = FakeQueue()
    podping = Podping(
        version=CURRENT_PODPING_VERSION,
        medium=Medium.PODCAST,
        reason=Reason.UPDATE,
        iris=["https://fail.com"],
        timestampNs=1,
        sessionId=42,
    )

    result = await send_podping_to_hive(
        podping_obj=podping,
        json_id="pp_podcast_update",
        hive_account_name="testaccount",
        hive_client=FakeHiveClient(),
        hive_posting_key="5Jxxx",
        queue=queue,
        batch_items=[
            {"id": 1, "url": "https://fail.com", "medium": "podcast", "reason": "update"}
        ],
    )

    assert result.success is False
    assert result.should_renew_client is False
    assert "CustomJsonSendError" in result.fail_reason
    # nothing should have been marked sent
    assert len(queue.marked_sent) == 0
    assert len(queue.removed_pending) == 0


@pytest.mark.asyncio
async def test_send_podping_to_hive_unexpected_error_renews_client(monkeypatch):
    """An unexpected exception should request client renewal."""
    from hivepinger import hive_writer

    async def mock_send(**kwargs):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(hive_writer, "send_custom_json", mock_send)

    queue = FakeQueue()
    podping = Podping(
        version=CURRENT_PODPING_VERSION,
        medium=Medium.PODCAST,
        reason=Reason.UPDATE,
        iris=["https://fail.com"],
        timestampNs=1,
        sessionId=42,
    )

    result = await send_podping_to_hive(
        podping_obj=podping,
        json_id="pp_podcast_update",
        hive_account_name="testaccount",
        hive_client=FakeHiveClient(),
        hive_posting_key="5Jxxx",
        queue=queue,
        batch_items=[
            {"id": 1, "url": "https://fail.com", "medium": "podcast", "reason": "update"}
        ],
    )

    assert result.success is False
    assert result.should_renew_client is True
    assert len(queue.marked_sent) == 0

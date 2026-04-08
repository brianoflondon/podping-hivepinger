import logging

import pytest

from hivewatcher import watch


class DummyBlock:
    def __init__(self, block_num=1000):
        self.block_num = block_num


class DummyHive:
    def __init__(self, node=None):
        # record the node for assertion
        self.node = node
        # expose a blockchain attribute mimicking nectar's API
        self.blockchain = self
        # allow tests to verify that our stream method was used
        self.stream_called = False

    def get_current_block(self):
        return DummyBlock(1000)

    def stream(self, opNames=None, raw_ops=False, **kwargs):
        # record invocation so tests can assert we used blockchain.stream
        self.stream_called = True
        # ignore filters; yield a mix of operations to test prefix logic
        yield {"type": "custom_json", "id": "nope"}
        yield {"type": "custom_json", "id": "pp_first"}
        yield {"type": "custom_json", "id": "pplt_second"}


@pytest.fixture(autouse=True)
def _isolate_block_file(monkeypatch, tmp_path):
    """Redirect the persistent block-number file to a temp directory so tests
    never read/write the real ``data/latest_block_num.json``."""
    monkeypatch.setattr(watch, "DATA_DIR", tmp_path)
    monkeypatch.setattr(watch, "LATEST_BLOCK_FILE", tmp_path / "latest_block_num.json")


@pytest.mark.asyncio
async def test_async_watch_filters(monkeypatch, caplog):
    """Verify async_watch filters and logs matching operations.

    The watcher now emits logging instead of printing; ``caplog`` is used to
    capture messages.  We also confirm the dummy client's streaming method
    was invoked.
    """
    caplog.set_level(logging.INFO)

    # patch Hive used in watcher to our dummy; also ensure the
    # ``Blockchain`` wrapper simply returns the provided client so that
    # ``client.stream`` is exercised (and recorded via ``stream_called``).
    monkeypatch.setattr(watch, "Hive", DummyHive)
    monkeypatch.setattr(watch, "Blockchain", lambda client: client)

    async def noop_send_test_podping(*args, **kwargs):
        return None

    monkeypatch.setattr(watch, "send_test_podping", noop_send_test_podping)

    # we pass a dummy client so ``node`` argument is skipped and we can also
    # assert the node value below
    client = DummyHive(node="https://example.com")

    await watch.async_watch("pp", hive_client=client, max_ops=2)  # type: ignore

    records = [r.getMessage() for r in caplog.records]
    assert any("pp_first" in msg for msg in records)
    assert any("pplt_second" in msg for msg in records)
    assert not any("nope" in msg for msg in records)
    # ensure our dummy blockchain stream method was actually used
    assert client.stream_called


class FailingHive(DummyHive):
    def __init__(self, node=None):
        super().__init__(node=node)
        self.call_count = 0

    def get_current_block(self):
        return DummyBlock(1000)

    def stream(self, opNames=None, raw_ops=False, **kwargs):
        self.stream_called = True
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("network failure")
        yield {"type": "custom_json", "id": "pp_recovered"}


@pytest.mark.asyncio
async def test_async_watch_recovers_after_stream_failure(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(watch, "Hive", FailingHive)
    monkeypatch.setattr(watch, "Blockchain", lambda client: client)

    async def noop_send_test_podping(*args, **kwargs):
        return None

    monkeypatch.setattr(watch, "send_test_podping", noop_send_test_podping)

    client = FailingHive(node="https://example.com")
    await watch.async_watch("pp", hive_client=client, max_ops=1, retry_delay=0.0)  # type: ignore

    records = [r.getMessage() for r in caplog.records]
    assert any("pp_recovered" in msg for msg in records)
    assert client.call_count == 2

import logging

import pytest

from hivewatcher import watch


class DummyHive:
    def __init__(self, node=None):
        # record the node for assertion
        self.node = node
        # expose a blockchain attribute mimicking nectar's API
        self.blockchain = self
        # allow tests to verify that our stream method was used
        self.stream_called = False

    def stream(self, opNames=None, raw_ops=False, **kwargs):
        # record invocation so tests can assert we used blockchain.stream
        self.stream_called = True
        # ignore filters; yield a mix of operations to test prefix logic
        yield {"type": "custom_json", "id": "nope"}
        yield {"type": "custom_json", "id": "pp_first"}
        yield {"type": "custom_json", "id": "pplt_second"}


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

    # we pass a dummy client so ``node`` argument is skipped and we can also
    # assert the node value below
    client = DummyHive(node="https://example.com")

    await watch.async_watch("pp", hive_client=client, max_ops=2)

    records = [r.getMessage() for r in caplog.records]
    assert any("pp_first" in msg for msg in records)
    assert any("pplt_second" in msg for msg in records)
    assert not any("nope" in msg for msg in records)
    # ensure our dummy blockchain stream method was actually used
    assert client.stream_called

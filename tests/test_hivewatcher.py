import asyncio

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
async def test_async_watch_filters(monkeypatch, capsys):
    # patch Hive used in watcher to our dummy; also ensure the
    # ``Blockchain`` wrapper simply returns the provided client so that
    # ``client.stream`` is exercised (and recorded via ``stream_called``).
    monkeypatch.setattr(watch, "Hive", DummyHive)
    monkeypatch.setattr(watch, "Blockchain", lambda client: client)

    # we pass a dummy client so ``node`` argument is skipped and we can also
    # assert the node value below
    client = DummyHive(node="https://example.com")

    await watch.async_watch("pp", hive_client=client, max_ops=2)
    out = capsys.readouterr().out
    assert "pp_first" in out
    assert "pplt_second" in out
    assert "nope" not in out
    # ensure our dummy blockchain stream method was actually used
    assert client.stream_called


def test_hive_client_created_with_node(monkeypatch):
    # if we don't supply a client, the watcher should construct one with the
    # provided node argument
    created = {}

    def make(node=None):
        created["node"] = node
        return DummyHive(node=node)

    # patch both Hive and Blockchain so the helper can instantiate them
    monkeypatch.setattr(watch, "Hive", make)
    monkeypatch.setattr(watch, "Blockchain", lambda client: client)
    # run the async helper briefly with max_ops=0 so it exits quickly
    asyncio.run(watch.async_watch("foo", node="bar", max_ops=0))
    assert created["node"] == ["bar"]

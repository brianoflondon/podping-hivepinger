from decimal import Decimal

import pytest

from hivepinger import hive_actions


class DummyHive:
    def __init__(self, node=None, keys=None, nobroadcast=False):
        # mirror constructor signature used in get_hive_client
        self.node = node
        self.keys = keys
        self.nobroadcast = nobroadcast
        self.calls = []

    @staticmethod
    def get_default_nodes():
        # return a predictable list for testing
        return ["http://default.node1", "http://default.node2"]

    def custom_json(self, **kwargs):
        # record call and return a dummy response
        self.calls.append(kwargs)
        return {"success": True, "kwargs": kwargs}


class DummyUnhandledRPCError(Exception):
    pass


class DummyMissingKeyError(Exception):
    pass


# patch the imported exceptions to our dummies inside the module so they can be raised
@pytest.fixture(autouse=True)
def patch_hive_and_exceptions(monkeypatch):
    monkeypatch.setattr(hive_actions, "Hive", DummyHive)
    monkeypatch.setattr(hive_actions, "UnhandledRPCError", DummyUnhandledRPCError)
    monkeypatch.setattr(hive_actions, "MissingKeyError", DummyMissingKeyError)
    yield


def test_convert_decimals_to_float_or_int_simple():
    # whole number -> int, fractional -> float
    assert hive_actions.convert_decimals_to_float_or_int(Decimal("10")) == 10
    assert hive_actions.convert_decimals_to_float_or_int(Decimal("3.14")) == pytest.approx(3.14)


def test_convert_decimals_nested():
    data = {
        "a": Decimal("2"),
        "b": [Decimal("1.0"), {"c": Decimal("5.5")}],
    }
    result = hive_actions.convert_decimals_to_float_or_int(data)
    assert result == {"a": 2, "b": [1.0, {"c": pytest.approx(5.5)}]}


def test_convert_with_decimal128():
    # Decimal128 is imported lazily to avoid dependency if not installed in environment
    try:
        from bson.decimal128 import Decimal128
    except ImportError:
        pytest.skip("bson.decimal128 not available")

    d128 = Decimal128("4.0")
    assert hive_actions.convert_decimals_to_float_or_int(d128) == 4.0


def test_get_hive_client_constructs_with_extra_nodes():
    # when we call get_hive_client, it should create DummyHive with combined nodes
    client = hive_actions.get_hive_client(keys=["key1"], nobroadcast=True)
    assert isinstance(client, DummyHive)
    # node list should include extra node followed by default nodes
    assert client.node[0] == "https://rpc.podping.org"
    assert "http://default.node1" in client.node
    assert client.keys == ["key1"]
    assert client.nobroadcast is True


@pytest.mark.asyncio
async def test_send_custom_json_validation_errors():
    # json_data not a dict
    with pytest.raises(ValueError):
        await hive_actions.send_custom_json("not a dict", send_account="foo")

    # empty dict
    with pytest.raises(ValueError):
        await hive_actions.send_custom_json({}, send_account="foo")

    # no hive_client or keys
    with pytest.raises(ValueError):
        await hive_actions.send_custom_json({"a": 1}, send_account="foo")


@pytest.mark.asyncio
async def test_send_custom_json_success_with_client():
    fake = DummyHive()
    result = await hive_actions.send_custom_json(
        json_data={"foo": Decimal("1")},
        send_account="alice",
        hive_client=fake,
        nobroadcast=True,
    )
    assert result["success"]
    # check that decimal got converted to int
    sent = fake.calls[0]["json_data"]
    assert sent == {"foo": 1}
    assert fake.calls[0]["id"] == "pp_startup"
    assert fake.calls[0]["required_posting_auths"] == ["alice"]


@pytest.mark.asyncio
async def test_send_custom_json_success_with_keys(monkeypatch):
    # override get_hive_client to return DummyHive instance we can inspect
    created = {}

    def factory(keys, nobroadcast=False):
        client = DummyHive(keys=keys, nobroadcast=nobroadcast)
        created["client"] = client
        return client

    monkeypatch.setattr(hive_actions, "get_hive_client", factory)

    result = await hive_actions.send_custom_json(
        json_data={"bar": Decimal("2.2")},
        send_account="bob",
        keys=["k1"],
    )
    assert result["success"]
    client = created["client"]
    assert client.keys == ["k1"]
    sent = client.calls[0]["json_data"]
    assert sent == {"bar": pytest.approx(2.2)}


@pytest.mark.asyncio
async def test_send_custom_json_unhandledrpcerror(monkeypatch):
    class ErrorHive(DummyHive):
        def custom_json(self, **kwargs):
            raise DummyUnhandledRPCError("rpc failure")

    hive = ErrorHive()
    with pytest.raises(hive_actions.CustomJsonSendError) as excinfo:
        await hive_actions.send_custom_json(
            json_data={"x": 1},
            send_account="carol",
            hive_client=hive,
        )
    assert "rpc failure" in str(excinfo.value)
    # extra info should include json_data and send_account
    assert excinfo.value.extra["send_account"] == "carol"


@pytest.mark.asyncio
async def test_send_custom_json_missingkeyerror():
    class ErrorHive(DummyHive):
        def custom_json(self, **kwargs):
            raise DummyMissingKeyError("bad key")

    hive = ErrorHive()
    with pytest.raises(hive_actions.CustomJsonSendError) as excinfo:
        await hive_actions.send_custom_json(
            json_data={"x": 1},
            send_account="dave",
            hive_client=hive,
        )
    assert "Wrong key" in str(excinfo.value)


@pytest.mark.asyncio
async def test_send_custom_json_generic_exception():
    class ErrorHive(DummyHive):
        def custom_json(self, **kwargs):
            raise RuntimeError("boom")

    hive = ErrorHive()
    with pytest.raises(hive_actions.CustomJsonSendError) as excinfo:
        await hive_actions.send_custom_json(
            json_data={"x": 1},
            send_account="ellen",
            hive_client=hive,
        )
    assert "boom" in str(excinfo.value)

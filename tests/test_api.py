import pytest
from fastapi.testclient import TestClient

from hivepinger.api import create_app


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test_api.db")
    app = create_app(db_path=db_path)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_root_success(client):
    response = client.get(
        "/",
        params={
            "url": "https://feeds.example.org/livestream/rss",
            "reason": "live",
            "medium": "music",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "queued"
    assert data["reason"] == "live"
    assert data["medium"] == "music"
    assert data["url"] == "https://feeds.example.org/livestream/rss"


def test_root_validation_error(client):
    response = client.get("/", params={"url": "", "reason": "bad", "medium": "nope"})
    assert response.status_code == 422


def test_root_url_must_be_valid(client):
    # passing a non-URL should trigger pydantic validation error
    response = client.get("/", params={"url": "not-a-url", "reason": "update", "medium": "music"})
    assert response.status_code == 422
    body = response.json()
    # ensure error refers to the url field and mentions 'url'
    assert any(err.get("loc") and err["loc"][1] == "url" for err in body["detail"])


def test_startup_event_sets_fail_state(monkeypatch, tmp_path):
    """The lifespan context should send the startup ping and record failures.

    We open the application's lifespan context to run the startup logic
    implemented in :func:`create_lifespan` and then inspect the resulting
    state attributes.
    """

    from hivepinger import api

    db_path = str(tmp_path / "test_api.db")

    # patch get_hive_client so we don’t try to parse the dummy key
    class DummyClient:
        def __init__(self):
            self.rpc = type("R", (), {"url": "http://node"})()

    monkeypatch.setattr(api, "get_hive_client", lambda *args, **kwargs: DummyClient())

    # pass dummy hive parameters; they aren't used by the failing stub
    app = api.create_app(
        db_path=db_path,
        hive_account_name="acct",
        hive_posting_key="key",
        no_broadcast=False,
        podping_prefix="pp",
    )

    async def fail_send(*args, **kwargs):
        raise api.CustomJsonSendError("simulated failure")

    monkeypatch.setattr(api, "send_custom_json", fail_send)

    # run through the lifespan to trigger startup actions
    async def _run():
        async with app.router.lifespan_context(app):
            pass

    import asyncio

    asyncio.run(_run())

    assert app.state.fail_state is True
    assert "simulated failure" in app.state.fail_reason


def test_health_priority_over_queue(tmp_path):
    """health() should return fail_reason even if the queue is missing or broken."""

    from hivepinger import api

    db_path = str(tmp_path / "test_api.db")
    app = api.create_app(db_path=db_path)

    # simulate startup completed but queue never set or later removed
    app.state.fail_state = True
    app.state.fail_reason = "something bad"
    # deliberately remove queue attribute
    if hasattr(app.state, "queue"):
        delattr(app.state, "queue")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body == {"detail": {"error": "something bad"}}


def test_health_handles_queue_error(tmp_path, monkeypatch):
    """If queue.count_pending raises, health returns queue inaccessible."""

    from hivepinger import api

    db_path = str(tmp_path / "test_api.db")

    # stub hive client to avoid Base58 errors during lifespan startup
    class DummyClient3:
        def __init__(self):
            self.rpc = type("R", (), {"url": "http://node"})()

    monkeypatch.setattr(api, "get_hive_client", lambda *args, **kwargs: DummyClient3())

    # prevent startup ping from failing when DummyClient3 lacks custom_json
    async def noop_send(*args, **kwargs):
        return {}

    monkeypatch.setattr(api, "send_custom_json", noop_send)
    app = api.create_app(db_path=db_path)

    # ensure queue is open normally
    async def do_nothing():
        pass

    # monkeypatch count_pending to raise
    async def fake_count():
        raise RuntimeError("db dead")

    # open lifespan to set queue
    async def open_and_patch():
        async with app.router.lifespan_context(app):
            app.state.queue.count_pending = fake_count

    import asyncio

    asyncio.run(open_and_patch())

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["detail"] == "Queue inaccessible"

import pytest
from fastapi.testclient import TestClient

from hivepinger.api import RATE_LIMIT_MAX, create_fast_api_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # stub out Hive interactions so the lifespan startup ping does not
    # attempt to parse a real posting key or contact the network.  The
    # tests themselves focus on API behaviour and queue logic, not the
    # Hive client, so we stub these globally for any test using the
    # ``client`` fixture.
    from hivepinger import api

    class DummyClient:
        def __init__(self):
            # only ``rpc.url`` is accessed by the startup code
            self.rpc = type("R", (), {"url": "http://node"})()

    monkeypatch.setattr(api, "get_hive_client", lambda *args, **kwargs: DummyClient())

    async def noop_send(*args, **kwargs):
        return {}

    monkeypatch.setattr(api, "send_custom_json", noop_send)

    db_path = str(tmp_path / "test_api.db")
    app = create_fast_api_app(db_path=db_path)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_root_success_enqueued_and_duplicate(client):
    response = client.get(
        "/",
        params={
            "url": "https://feeds.example.org/livestream/rss",
            "reason": "live",
            "medium": "music",
            "detailed_response": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "enqueued"
    assert data["reason"] == "live"
    assert data["medium"] == "music"
    assert data["url"] == "https://feeds.example.org/livestream/rss"

    response2 = client.get(
        "/",
        params={
            "url": "https://feeds.example.org/livestream/rss",
            "reason": "live",
            "medium": "music",
            "detailed_response": True,
        },
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["message"] == "duplicate"

    response3 = client.get(
        "/",
        params={
            "url": "https://feeds.example.org/livestream/rss",
            "reason": "live",
            "medium": "music",
        },
    )
    assert response2.status_code == 200
    assert response3.text == "Success!"


def test_rate_limit(client):
    """IP-based rate limiting should cap at 60 requests per minute.

    Because our shared ``client`` fixture enables ``raise_server_exceptions``
    we create a new TestClient for this test so that 429 responses are
    returned normally instead of being propagated as errors.
    """
    from fastapi.testclient import TestClient

    # recreate the app/fixture environment but disable exception raising
    app = client.app
    with TestClient(app, raise_server_exceptions=False) as c:
        headers = {"cf-connecting-ip": "1.2.3.4"}
        params = {
            "url": "https://feeds.example.org/livestream/rss",
            "reason": "live",
            "medium": "music",
        }

        # first 60 requests should pass
        for i in range(RATE_LIMIT_MAX):
            resp = c.get("/", params=params, headers=headers)
            assert resp.status_code == 200, f"failed on iteration {i}"

        # the 61st should be rate-limited
        resp = c.get("/", params=params, headers=headers)
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Rate limit exceeded"

        # a request from a different IP should still work
        headers2 = {"cf-connecting-ip": "5.6.7.8"}
        resp = c.get("/", params=params, headers=headers2)
        assert resp.status_code == 200


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
    app = api.create_fast_api_app(
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


def test_lifespan_sets_shutdown_event(tmp_path):
    from hivepinger import api

    db_path = str(tmp_path / "test_api.db")

    # stub hive client and send so startup ping succeeds
    class DummyClient:
        def __init__(self):
            self.rpc = type("R", (), {"url": "http://node"})()

    api.get_hive_client = lambda *args, **kwargs: DummyClient()  # type: ignore

    async def noop_send(*args, **kwargs):
        return {}

    api.send_custom_json = noop_send  # type: ignore

    app = api.create_fast_api_app(db_path=db_path)

    # the event is only attached when the lifespan context begins
    async def run_ctx():
        async with app.router.lifespan_context(app):
            assert hasattr(app.state, "shutdown_event")
            ev = app.state.shutdown_event
            assert not ev.is_set()
        # after exiting context the event should be set
        assert ev.is_set()

    import asyncio

    asyncio.run(run_ctx())

    # ensure event survives beyond context
    assert app.state.shutdown_event.is_set()


def test_health_priority_over_queue(tmp_path):
    """health() should report queue errors when the queue is missing or broken.

    The service records a failure reason but queue accessibility takes
    precedence; missing or broken queue results in a 503 with a simple
    "Queue inaccessible" message regardless of the fail_state.
    """

    from hivepinger import api

    db_path = str(tmp_path / "test_api.db")
    app = api.create_fast_api_app(db_path=db_path)

    # simulate startup completed but queue never set or later removed
    app.state.fail_state = True
    app.state.fail_reason = "something bad"
    # deliberately remove queue attribute
    if hasattr(app.state, "queue"):
        delattr(app.state, "queue")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["detail"] == "Queue inaccessible"


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
    app = api.create_fast_api_app(db_path=db_path)

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

import pytest
from fastapi.testclient import TestClient

from app.api import create_app


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

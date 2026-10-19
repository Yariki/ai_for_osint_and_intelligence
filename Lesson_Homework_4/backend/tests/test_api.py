import pytest
from conftest import entry, rss
from fastapi.testclient import TestClient

from rss_reader.db import session_scope
from rss_reader.ingestion import parse_feed, save_entries
from rss_reader.main import create_app
from rss_reader.models import Source


@pytest.fixture
def client(settings, engine):
    with TestClient(create_app(settings)) as client:
        yield client


def test_source_validation_duplicates_and_manual_job(client, source_id):
    assert client.get("/api/health").status_code == 200
    assert (
        client.post("/api/sources", json={"name": "Feed", "url": "file:///etc/passwd"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/sources", json={"name": "Feed", "url": "https://user:secret@example.com/feed"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/sources", json={"name": "Feed", "url": "https://example.com/feed"}
        ).status_code
        == 409
    )
    created = client.post(
        "/api/sources",
        json={"name": "New feed", "url": "https://example.org/feed", "period_minutes": 15},
    )
    assert created.status_code == 201 and created.json()["period_minutes"] == 15
    first = client.post(f"/api/sources/{source_id}/crawl")
    second = client.post(f"/api/sources/{source_id}/crawl")
    assert first.status_code == 202 and first.json()["id"] == second.json()["id"]
    assert client.get("/api/stats").json()["active_jobs"] == 1
    assert client.post("/api/sources/999/crawl").status_code == 404


def test_article_filtering_pagination_and_safe_description(client, engine, source_id):
    with session_scope(engine) as session:
        source = session.get(Source, source_id)
        save_entries(
            session,
            source,
            parse_feed(rss([entry("1", categories=["F1"]), entry("2", categories=["MotoGP"])])),
        )
    tags = client.get("/api/tags").json()
    f1 = next(tag["id"] for tag in tags if tag["name"] == "F1")
    result = client.get("/api/articles", params={"tag_ids": f1, "page_size": 1}).json()
    assert result["total"] == 1 and result["items"][0]["title"] == "Article 1"
    assert result["items"][0]["description"] == "A story & its context."
    assert result["items"][0]["source_ids"] == [source_id]
    assert (
        client.get("/api/articles", params={"page_size": 1, "page": 2}).json()["items"][0]["title"]
        == "Article 1"
    )
    assert client.get("/api/articles", params={"search": "Article 2"}).json()["total"] == 1
    assert client.get("/api/articles", params={"search": "%"}).json()["total"] == 0
    assert client.get("/api/articles", params={"source_id": 999}).json()["total"] == 0
    assert (
        client.get("/api/articles", params={"published_from": "2026-10-20T00:00:00Z"}).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.get("/api/articles", params={"published_from": "2026-10-20T00:00:00"}).status_code
        == 422
    )
    assert client.get("/api/articles", params={"page_size": 99999}).status_code == 422
    assert client.get("/api/stats").json()["articles"] == 2


def test_cross_origin_mutations_are_rejected(client, source_id):
    response = client.post(
        f"/api/sources/{source_id}/crawl", headers={"Origin": "https://malicious.example"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin"


def test_structured_validation_errors_do_not_echo_credentials(client):
    response = client.post(
        "/api/sources", json={"name": "", "url": "https://user:secret@example.com/feed"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation"
    assert "secret" not in response.text

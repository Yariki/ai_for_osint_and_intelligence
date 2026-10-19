from datetime import UTC, datetime
from html import escape

import pytest

from rss_reader.config import Settings
from rss_reader.db import create_db_engine, session_scope
from rss_reader.models import Base, Source
from rss_reader.network import FetchResult


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        request_jitter_seconds=0,
        request_delay_seconds=0.1,
    )


@pytest.fixture
def engine(settings):
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def source_id(engine):
    with session_scope(engine) as session:
        source = Source(name="Test feed", url="https://example.com/feed", period_minutes=60)
        session.add(source)
        session.flush()
        return source.id


def rss(items, *, headers=None, status=200):
    rows = []
    for item in items:
        row = "<item>"
        for key, value in item.items():
            values = value if isinstance(value, list) else [value]
            row += "".join(f"<{key}>{escape(str(v))}</{key}>" for v in values)
        rows.append(row + "</item>")
    body = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>Test</title><link>https://example.com</link><description>Feed</description>"
        + "".join(rows)
        + "</channel></rss>"
    )
    return FetchResult(status, body.encode(), headers or {}, "https://example.com/feed")


def entry(id="1", *, link=None, categories=None):
    return {
        "guid": id,
        "title": f"Article {id}",
        "link": link or f"https://example.com/news/{id}",
        "pubDate": "Mon, 19 Oct 2026 11:00:00 +0000",
        "description": "<p>A story &amp; its context.</p><script>unsafe()</script>",
        "category": categories or ["Formula 1"],
    }


def past():
    return datetime(2020, 1, 1, tzinfo=UTC)

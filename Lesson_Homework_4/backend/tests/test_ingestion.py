import pytest
from conftest import entry, rss
from sqlalchemy import func, select

from rss_reader.db import session_scope
from rss_reader.ingestion import canonical_url, parse_feed, save_entries
from rss_reader.models import Article, ArticleTag, Source, SourceEntry, Tag
from rss_reader.network import CrawlError, FetchResult


def test_repeated_snapshot_and_out_of_order_entries(engine, source_id):
    def save(items):
        with session_scope(engine) as session:
            return save_entries(session, session.get(Source, source_id), parse_feed(rss(items)))

    assert save([entry("2"), entry("1")]) == (2, 0)
    assert save([entry("2"), entry("1")]) == (0, 2)
    assert save([entry("3"), entry("1"), entry("older-late")]) == (2, 1)
    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 4


def test_cross_source_deduplication_and_tag_reuse(engine, source_id):
    with session_scope(engine) as session:
        other = Source(name="Other", url="https://example.org/feed")
        session.add(other)
        session.flush()
        first = entry(
            link="https://example.com/news/1?utm_source=f1&edition=uk", categories=[" Formula 1 "]
        )
        second = entry(
            link="https://example.com/news/1?utm_source=wec&edition=uk",
            categories=["formula 1", "WEC"],
        )
        assert save_entries(session, session.get(Source, source_id), parse_feed(rss([first]))) == (
            1,
            0,
        )
        assert save_entries(session, other, parse_feed(rss([second]))) == (0, 1)
        assert session.scalar(select(func.count()).select_from(Article)) == 1
        assert session.scalar(select(func.count()).select_from(Tag)) == 2
        assert session.scalar(select(func.count()).select_from(ArticleTag)) == 2
        assert session.scalar(select(func.count()).select_from(SourceEntry)) == 2


def test_same_guid_on_unrelated_sources_is_not_a_global_key(engine, source_id):
    with session_scope(engine) as session:
        other = Source(name="Other", url="https://other.example/feed")
        session.add(other)
        session.flush()
        save_entries(session, session.get(Source, source_id), parse_feed(rss([entry()])))
        assert save_entries(
            session, other, parse_feed(rss([entry(link="https://other.example/news/1")]))
        ) == (1, 0)


def test_bad_entry_is_isolated_and_missing_date_is_nullable():
    valid = entry()
    del valid["pubDate"]
    invalid = entry("bad")
    del invalid["title"]
    parsed = parse_feed(rss([invalid, valid]))
    assert (parsed.found, parsed.invalid, len(parsed.entries)) == (2, 1, 1)
    assert parsed.entries[0].published_at is None
    assert parsed.entries[0].description == "A story & its context."


def test_missing_guid_uses_canonical_link():
    item = entry()
    del item["guid"]
    parsed = parse_feed(rss([item]))
    assert parsed.entries[0].identity == "https://example.com/news/1"


def test_atom_and_relative_links():
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
    <entry><id>urn:story:1</id><title>Atom article</title><link href="/story" />
    <published>2026-10-19T11:00:00Z</published><summary>Text</summary>
    <category term="Racing" /></entry></feed>"""
    parsed = parse_feed(FetchResult(200, xml, {}, "https://example.com/feed"))
    assert parsed.entries[0].link == "https://example.com/story"
    assert parsed.entries[0].tags == [("Racing", "racing")]
    assert parsed.entries[0].published_at.isoformat() == "2026-10-19T11:00:00+00:00"


@pytest.mark.parametrize("body", [b"<rss><channel><item>", b"<html>not a feed</html>"])
def test_invalid_document_is_not_checkpointable(body):
    with pytest.raises(CrawlError, match="Invalid or incomplete"):
        parse_feed(FetchResult(200, body, {}, "https://example.com/feed"))


def test_canonicalization_preserves_functional_parameters():
    assert (
        canonical_url("https://EXAMPLE.com/news?id=4&utm_medium=rss#intro")
        == "https://example.com/news?id=4"
    )
    assert canonical_url("https://example.com/news?id=4") != canonical_url(
        "https://example.com/news?id=5"
    )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_entity_declarations_are_rejected_before_parsing(encoding):
    xml = '<!DOCTYPE rss [<!ENTITY story "expanded">]><rss version="2.0"><channel/></rss>'
    with pytest.raises(CrawlError, match="entity declarations"):
        parse_feed(FetchResult(200, xml.encode(encoding), {}, "https://example.com/feed"))

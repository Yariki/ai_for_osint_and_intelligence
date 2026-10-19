import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from rss_reader.models import Article, ArticleTag, Source, SourceEntry, Tag
from rss_reader.network import CrawlError, FetchResult, validate_url


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = PlainText()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def canonical_url(value: str) -> str:
    value = validate_url(value)
    parts = urlsplit(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid", "msclkid")
    ]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def normalize_tag(value: str) -> tuple[str, str]:
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()[:200]
    return name, name.casefold()


@dataclass
class ParsedEntry:
    identity: str
    title: str
    link: str
    canonical: str
    published_at: datetime | None
    description: str
    tags: list[tuple[str, str]]


@dataclass
class ParsedFeed:
    entries: list[ParsedEntry] = field(default_factory=list)
    found: int = 0
    invalid: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)


def parse_feed(result: FetchResult) -> ParsedFeed:
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)\b", result.body.replace(b"\x00", b""), re.IGNORECASE):
        raise CrawlError("parse", "XML document type and entity declarations are not supported")
    feed = feedparser.parse(
        result.body,
        response_headers={
            # Resolve article URLs below; a Content-Location here would also
            # rewrite opaque RSS GUIDs into URLs.
            "content-type": result.headers.get("content-type", "application/xml"),
        },
    )
    benign_warning = isinstance(
        feed.get("bozo_exception"),
        (feedparser.CharacterEncodingOverride, feedparser.NonXMLContentType),
    )
    if not feed.version or (feed.get("bozo") and not benign_warning):
        # Do not checkpoint a truncated document even if a permissive parser found items.
        raise CrawlError("parse", "Invalid or incomplete RSS/Atom document", retryable=True)
    parsed = ParsedFeed(found=len(feed.entries))
    for index, entry in enumerate(feed.entries):
        try:
            raw_link = str(entry.get("link", ""))
            if not raw_link:
                raise ValueError("Entry has no link")
            if not entry.get("links"):
                # feedparser can promote a permalink GUID to a link. An
                # opaque identifier alone must not invent an article URL.
                validate_url(raw_link)
            link = validate_url(urljoin(result.url, raw_link))
            canonical = canonical_url(link)
            title = plain_text(str(entry.get("title", "")))
            if not title:
                raise ValueError("Entry has no title")
            identity = str(entry.get("id") or canonical)
            published = entry.get("published_parsed")
            date = None
            if published:
                try:
                    date = datetime.fromtimestamp(calendar.timegm(published), UTC)
                except ValueError, OverflowError, OSError:
                    pass
            tags = [normalize_tag(str(tag.get("term", ""))) for tag in entry.get("tags", [])]
            parsed.entries.append(
                ParsedEntry(
                    identity,
                    title,
                    link,
                    canonical,
                    date,
                    plain_text(str(entry.get("summary", ""))),
                    [tag for tag in tags if tag[0]],
                )
            )
        except ValueError, TypeError:
            parsed.invalid += 1
            if len(parsed.errors) < 20:
                parsed.errors.append(
                    {
                        "code": "invalid_entry",
                        "message": f"Entry {index + 1} has invalid required fields",
                    }
                )
    return parsed


def save_entries(session: Session, source: Source, feed: ParsedFeed) -> tuple[int, int]:
    new = duplicate = 0
    for entry in feed.entries:
        if session.get(SourceEntry, (source.id, entry.identity)):
            duplicate += 1
            continue
        inserted = session.scalar(
            insert(Article)
            .values(
                title=entry.title,
                link=entry.link,
                canonical_url=entry.canonical,
                published_at=entry.published_at,
                description=entry.description,
            )
            .on_conflict_do_nothing(index_elements=["canonical_url"])
            .returning(Article.id)
        )
        article_id = inserted or session.scalar(
            select(Article.id).where(Article.canonical_url == entry.canonical)
        )
        assert article_id is not None
        new += int(inserted is not None)
        duplicate += int(inserted is None)
        session.add(
            SourceEntry(source_id=source.id, entry_id=entry.identity, article_id=article_id)
        )
        for name, normalized in entry.tags:
            session.execute(
                insert(Tag)
                .values(name=name, normalized_name=normalized)
                .on_conflict_do_nothing(index_elements=["normalized_name"])
            )
            tag_id = session.scalar(select(Tag.id).where(Tag.normalized_name == normalized))
            session.execute(
                insert(ArticleTag)
                .values(article_id=article_id, tag_id=tag_id)
                .on_conflict_do_nothing()
            )
        session.flush()
    return new, duplicate

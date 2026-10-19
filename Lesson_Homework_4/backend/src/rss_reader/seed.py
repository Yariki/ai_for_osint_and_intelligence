from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from rss_reader.config import Settings
from rss_reader.models import Source

DEFAULT_SOURCES = (
    ("Autosport · Formula 1", "https://www.autosport.com/rss/f1/news/"),
    ("Autosport · MotoGP", "https://www.autosport.com/rss/motogp/news/"),
    ("Autosport · National", "https://www.autosport.com/rss/national/news/"),
    ("Autosport · Sportscars", "https://www.autosport.com/rss/category/sportscars/news/"),
)


def seed_sources(session: Session, settings: Settings) -> None:
    for name, url in DEFAULT_SOURCES:
        session.execute(
            insert(Source)
            .values(name=name, url=url, period_minutes=settings.default_period_minutes)
            .on_conflict_do_nothing(index_elements=["url"])
        )

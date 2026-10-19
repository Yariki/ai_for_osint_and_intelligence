from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from rss_reader.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    path = make_url(settings.database_url).database
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False, "timeout": 15}
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        yield session

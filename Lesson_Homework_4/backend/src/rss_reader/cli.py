import argparse
import sqlite3
from pathlib import Path

from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command
from rss_reader.config import Settings
from rss_reader.db import create_db_engine, session_scope
from rss_reader.seed import seed_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="RSS reader database maintenance")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="Apply migrations and seed the four default sources")
    backup = sub.add_parser("backup", help="Create a consistent SQLite online backup")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()
    settings = Settings()
    if args.action == "init":
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_db_engine(settings)
        try:
            with session_scope(engine) as session:
                seed_sources(session, settings)
        finally:
            engine.dispose()
    else:
        database = make_url(settings.database_url).database
        assert database is not None
        destination = args.destination.resolve()
        if destination == Path(database).resolve() or destination.exists():
            parser.error("Backup destination must be a new file different from the database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
        print(f"Backup saved to {destination}")


if __name__ == "__main__":
    main()

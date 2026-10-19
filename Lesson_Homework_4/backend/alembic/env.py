from alembic import context
from rss_reader.config import Settings
from rss_reader.db import create_db_engine
from rss_reader.models import Base

config = context.config
settings = Settings()
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_db_engine(settings)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

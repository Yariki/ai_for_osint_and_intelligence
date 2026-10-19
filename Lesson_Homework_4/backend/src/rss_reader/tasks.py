import logging

from celery import Celery
from redis import Redis

from rss_reader.config import Settings
from rss_reader.db import create_db_engine
from rss_reader.jobs import dispatch_due, run_job

settings = Settings()
logging.getLogger("httpx").setLevel(logging.WARNING)
celery_app = Celery("rss_reader", broker=str(settings.redis_url))
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.worker_concurrency,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": settings.job_lease_seconds + 60},
    task_publish_retry=False,
    broker_connection_timeout=3,
    beat_schedule={
        "dispatch-due-sources": {
            "task": "rss_reader.dispatch",
            "schedule": settings.scheduler_interval_seconds,
            "options": {"expires": settings.scheduler_interval_seconds},
        }
    },
)


@celery_app.task(name="rss_reader.dispatch")
def dispatch() -> None:
    engine = create_db_engine(settings)
    try:
        dispatch_due(
            engine,
            settings,
            lambda job_id: celery_app.send_task("rss_reader.crawl", args=[job_id]),
        )
    finally:
        engine.dispose()


@celery_app.task(
    name="rss_reader.crawl",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=settings.job_lease_seconds - 15,
)
def crawl(job_id: int) -> None:
    engine = create_db_engine(settings)
    redis = Redis.from_url(str(settings.redis_url), socket_timeout=5, socket_connect_timeout=5)
    try:
        run_job(engine, settings, redis, job_id)
    finally:
        redis.close()
        engine.dispose()

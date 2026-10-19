import logging
import random
from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, exists, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError

from rss_reader.config import Settings
from rss_reader.db import session_scope
from rss_reader.ingestion import parse_feed, save_entries
from rss_reader.models import ACTIVE_STATUSES, Job, Source, utcnow
from rss_reader.network import CrawlError, Deferred, FeedHTTPClient

logger = logging.getLogger(__name__)


def request_job(engine: Engine, source_id: int, trigger: str = "manual") -> int:
    with session_scope(engine) as session:
        source = session.get(Source, source_id)
        if source is None:
            raise LookupError("Source not found")
        job_id = session.scalar(
            insert(Job)
            .values(source_id=source_id, trigger=trigger)
            .on_conflict_do_nothing()
            .returning(Job.id)
        )
        if job_id is None:
            job_id = session.scalar(
                select(Job.id).where(Job.source_id == source_id, Job.status.in_(ACTIVE_STATUSES))
            )
        else:
            source.next_run_at = utcnow() + timedelta(minutes=source.period_minutes)
        assert job_id is not None
        return job_id


def dispatch_due(engine: Engine, settings: Settings, publish: Callable[[int], None]) -> None:
    now = utcnow()
    with session_scope(engine) as session:
        stale = session.scalars(
            select(Job).where(Job.status == "running", Job.lease_until < now)
        ).all()
        for job in stale:
            job.status = "retrying" if job.attempts < settings.max_attempts else "failed"
            job.next_attempt_at = now
            job.finished_at = now if job.status == "failed" else None
            job.dispatched_at = None
            job.run_token = None
            job.lease_until = None
            job.errors = (
                job.errors + [{"code": "interrupted", "message": "Worker lease expired"}]
            )[-20:]
        session.flush()
        due_sources = session.scalars(
            select(Source).where(
                Source.enabled.is_(True),
                Source.next_run_at <= now,
                ~exists().where(Job.source_id == Source.id, Job.status.in_(ACTIVE_STATUSES)),
            )
        ).all()
        for source in due_sources:
            session.execute(
                insert(Job)
                .values(source_id=source.id, next_attempt_at=now)
                .on_conflict_do_nothing()
            )
            source.next_run_at = now + timedelta(minutes=source.period_minutes)
        session.flush()
        candidates = list(
            session.scalars(
                select(Job.id).where(
                    Job.status.in_(("queued", "retrying")),
                    Job.next_attempt_at <= now,
                    (Job.dispatched_at.is_(None))
                    | (
                        Job.dispatched_at < now - timedelta(seconds=settings.dispatch_lease_seconds)
                    ),
                )
            )
        )
    for job_id in candidates:
        # Reserve publication separately. If a process dies after this commit,
        # the dispatch lease expires and another tick safely republishes it.
        with session_scope(engine) as session:
            reserved = session.scalar(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status.in_(("queued", "retrying")),
                    Job.next_attempt_at <= now,
                    (Job.dispatched_at.is_(None))
                    | (
                        Job.dispatched_at < now - timedelta(seconds=settings.dispatch_lease_seconds)
                    ),
                )
                .values(dispatched_at=now)
                .returning(Job.id)
            )
        if reserved is None:
            continue
        try:
            publish(job_id)
        except Exception:
            # A broker failure leaves the durable job for the next scheduler tick.
            with session_scope(engine) as session:
                job = session.get(Job, job_id)
                if job and job.status in ("queued", "retrying"):
                    job.dispatched_at = None
                    job.errors = (
                        job.errors + [{"code": "dispatch", "message": "Broker publication failed"}]
                    )[-20:]
            logger.warning("Job %s could not be published; it remains queued", job_id)


def finish_error(
    engine: Engine, settings: Settings, job_id: int, token: str, error: CrawlError
) -> None:
    now = utcnow()
    with session_scope(engine) as session:
        # Acquire SQLite's write lock before reading the lease owner.
        owned = session.scalar(
            update(Job)
            .where(Job.id == job_id, Job.run_token == token, Job.status == "running")
            .values(lease_until=now)
            .returning(Job.id)
        )
        if owned is None:
            return
        job = session.get(Job, job_id)
        assert job is not None
        if isinstance(error, Deferred):
            job.attempts -= 1
            job.status = "retrying"
            delay = max(1, error.delay)
        else:
            job.errors = (job.errors + [{"code": error.code, "message": str(error)}])[-20:]
            job.http_status = error.status
            if error.code == "robots_blocked":
                job.status = "blocked"
            elif error.retryable and job.attempts < settings.max_attempts:
                job.status = "retrying"
            else:
                job.status = "failed"
            delay = max(
                error.delay,
                settings.retry_base_seconds * 2 ** max(0, job.attempts - 1)
                + random.uniform(0, settings.request_jitter_seconds),
            )
        job.next_attempt_at = now + timedelta(seconds=delay)
        job.dispatched_at = None
        job.lease_until = None
        job.run_token = None
        if job.status not in ACTIVE_STATUSES:
            job.finished_at = now
        source = session.get(Source, job.source_id)
        if source:
            source.last_checked_at = now
            # Keep the source schedule behind retries and publisher cooldowns.
            source.next_run_at = max(source.next_run_at, job.next_attempt_at)


def run_job(
    engine: Engine,
    settings: Settings,
    redis: Redis,
    job_id: int,
    client_factory: Callable[..., FeedHTTPClient] = FeedHTTPClient,
) -> None:
    now, token = utcnow(), str(uuid4())
    with session_scope(engine) as session:
        claimed = session.scalar(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status.in_(("queued", "retrying")),
                Job.next_attempt_at <= now,
            )
            .values(
                status="running",
                run_token=token,
                lease_until=now + timedelta(seconds=settings.job_lease_seconds),
                attempts=Job.attempts + 1,
                started_at=now,
            )
            .returning(Job.id)
        )
        if claimed is None:
            return
        job = session.get(Job, job_id)
        assert job is not None
        source = session.get(Source, job.source_id)
        assert source is not None
        source_id, source_url = source.id, source.url
        etag, last_modified = source.etag, source.last_modified
        enabled = source.enabled or job.trigger == "manual"
    client = None
    try:
        if not enabled:
            raise CrawlError("source_disabled", "Source was disabled before this job started")
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        client = client_factory(settings, redis)
        result = client.fetch(source_url, headers)
        if result.status == 304 and not headers:
            raise CrawlError("http", "Unexpected HTTP 304 without a conditional request")
        feed = parse_feed(result) if result.status == 200 else None
        with session_scope(engine) as session:
            owned = session.scalar(
                update(Job)
                .where(Job.id == job_id, Job.run_token == token, Job.status == "running")
                .values(lease_until=utcnow())
                .returning(Job.id)
            )
            if owned is None:
                return
            job = session.get(Job, job_id)
            source = session.get(Source, source_id)
            assert job is not None and source is not None
            if feed:
                job.new_count, job.duplicate_count = save_entries(session, source, feed)
                job.found_count, job.invalid_count = feed.found, feed.invalid
                job.errors = (job.errors + feed.errors)[-20:]
            job.status = "partial" if feed and feed.invalid else "successful"
            job.http_status = result.status
            job.finished_at = utcnow()
            job.run_token = None
            job.lease_until = None
            source.last_checked_at = job.finished_at
            if job.status == "successful":
                source.last_success_at = job.finished_at
                if result.status == 200:
                    # Cache validators and checkpoint must reflect a fully committed document.
                    source.etag = result.headers.get("etag")
                    source.last_modified = result.headers.get("last-modified")
                    if feed and feed.entries:
                        source.last_entry_id = feed.entries[0].identity
            elif feed:
                # A changed document with invalid entries must be fetched again.
                source.etag = source.last_modified = None
    except CrawlError as error:
        finish_error(engine, settings, job_id, token, error)
    except RedisError:
        finish_error(
            engine,
            settings,
            job_id,
            token,
            CrawlError("redis", "Request coordination is unavailable", retryable=True),
        )
    except SQLAlchemyError:
        # A database outage may also prevent writing a failure. Leave the lease
        # for recovery, and propagate a sanitized exception to Celery.
        raise RuntimeError("Database operation failed; job lease will be recovered") from None
    except Exception:
        logger.error("Job %s encountered an unexpected processing error", job_id)
        finish_error(
            engine,
            settings,
            job_id,
            token,
            CrawlError("internal", "Unexpected processing error", retryable=True),
        )
    finally:
        if client:
            client.close()

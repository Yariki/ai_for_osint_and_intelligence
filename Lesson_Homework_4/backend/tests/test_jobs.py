from datetime import timedelta
from unittest.mock import Mock

from conftest import entry, past, rss
from sqlalchemy import func, select

from rss_reader.db import session_scope
from rss_reader.jobs import dispatch_due, request_job, run_job
from rss_reader.models import Article, Job, Source, utcnow
from rss_reader.network import CrawlError, Deferred
from rss_reader.seed import seed_sources


def run(engine, settings, job_id, response=None, error=None):
    client = Mock()
    if error:
        client.fetch.side_effect = error
    else:
        client.fetch.return_value = response or rss([entry()], headers={"etag": '"v1"'})
    run_job(engine, settings, Mock(), job_id, lambda *_: client)
    return client


def make_due(engine, job_id):
    with session_scope(engine) as session:
        session.get(Job, job_id).next_attempt_at = past()


def test_checkpoint_conditional_fetch_and_idempotent_delivery(engine, settings, source_id):
    job_id = request_job(engine, source_id)
    client = run(engine, settings, job_id)
    client.fetch.assert_called_once_with("https://example.com/feed", {})
    with session_scope(engine) as session:
        assert session.get(Job, job_id).new_count == 1
        assert session.get(Source, source_id).last_entry_id == "1"
    duplicate_delivery = run(engine, settings, job_id)
    duplicate_delivery.fetch.assert_not_called()
    next_job = request_job(engine, source_id)
    client = run(engine, settings, next_job, rss([], status=304))
    client.fetch.assert_called_once_with("https://example.com/feed", {"If-None-Match": '"v1"'})
    with session_scope(engine) as session:
        assert session.get(Job, next_job).status == "successful"
        assert session.get(Job, next_job).http_status == 304
        assert session.scalar(select(func.count()).select_from(Article)) == 1


def test_partial_job_does_not_advance_checkpoint_or_cache_bad_feed(engine, settings, source_id):
    with session_scope(engine) as session:
        source = session.get(Source, source_id)
        source.last_entry_id, source.etag = "old", '"old"'
    bad = entry("broken")
    del bad["link"]
    job_id = request_job(engine, source_id)
    run(engine, settings, job_id, rss([bad, entry()], headers={"etag": '"bad"'}))
    with session_scope(engine) as session:
        job, source = session.get(Job, job_id), session.get(Source, source_id)
        assert (job.status, job.new_count, job.invalid_count) == ("partial", 1, 1)
        assert source.last_entry_id == "old"
        assert source.etag is None
    next_job = request_job(engine, source_id)
    run(engine, settings, next_job, rss([entry("broken"), entry()]))
    with session_scope(engine) as session:
        assert session.get(Job, next_job).new_count == 1
        assert session.get(Job, next_job).duplicate_count == 1
        assert session.get(Source, source_id).last_entry_id == "broken"


def test_failure_does_not_prevent_other_source_and_retries_are_bounded(engine, settings, source_id):
    first = request_job(engine, source_id)
    with session_scope(engine) as session:
        other = Source(name="Other", url="https://example.org/feed")
        session.add(other)
        session.flush()
        other_id = other.id
    second = request_job(engine, other_id)
    for _ in range(settings.max_attempts):
        make_due(engine, first)
        run(engine, settings, first, error=CrawlError("network", "Timeout", retryable=True))
    run(engine, settings, second)
    with session_scope(engine) as session:
        assert session.get(Job, first).status == "failed"
        assert session.get(Job, first).attempts == settings.max_attempts
        assert session.get(Job, second).status == "successful"
        assert session.get(Source, source_id).last_entry_id is None


def test_pacing_does_not_consume_retries_and_429_respects_delay(engine, settings, source_id):
    job_id = request_job(engine, source_id)
    run(engine, settings, job_id, error=Deferred(600))
    with session_scope(engine) as session:
        job = session.get(Job, job_id)
        assert job.status == "retrying" and job.attempts == 0 and not job.errors
        assert job.next_attempt_at > utcnow() + timedelta(seconds=590)
    make_due(engine, job_id)
    run(
        engine,
        settings,
        job_id,
        error=CrawlError("http_transient", "HTTP 429", status=429, retryable=True, delay=900),
    )
    with session_scope(engine) as session:
        job = session.get(Job, job_id)
        assert job.attempts == 1 and job.http_status == 429
        assert job.next_attempt_at > utcnow() + timedelta(seconds=890)


def test_scheduler_reserves_one_active_job_and_recovers_publication(engine, settings, source_id):
    published = []
    dispatch_due(engine, settings, published.append)
    dispatch_due(engine, settings, published.append)
    assert len(published) == 1
    assert request_job(engine, source_id) == published[0]
    with session_scope(engine) as session:
        job = session.get(Job, published[0])
        job.dispatched_at = past()
    failed = Mock(side_effect=ConnectionError("broker unavailable"))
    dispatch_due(engine, settings, failed)
    failed.assert_called_once()
    dispatch_due(engine, settings, published.append)
    assert published[0] == published[1]


def test_scheduler_recovers_expired_worker_lease(engine, settings, source_id):
    job_id = request_job(engine, source_id)
    with session_scope(engine) as session:
        job = session.get(Job, job_id)
        job.status, job.run_token, job.attempts = "running", "old-worker", 1
        job.lease_until = past()
    published = []
    dispatch_due(engine, settings, published.append)
    assert published == [job_id]
    run(engine, settings, job_id)
    with session_scope(engine) as session:
        job = session.get(Job, job_id)
        assert job.status == "successful" and job.attempts == 2
        assert job.errors[0]["code"] == "interrupted"


def test_robots_denial_is_visible_and_not_retried(engine, settings, source_id):
    job_id = request_job(engine, source_id)
    run(engine, settings, job_id, error=CrawlError("robots_blocked", "Disallowed by robots.txt"))
    with session_scope(engine) as session:
        assert session.get(Job, job_id).status == "blocked"


def test_seeding_is_idempotent_and_preserves_user_changes(engine, settings):
    with session_scope(engine) as session:
        seed_sources(session, settings)
        first = session.scalar(select(Source).order_by(Source.id))
        first.name, first.enabled, first.period_minutes = "My feed", False, 15
    with session_scope(engine) as session:
        seed_sources(session, settings)
        assert session.scalar(select(func.count()).select_from(Source)) == 4
        first = session.scalar(select(Source).order_by(Source.id))
        assert (first.name, first.enabled, first.period_minutes) == ("My feed", False, 15)


def test_concurrent_deliveries_claim_a_job_only_once(engine, settings, source_id):
    from concurrent.futures import ThreadPoolExecutor

    job_id = request_job(engine, source_id)
    client = Mock()
    client.fetch.return_value = rss([entry()])
    factory = Mock(return_value=client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(run_job, engine, settings, Mock(), job_id, factory) for _ in range(2)
        ]
        for result in results:
            result.result()
    factory.assert_called_once()
    with session_scope(engine) as session:
        assert session.get(Job, job_id).status == "successful"
        assert session.scalar(select(func.count()).select_from(Article)) == 1

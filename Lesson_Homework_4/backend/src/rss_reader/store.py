from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from rss_reader.models import (
    ACTIVE_STATUSES,
    Article,
    ArticleTag,
    Job,
    Source,
    SourceEntry,
    Tag,
    utcnow,
)
from rss_reader.schemas import (
    ArticleOut,
    JobOut,
    JobStatus,
    Page,
    SourceCreate,
    SourceUpdate,
    Stats,
    TagOut,
)


def check_database(session: Session) -> None:
    session.execute(text("SELECT id FROM sources LIMIT 1"))


def list_sources(session: Session) -> Sequence[Source]:
    return session.scalars(select(Source).order_by(Source.id)).all()


def add_source(session: Session, payload: SourceCreate, default_period: int) -> Source:
    source = Source(**payload.model_dump())
    if "period_minutes" not in payload.model_fields_set:
        source.period_minutes = default_period
    session.add(source)
    session.commit()
    return source


def update_source(session: Session, source_id: int, payload: SourceUpdate) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise LookupError("Source not found")
    changed_period = source.period_minutes != payload.period_minutes
    reenabled = payload.enabled and not source.enabled
    source.name, source.period_minutes, source.enabled = (
        payload.name,
        payload.period_minutes,
        payload.enabled,
    )
    if reenabled:
        source.next_run_at = utcnow()
    elif changed_period:
        source.next_run_at = utcnow() + timedelta(minutes=source.period_minutes)
    session.commit()
    return source


def get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError("Job not found")
    return job


def list_jobs(
    session: Session, source_id: int | None, status: JobStatus | None, page: int, page_size: int
) -> Page[JobOut]:
    query = select(Job)
    if source_id is not None:
        query = query.where(Job.source_id == source_id)
    if status:
        query = query.where(Job.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(Job.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[JobOut](
        items=[JobOut.model_validate(job) for job in items],
        total=total,
        page=page,
        page_size=page_size,
    )


def list_tags(session: Session) -> Sequence[Tag]:
    return session.scalars(select(Tag).order_by(Tag.normalized_name)).all()


def list_articles(
    session: Session,
    *,
    tag_ids: list[int] | None,
    source_id: int | None,
    search: str,
    published_from: datetime | None,
    published_to: datetime | None,
    page: int,
    page_size: int,
) -> Page[ArticleOut]:
    query = select(Article)
    if tag_ids:
        query = query.where(
            exists().where(ArticleTag.article_id == Article.id, ArticleTag.tag_id.in_(tag_ids))
        )
    if source_id is not None:
        query = query.where(
            exists().where(SourceEntry.article_id == Article.id, SourceEntry.source_id == source_id)
        )
    if search:
        query = query.where(Article.title.icontains(search, autoescape=True))
    if published_from:
        query = query.where(Article.published_at >= published_from)
    if published_to:
        query = query.where(Article.published_at <= published_to)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    ids = [article.id for article in rows]
    tags_by_article: dict[int, list[TagOut]] = {id_: [] for id_ in ids}
    sources_by_article: dict[int, set[int]] = {id_: set() for id_ in ids}
    if ids:
        for article_id, tag in session.execute(
            select(ArticleTag.article_id, Tag)
            .join(Tag)
            .where(ArticleTag.article_id.in_(ids))
            .order_by(Tag.normalized_name)
        ):
            tags_by_article[article_id].append(TagOut.model_validate(tag))
        for article_id, source in session.execute(
            select(SourceEntry.article_id, SourceEntry.source_id)
            .where(SourceEntry.article_id.in_(ids))
            .distinct()
        ):
            sources_by_article[article_id].add(source)
    return Page[ArticleOut](
        items=[
            ArticleOut(
                id=article.id,
                title=article.title,
                link=article.link,
                published_at=article.published_at,
                discovered_at=article.discovered_at,
                description=article.description,
                tags=tags_by_article[article.id],
                source_ids=sorted(sources_by_article[article.id]),
            )
            for article in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def statistics(session: Session) -> Stats:
    return Stats(
        sources=session.scalar(select(func.count()).select_from(Source)) or 0,
        enabled_sources=session.scalar(
            select(func.count()).select_from(Source).where(Source.enabled.is_(True))
        )
        or 0,
        jobs=session.scalar(select(func.count()).select_from(Job)) or 0,
        active_jobs=session.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE_STATUSES))
        )
        or 0,
        articles=session.scalar(select(func.count()).select_from(Article)) or 0,
        error_jobs=session.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.status.in_(("failed", "partial", "blocked"))
                | (func.json_array_length(Job.errors) > 0)
            )
        )
        or 0,
        invalid_entries=session.scalar(select(func.coalesce(func.sum(Job.invalid_count), 0))) or 0,
    )

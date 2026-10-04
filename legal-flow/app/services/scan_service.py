import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Discovery, NewsFlow, NewsItem, ScanJob, SourcePolicy, SourceSite, Story
from app.models.enums import NewsStatus, ScanJobStatus
from app.providers.base import CandidateItem
from app.providers.fixture_source import FixtureSourceProvider
from app.providers.html_source import SUPPORTED_DOMAINS as HTML_SUPPORTED_DOMAINS, HtmlSourceProvider
from app.services.activity_service import log_activity

PROVIDERS = {"fixture": FixtureSourceProvider(), "html": HtmlSourceProvider()}

MOSCOW_TZ = timezone(timedelta(hours=3))


async def provider_name_for_flow(db: AsyncSession, flow_id: uuid.UUID) -> str:
    """"html" (реальный сбор) только если ВСЕ активные домены потока поддержаны
    HtmlSourceProvider; иначе "fixture" — чтобы не пытаться скрести произвольные сайты."""
    domains = (
        await db.scalars(select(SourceSite.domain).where(SourceSite.flow_id == flow_id, SourceSite.active.is_(True)))
    ).all()
    if domains and all(d in HTML_SUPPORTED_DOMAINS for d in domains):
        return "html"
    return "fixture"


async def get_or_create_scan_job(
    db: AsyncSession, *, flow_id: uuid.UUID, idempotency_key: str, provider_name: str
) -> ScanJob:
    """Shared by the manual 'Приступить к сбору' endpoint and the schedule-based
    auto-run below — same idempotency-key dedupe either way (ТЗ §9)."""
    existing = await db.scalar(
        select(ScanJob).where(ScanJob.flow_id == flow_id, ScanJob.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    job = ScanJob(flow_id=flow_id, idempotency_key=idempotency_key, status=ScanJobStatus.pending, provider_name=provider_name)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def _is_schedule_due(schedule_period: str, schedule_time: str, last_scan_at: datetime | None, now_moscow: datetime) -> bool:
    if schedule_period == "Вручную":
        return False
    try:
        due_hour, due_minute = (int(p) for p in schedule_time.split(":"))
    except ValueError:
        return False
    if now_moscow.time() < time(due_hour, due_minute):
        return False
    if schedule_period == "По рабочим дням" and now_moscow.weekday() >= 5:
        return False
    if last_scan_at is None:
        return True
    last_scan_moscow = last_scan_at.astimezone(MOSCOW_TZ)
    if schedule_period == "Раз в неделю":
        return (now_moscow.date() - last_scan_moscow.date()).days >= 7
    return now_moscow.date() > last_scan_moscow.date()


async def enqueue_due_scheduled_scans(db: AsyncSession) -> list[uuid.UUID]:
    """Called periodically by the worker (ТЗ: сбор день в день / с периодичностью,
    без нажатия кнопки). For each flow whose schedule is due, enqueues one scan job
    (reusing the pending one if a previous check already created it today) and
    advances last_scan_at so the same day never fires twice."""
    now_moscow = datetime.now(MOSCOW_TZ)
    flows = (await db.scalars(select(NewsFlow).where(NewsFlow.schedule_period != "Вручную"))).all()
    created_job_ids: list[uuid.UUID] = []
    for flow in flows:
        if not _is_schedule_due(flow.schedule_period, flow.schedule_time, flow.last_scan_at, now_moscow):
            continue
        key = f"auto-schedule-{now_moscow.date().isoformat()}"
        provider_name = await provider_name_for_flow(db, flow.id)
        job = await get_or_create_scan_job(db, flow_id=flow.id, idempotency_key=key, provider_name=provider_name)
        flow.last_scan_at = now_moscow
        await db.commit()
        created_job_ids.append(job.id)
    return created_job_ids


async def _policy_cleared_domains(db: AsyncSession, domains: list[str]) -> tuple[list[str], list[str]]:
    """Splits domains into those where 'fetch' is allowed vs. those needing review.
    One allowed action never implies another (ТЗ §3): discover alone is not enough."""
    if not domains:
        return [], []
    rows = (await db.scalars(select(SourcePolicy).where(SourcePolicy.domain.in_(domains)))).all()
    by_domain = {r.domain: r for r in rows}
    cleared, blocked = [], []
    for d in domains:
        policy = by_domain.get(d)
        if policy is not None and "fetch" in policy.allowed_actions and "extract_facts" in policy.allowed_actions:
            cleared.append(d)
        else:
            blocked.append(d)
    return cleared, blocked


async def _is_duplicate(db: AsyncSession, *, workspace_id: uuid.UUID, item: CandidateItem) -> Discovery | None:
    match_conditions = [Discovery.normalized_url == item.normalized_url, Discovery.story_key == item.story_key]
    if item.content_hash:
        match_conditions.append(Discovery.content_hash == item.content_hash)
    stmt = select(Discovery).where(
        Discovery.workspace_id == workspace_id,
        Discovery.is_duplicate.is_(False),
        or_(*match_conditions),
    )
    return await db.scalar(stmt.limit(1))


async def process_scan_job(db: AsyncSession, job_id: uuid.UUID) -> ScanJob:
    """Idempotent: a job already 'done' or 'failed' is returned as-is, never reprocessed
    (ТЗ §9 — 'повторное выполнение задания не должно порождать новые редакционные
    карточки и затраты на модель'). This is what app/worker/runner.py calls for each
    queued job id; tests call it directly to exercise the same code path."""
    job = await db.get(ScanJob, job_id)
    if job is None:
        raise ValueError(f"scan job {job_id} not found")
    if job.status != ScanJobStatus.pending:
        return job

    job.status = ScanJobStatus.running
    await db.commit()

    flow = await db.get(NewsFlow, job.flow_id)
    sites = (
        await db.scalars(select(SourceSite).where(SourceSite.flow_id == flow.id, SourceSite.active.is_(True)))
    ).all()
    all_domains = [s.domain for s in sites]
    cleared_domains, blocked_domains = await _policy_cleared_domains(db, all_domains)

    for domain in blocked_domains:
        await log_activity(
            db,
            workspace_id=flow.workspace_id,
            action="source_policy_review_required",
            details={"domain": domain, "flow_id": str(flow.id), "reason": "SOURCE_POLICY_REVIEW"},
        )

    created_ids: list[str] = []
    if cleared_domains:
        provider = PROVIDERS[job.provider_name]
        candidates = await provider.discover(domains=cleared_domains, theme=flow.theme, limit=flow.news_limit_per_run)

        for item in candidates:
            existing = await _is_duplicate(db, workspace_id=flow.workspace_id, item=item)
            is_dup = existing is not None

            discovery = Discovery(
                workspace_id=flow.workspace_id,
                scan_job_id=job.id,
                normalized_url=item.normalized_url,
                discovery_domain=item.discovery_domain,
                content_hash=item.content_hash,
                story_key=item.story_key,
                is_duplicate=is_dup,
                duplicate_of_news_id=None,
                decision_reason="duplicate_of_existing_discovery" if is_dup else "new_story",
                metadata_json={"label": item.label},
            )
            db.add(discovery)
            await db.flush()

            if is_dup:
                continue

            story = await db.scalar(select(Story).where(Story.workspace_id == flow.workspace_id, Story.story_key == item.story_key))
            if story is None:
                story = Story(workspace_id=flow.workspace_id, story_key=item.story_key)
                db.add(story)
                await db.flush()

            news = NewsItem(
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                story_id=story.id,
                title=item.title,
                text="",
                status=NewsStatus.DISCOVERED,
                discovery_domain=item.discovery_domain,
                discovery_original_fragment=item.original_fragment,
                discovery_id=discovery.id,
            )
            db.add(news)
            await db.flush()
            created_ids.append(str(news.id))

    job.status = ScanJobStatus.done
    job.created_news_ids = created_ids
    job.finished_at = datetime.now(timezone.utc)
    await db.commit()

    await log_activity(
        db,
        workspace_id=flow.workspace_id,
        action="scan_job_completed",
        details={"flow_id": str(flow.id), "job_id": str(job.id), "created_news_count": len(created_ids)},
    )
    return job

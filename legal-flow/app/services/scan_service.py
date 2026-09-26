import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Discovery, NewsFlow, NewsItem, ScanJob, SourcePolicy, SourceSite, Story
from app.models.enums import NewsStatus, ScanJobStatus
from app.providers.base import CandidateItem
from app.providers.fixture_source import FixtureSourceProvider
from app.services.activity_service import log_activity

PROVIDERS = {"fixture": FixtureSourceProvider()}


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

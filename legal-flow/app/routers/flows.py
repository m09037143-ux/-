import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership, require_active_access, require_csrf, require_role
from app.errors import AppError
from app.models import Discovery, Membership, NewsFlow, ScanJob, SourceSite
from app.models.enums import Role
from app.providers.html_source import SUPPORTED_DOMAINS as HTML_SUPPORTED_DOMAINS
from app.schemas.flows import (
    AddSourceRequest,
    CreateFlowRequest,
    FlowOut,
    ScanJobOut,
    SourceSiteOut,
    UpdateFlowRequest,
    UpdateSourceRequest,
)
from app.services import flow_service, scan_service
from app.services.rate_limit import enforce_rate_limit
from app.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["flows"])

_EDIT_ROLES = (Role.workspace_owner, Role.editor)


async def _duplicate_count(db: AsyncSession, job_id: uuid.UUID) -> int:
    return await db.scalar(
        select(func.count()).select_from(Discovery).where(
            Discovery.scan_job_id == job_id, Discovery.is_duplicate.is_(True)
        )
    )


async def _get_owned_flow(db: AsyncSession, membership: Membership, flow_id: uuid.UUID) -> NewsFlow:
    flow = await db.get(NewsFlow, flow_id)
    if flow is None or flow.workspace_id != membership.workspace_id:
        raise AppError("VALIDATION_ERROR", "Поток не найден.")
    return flow


def _flow_out(flow: NewsFlow, sources: list[SourceSite]) -> FlowOut:
    # Тот же критерий, что и scan_service.provider_name_for_flow — держать в одном
    # месте нельзя, та функция асинхронная и берёт домены из БД по flow_id, а не из
    # уже загруженного списка sources; при изменении правила менять оба места.
    active_domains = [s.domain for s in sources if s.active]
    real_collection_enabled = (
        not get_settings().source_fixture_mode
        and bool(active_domains)
        and all(d in HTML_SUPPORTED_DOMAINS for d in active_domains)
    )
    return FlowOut(
        id=flow.id,
        name=flow.name,
        theme=flow.theme,
        schedule_period=flow.schedule_period,
        schedule_time=flow.schedule_time,
        news_limit_per_run=flow.news_limit_per_run,
        sources=[
            SourceSiteOut(id=s.id, domain=s.domain, active=s.active, html_supported=s.domain in HTML_SUPPORTED_DOMAINS)
            for s in sources
        ],
        real_collection_enabled=real_collection_enabled,
    )


@router.get("/flows", response_model=list[FlowOut])
async def list_flows(membership: Membership = Depends(get_current_membership), db: AsyncSession = Depends(get_db)):
    flows = (await db.scalars(select(NewsFlow).where(NewsFlow.workspace_id == membership.workspace_id))).all()
    out = []
    for f in flows:
        sources = (await db.scalars(select(SourceSite).where(SourceSite.flow_id == f.id))).all()
        out.append(_flow_out(f, sources))
    return out


@router.post("/flows", response_model=FlowOut, status_code=201, dependencies=[Depends(require_csrf)])
async def create_flow(
    payload: CreateFlowRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    flow = await flow_service.create_flow(
        db,
        workspace_id=membership.workspace_id,
        name=payload.name,
        theme=payload.theme,
        schedule_period=payload.schedule_period,
        schedule_time=payload.schedule_time,
        news_limit_per_run=payload.news_limit_per_run,
        domains=payload.domains,
    )
    sources = (await db.scalars(select(SourceSite).where(SourceSite.flow_id == flow.id))).all()
    return _flow_out(flow, sources)


@router.patch("/flows/{flow_id}", response_model=FlowOut, dependencies=[Depends(require_csrf)])
async def update_flow(
    flow_id: uuid.UUID,
    payload: UpdateFlowRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    flow = await _get_owned_flow(db, membership, flow_id)
    for field_name in ("name", "theme", "schedule_period", "schedule_time", "news_limit_per_run"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(flow, field_name, value)
    await db.commit()
    sources = (await db.scalars(select(SourceSite).where(SourceSite.flow_id == flow.id))).all()
    return _flow_out(flow, sources)


@router.post("/flows/{flow_id}/sources", response_model=SourceSiteOut, status_code=201, dependencies=[Depends(require_csrf)])
async def add_source(
    flow_id: uuid.UUID,
    payload: AddSourceRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    flow = await _get_owned_flow(db, membership, flow_id)
    site = await flow_service.add_source(db, flow=flow, workspace_id=membership.workspace_id, domain=payload.domain)
    return SourceSiteOut(id=site.id, domain=site.domain, active=site.active, html_supported=site.domain in HTML_SUPPORTED_DOMAINS)


@router.patch("/flows/{flow_id}/sources/{source_id}", response_model=SourceSiteOut, dependencies=[Depends(require_csrf)])
async def update_source(
    flow_id: uuid.UUID,
    source_id: uuid.UUID,
    payload: UpdateSourceRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    flow = await _get_owned_flow(db, membership, flow_id)
    site = await db.get(SourceSite, source_id)
    if site is None or site.flow_id != flow.id:
        raise AppError("VALIDATION_ERROR", "Источник не найден.")
    if payload.domain is not None:
        site.domain = flow_service.validate_domain(payload.domain)
    if payload.active is not None:
        site.active = payload.active
    await db.commit()
    return SourceSiteOut(id=site.id, domain=site.domain, active=site.active, html_supported=site.domain in HTML_SUPPORTED_DOMAINS)


@router.delete("/flows/{flow_id}/sources/{source_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_source(
    flow_id: uuid.UUID,
    source_id: uuid.UUID,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    flow = await _get_owned_flow(db, membership, flow_id)
    site = await db.get(SourceSite, source_id)
    if site is None or site.flow_id != flow.id:
        raise AppError("VALIDATION_ERROR", "Источник не найден.")
    await db.delete(site)
    await db.commit()


@router.post("/flows/{flow_id}/scan-jobs", response_model=ScanJobOut, status_code=202, dependencies=[Depends(require_csrf)])
async def create_scan_job(
    flow_id: uuid.UUID,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    settings = get_settings()
    await enforce_rate_limit("scan_job", str(membership.workspace_id), settings.rate_limit_scan_jobs_per_hour, 3600)

    flow = await _get_owned_flow(db, membership, flow_id)
    key = idempotency_key or f"auto-{uuid.uuid4()}"

    provider_name = await scan_service.provider_name_for_flow(db, flow.id)
    job = await scan_service.get_or_create_scan_job(
        db, flow_id=flow.id, idempotency_key=key, provider_name=provider_name
    )
    # Manual run also counts as "checked now" — the schedule-based auto-run (see
    # scan_service.enqueue_due_scheduled_scans) skips the same day so it doesn't
    # collect twice right after a manual click.
    flow.last_scan_at = datetime.now(timezone.utc)
    await db.commit()

    return ScanJobOut(
        id=job.id,
        flow_id=job.flow_id,
        status=job.status.value,
        provider_name=job.provider_name,
        created_news_ids=job.created_news_ids,
        duplicate_count=await _duplicate_count(db, job.id),
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


@router.get("/scan-jobs/{job_id}", response_model=ScanJobOut)
async def get_scan_job(
    job_id: uuid.UUID,
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(ScanJob, job_id)
    if job is None:
        raise AppError("VALIDATION_ERROR", "Задача не найдена.")
    flow = await db.get(NewsFlow, job.flow_id)
    if flow is None or flow.workspace_id != membership.workspace_id:
        raise AppError("FORBIDDEN", "Нет доступа к этой задаче.")
    return ScanJobOut(
        id=job.id,
        flow_id=job.flow_id,
        status=job.status.value,
        provider_name=job.provider_name,
        created_news_ids=job.created_news_ids,
        duplicate_count=await _duplicate_count(db, job.id),
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )

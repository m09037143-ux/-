import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership, get_current_user, require_active_access, require_csrf, require_role
from app.errors import AppError
from app.models import Membership, NewsItem, OfficialDocument, User
from app.config import get_settings
from app.models.enums import NewsStatus, Role
from app.schemas.news import (
    NewsItemOut,
    NewsListItemOut,
    PublicNewsOut,
    OfficialDocumentOut,
    RejectRequest,
    SourceViewOut,
    ReviewRequest,
    UpdateDraftRequest,
    UpdateFactsRequest,
    UpdateOfficialDocumentRequest,
)
from app.services import ai_service, news_service
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["news"])
_EDIT_ROLES = (Role.workspace_owner, Role.editor)


@router.get("/news", response_model=list[NewsListItemOut])
async def list_news(
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
    source_domain: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(NewsItem).where(NewsItem.workspace_id == membership.workspace_id)
    if status_filter:
        try:
            stmt = stmt.where(NewsItem.status == NewsStatus(status_filter))
        except ValueError:
            raise AppError("VALIDATION_ERROR", f"Некорректный статус: {status_filter!r}")
    if source_domain:
        stmt = stmt.where(NewsItem.discovery_domain == source_domain)
    if q:
        stmt = stmt.where(NewsItem.title.ilike(f"%{q}%"))
    stmt = stmt.order_by(NewsItem.created_at.desc()).limit(limit).offset(offset)

    items = (await db.scalars(stmt)).all()
    out = []
    for n in items:
        has_doc = (await db.scalar(select(OfficialDocument.id).where(OfficialDocument.news_item_id == n.id))) is not None
        out.append(
            NewsListItemOut(
                id=n.id, title=n.title, discovery_domain=n.discovery_domain,
                status=n.status.value, has_official_document=has_doc, created_at=n.created_at,
            )
        )
    return out


@router.get("/news/{news_id}", response_model=NewsItemOut)
async def get_news(
    news_id: uuid.UUID,
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    return await news_service.to_internal_out(db, news)


@router.get("/news/{news_id}/source", response_model=SourceViewOut)
async def get_news_source(
    news_id: uuid.UUID,
    full: bool = Query(default=False, description="true — загрузить текст оригинала с сайта (не сохраняется)"),
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    if full:
        await enforce_rate_limit(
            "news_source", str(membership.workspace_id), get_settings().rate_limit_news_source_per_hour, 3600
        )
    return await ai_service.get_source_view(db, news, load_text=full)


@router.patch("/news/{news_id}/draft", response_model=NewsItemOut, dependencies=[Depends(require_csrf)])
async def update_draft(
    news_id: uuid.UUID,
    payload: UpdateDraftRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    news = await news_service.update_draft(
        db, news=news, expected_version=payload.expected_version, title=payload.title, text=payload.text, user_id=user.id
    )
    return await news_service.to_internal_out(db, news)


@router.put("/news/{news_id}/official-document", response_model=NewsItemOut, dependencies=[Depends(require_csrf)])
async def put_official_document(
    news_id: uuid.UUID,
    payload: UpdateOfficialDocumentRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    news = await news_service.update_official_document(
        db, news=news, expected_version=payload.expected_version, title=payload.title, url=payload.url,
        requisites=payload.requisites, currency_notes=payload.currency_notes, confirmation_limits=payload.confirmation_limits,
    )
    return await news_service.to_internal_out(db, news)


@router.put("/news/{news_id}/facts", response_model=NewsItemOut, dependencies=[Depends(require_csrf)])
async def put_facts(
    news_id: uuid.UUID,
    payload: UpdateFactsRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    news = await news_service.update_facts(
        db, news=news, expected_version=payload.expected_version, text=payload.text, overall_status=payload.overall_status
    )
    return await news_service.to_internal_out(db, news)


@router.post("/news/{news_id}/review", response_model=NewsItemOut, dependencies=[Depends(require_csrf)])
async def post_review(
    news_id: uuid.UUID,
    payload: ReviewRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    news = await news_service.submit_review(
        db, news=news, reviewer_id=user.id, official_reviewed=payload.official_reviewed,
        facts_reviewed=payload.facts_reviewed, note=payload.note,
    )
    return await news_service.to_internal_out(db, news)


@router.post("/news/{news_id}/approve", response_model=NewsItemOut, dependencies=[Depends(require_csrf)])
async def post_approve(
    news_id: uuid.UUID,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    news = await news_service.approve(db, news=news)
    return await news_service.to_internal_out(db, news)


@router.post("/news/{news_id}/publish", response_model=NewsItemOut, dependencies=[Depends(require_csrf)])
async def post_publish(
    news_id: uuid.UUID,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    news = await news_service.publish(db, news=news)
    return await news_service.to_internal_out(db, news)


@router.post("/news/{news_id}/reject", response_model=NewsItemOut, dependencies=[Depends(require_csrf)])
async def post_reject(
    news_id: uuid.UUID,
    payload: RejectRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    news = await news_service.reject(db, news=news, reason=payload.reason)
    return await news_service.to_internal_out(db, news)


@router.get("/news/{news_id}/public-preview", response_model=PublicNewsOut)
async def get_public_preview(
    news_id: uuid.UUID,
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    doc = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == news.id))
    return PublicNewsOut(
        id=news.id,
        title=news.title,
        text=news.text,
        release_mode=news.release_mode.value,
        official_document=OfficialDocumentOut(
            title=doc.title, url=doc.url, requisites=doc.requisites, checked_at=doc.checked_at,
            currency_notes=doc.currency_notes, confirmation_limits=doc.confirmation_limits,
        ) if doc else None,
        attribution_owner=news.attribution_owner,
        attribution_text=news.attribution_text,
        published_at=news.published_at,
    )

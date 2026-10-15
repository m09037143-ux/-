from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.deps import require_platform_admin
from app.schemas.marketing import AdminLeadsOut, EarlyAccessIn, EarlyAccessOut, EventIn, PublicConfigOut
from app.services import marketing_service
from app.services.rate_limit import enforce_rate_limit

# Публичные эндпоинты лендинга (без входа) и просмотр заявок для администратора платформы.
public_router = APIRouter(prefix="/api/v1/public", tags=["public"])
admin_router = APIRouter(
    prefix="/api/v1/admin/leads", tags=["admin-leads"], dependencies=[Depends(require_platform_admin)]
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@public_router.get("/config", response_model=PublicConfigOut)
async def public_config():
    """Что нужно лендингу: открыта ли регистрация (иначе вместо неё — заявка на ранний доступ)."""
    return PublicConfigOut(registration_open=not get_settings().admin_only_mode)


@public_router.post("/early-access", response_model=EarlyAccessOut, status_code=201)
async def early_access(payload: EarlyAccessIn, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit("early_access", _client_ip(request), 8, 3600)
    return await marketing_service.submit_lead(db, payload)


@public_router.post("/events", status_code=204)
async def track_event(payload: EventIn, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit("product_event", _client_ip(request), 300, 3600)
    await marketing_service.record_event(db, visitor_id=payload.visitor_id, name=payload.name)
    return Response(status_code=204)


@admin_router.get("", response_model=AdminLeadsOut)
async def admin_leads(db: AsyncSession = Depends(get_db)):
    funnel, total, leads = await marketing_service.admin_overview(db)
    return AdminLeadsOut(funnel=funnel, total=total, leads=leads)

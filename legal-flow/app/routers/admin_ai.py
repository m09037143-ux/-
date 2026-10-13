import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership, get_current_user, require_csrf, require_platform_admin
from app.models import Membership, User
from app.schemas.admin_ai import (
    AiOverviewOut,
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    StagesUpdate,
    TestRequest,
    TestResultOut,
)
from app.services import ai_admin_service
from app.services.rate_limit import enforce_rate_limit

# Только администратор платформы. Ключи принимаются один раз и наружу не отдаются (только маска).
router = APIRouter(prefix="/api/v1/admin/ai", tags=["admin-ai"], dependencies=[Depends(require_platform_admin)])


@router.get("", response_model=AiOverviewOut)
async def get_ai_overview(db: AsyncSession = Depends(get_db)):
    return await ai_admin_service.overview(db)


@router.post("/connections", response_model=ConnectionOut, status_code=201, dependencies=[Depends(require_csrf)])
async def create_connection(
    payload: ConnectionCreate,
    user: User = Depends(get_current_user),
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    conn = await ai_admin_service.create_connection(db, payload=payload, user_id=user.id, workspace_id=membership.workspace_id)
    return ai_admin_service.connection_out(conn)


@router.patch("/connections/{connection_id}", response_model=ConnectionOut, dependencies=[Depends(require_csrf)])
async def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdate,
    user: User = Depends(get_current_user),
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    conn = await ai_admin_service.update_connection(
        db, connection_id=connection_id, payload=payload, user_id=user.id, workspace_id=membership.workspace_id
    )
    return ai_admin_service.connection_out(conn)


@router.delete("/connections/{connection_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_connection(
    connection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    await ai_admin_service.delete_connection(db, connection_id=connection_id, user_id=user.id, workspace_id=membership.workspace_id)


@router.post("/connections/{connection_id}/test", response_model=TestResultOut, dependencies=[Depends(require_csrf)])
async def test_connection(
    connection_id: uuid.UUID,
    payload: TestRequest,
    user: User = Depends(get_current_user),
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit("ai_test", str(user.id), 30, 3600)  # реальный платный запрос — не чаще 30 в час
    return await ai_admin_service.test_connection(
        db, connection_id=connection_id, model=payload.model, user_id=user.id, workspace_id=membership.workspace_id
    )


@router.put("/stages", response_model=AiOverviewOut, dependencies=[Depends(require_csrf)])
async def put_stages(
    payload: StagesUpdate,
    user: User = Depends(get_current_user),
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    await ai_admin_service.set_stages(db, payload=payload, user_id=user.id, workspace_id=membership.workspace_id)
    return await ai_admin_service.overview(db)

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership, get_current_user, require_active_access, require_csrf, require_role
from app.models import ApiKey, Membership, User
from app.models.enums import Role
from app.schemas.exports import ApiKeyCreatedOut, ApiKeyOut, CreateApiKeyRequest
from app.services import api_key_service

router = APIRouter(prefix="/api/v1", tags=["api-keys"])


def _out(k: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=k.id, name=k.name, key_prefix=k.key_prefix, created_at=k.created_at,
        last_used_at=k.last_used_at, revoked_at=k.revoked_at,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(membership: Membership = Depends(get_current_membership), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.scalars(select(ApiKey).where(ApiKey.workspace_id == membership.workspace_id).order_by(ApiKey.created_at.desc()))
    ).all()
    return [_out(k) for k in rows]


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=201, dependencies=[Depends(require_csrf)])
async def create_api_key(
    payload: CreateApiKeyRequest,
    membership: Membership = Depends(require_role(Role.workspace_owner)),
    _access: Membership = Depends(require_active_access),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    key, raw = await api_key_service.create_key(db, workspace_id=membership.workspace_id, user_id=user.id, name=payload.name)
    return ApiKeyCreatedOut(**_out(key).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def revoke_api_key(
    key_id: uuid.UUID,
    membership: Membership = Depends(require_role(Role.workspace_owner)),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await api_key_service.revoke_key(db, workspace_id=membership.workspace_id, key_id=key_id, user_id=user.id)

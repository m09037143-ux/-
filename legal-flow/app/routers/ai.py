import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_active_access, require_csrf, require_role
from app.models import Membership, User
from app.models.enums import Role
from app.services import ai_service, news_service

router = APIRouter(prefix="/api/v1", tags=["ai"])
_EDIT_ROLES = (Role.workspace_owner, Role.editor)


class AiDraftRequest(BaseModel):
    expected_version: int


@router.post("/news/{news_id}/ai/draft", dependencies=[Depends(require_csrf)])
async def post_ai_draft(
    news_id: uuid.UUID,
    payload: AiDraftRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    return await ai_service.generate_ai_draft(db, news=news, expected_version=payload.expected_version, user_id=user.id)


@router.post("/news/{news_id}/ai/critique", dependencies=[Depends(require_csrf)])
async def post_ai_critique(
    news_id: uuid.UUID,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    news = await news_service.get_owned_news(db, workspace_id=membership.workspace_id, news_id=news_id)
    return await ai_service.run_critique(db, news=news)

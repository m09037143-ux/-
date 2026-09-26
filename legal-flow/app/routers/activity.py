from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership
from app.models import ActivityEvent, Membership
from app.schemas.news import ActivityEventOut

router = APIRouter(prefix="/api/v1", tags=["activity"])


@router.get("/activity", response_model=list[ActivityEventOut])
async def list_activity(
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(ActivityEvent)
        .where(ActivityEvent.workspace_id == membership.workspace_id)
        .order_by(ActivityEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    events = (await db.scalars(stmt)).all()
    return [ActivityEventOut(id=e.id, action=e.action, details=e.details, created_at=e.created_at) for e in events]

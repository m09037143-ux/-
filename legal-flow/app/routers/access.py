from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_membership
from app.models import Membership, Plan
from app.schemas.access import AccessResponse
from app.services import trial_service
from app.services.billing_service import get_plan_by_code

router = APIRouter(prefix="/api/v1", tags=["access"])


@router.get("/access", response_model=AccessResponse)
async def get_access(
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    trial = await trial_service.get_trial(db, membership.workspace_id)
    state = trial_service.trial_state(trial)
    sub = await trial_service.get_active_subscription(db, membership.workspace_id)
    can_mutate = await trial_service.has_mutating_access(db, membership.workspace_id)

    if sub is not None:
        plan = await db.get(Plan, sub.plan_id)
    else:
        plan = await get_plan_by_code(db, settings.trial_plan_code)

    return AccessResponse(
        role=membership.role.value,
        trial_state=state.value,
        trial_started_at=trial.started_at if trial else None,
        trial_ends_at=trial.ends_at if trial else None,
        server_time=datetime.now(timezone.utc),
        plan_code=plan.code if plan else None,
        plan_name=plan.name if plan else None,
        can_mutate=can_mutate,
        limits=plan.limits if plan else {},
    )

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership
from app.models import Membership
from app.schemas.access import AccessResponse
from app.services import trial_service

router = APIRouter(prefix="/api/v1", tags=["access"])


@router.get("/access", response_model=AccessResponse)
async def get_access(
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    trial = await trial_service.get_trial(db, membership.workspace_id)
    state = trial_service.trial_state(trial)
    can_mutate = await trial_service.has_mutating_access(db, membership.workspace_id)
    # Same plan resolution flow_service uses to enforce limits (paid subscription,
    # else the top tier for a platform_admin's workspace, else the trial tier) —
    # kept in one place so the displayed plan always matches what's enforced.
    plan = await trial_service.get_effective_plan(db, membership.workspace_id)

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

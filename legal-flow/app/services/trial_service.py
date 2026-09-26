from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Plan, Subscription, Trial
import uuid


class TrialState(str, Enum):
    none = "none"
    active = "active"
    expired = "expired"


async def get_trial(db: AsyncSession, workspace_id: uuid.UUID) -> Trial | None:
    return await db.scalar(select(Trial).where(Trial.workspace_id == workspace_id))


def trial_state(trial: Trial | None, *, now: datetime | None = None) -> TrialState:
    if trial is None:
        return TrialState.none
    now = now or datetime.now(timezone.utc)
    return TrialState.active if now < trial.ends_at else TrialState.expired


async def get_active_subscription(db: AsyncSession, workspace_id: uuid.UUID) -> Subscription | None:
    now = datetime.now(timezone.utc)
    sub = await db.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace_id, Subscription.active.is_(True))
    )
    if sub is None:
        return None
    if sub.current_period_end is not None and sub.current_period_end < now:
        return None
    return sub


async def has_mutating_access(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Gate for collect/edit/review/approve/publish. A workspace may act while its
    trial is active OR while it holds an active paid subscription (ТЗ §6-7)."""
    sub = await get_active_subscription(db, workspace_id)
    if sub is not None:
        return True
    trial = await get_trial(db, workspace_id)
    return trial_state(trial) == TrialState.active


async def get_effective_plan(db: AsyncSession, workspace_id: uuid.UUID) -> Plan | None:
    """The plan whose limits currently govern the workspace: its paid subscription's
    plan if one is active, otherwise the trial-tier plan (ТЗ §6-7)."""
    sub = await get_active_subscription(db, workspace_id)
    if sub is not None:
        return await db.get(Plan, sub.plan_id)
    return await db.scalar(select(Plan).where(Plan.code == get_settings().trial_plan_code))

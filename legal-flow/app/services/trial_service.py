from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Membership, Plan, Subscription, Trial, User
import uuid

# Платформенный администратор не ограничен тарифом/триалом рабочего пространства,
# которым управляет (ему нужен доступ ко всем возможностям для поддержки/проверки),
# но Trial.ends_at при этом никогда не трогается — см. запрет на изменение в
# app/models/tenancy.py (ТЗ §6).
_PLATFORM_ADMIN_EFFECTIVE_PLAN_CODE = "editorial"


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


async def _workspace_has_platform_admin(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(Membership)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == workspace_id, User.is_platform_admin.is_(True))
    )
    return bool(count)


async def has_mutating_access(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Gate for collect/edit/review/approve/publish. A workspace may act while its
    trial is active OR while it holds an active paid subscription (ТЗ §6-7) — or,
    same as require_role already does for role checks, when a platform_admin owns
    the workspace."""
    if await _workspace_has_platform_admin(db, workspace_id):
        return True
    sub = await get_active_subscription(db, workspace_id)
    if sub is not None:
        return True
    trial = await get_trial(db, workspace_id)
    return trial_state(trial) == TrialState.active


async def get_effective_plan(db: AsyncSession, workspace_id: uuid.UUID) -> Plan | None:
    """The plan whose limits currently govern the workspace: its paid subscription's
    plan if one is active, otherwise the trial-tier plan (ТЗ §6-7) — or the top plan's
    limits for a workspace a platform_admin owns, so admin accounts aren't limited to
    the trial tier."""
    sub = await get_active_subscription(db, workspace_id)
    if sub is not None:
        return await db.get(Plan, sub.plan_id)
    if await _workspace_has_platform_admin(db, workspace_id):
        return await db.scalar(select(Plan).where(Plan.code == _PLATFORM_ADMIN_EFFECTIVE_PLAN_CODE))
    return await db.scalar(select(Plan).where(Plan.code == get_settings().trial_plan_code))

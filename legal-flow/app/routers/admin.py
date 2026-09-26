import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_csrf, require_platform_admin
from app.errors import AppError
from app.models import Plan, SourcePolicy, User
from app.models.enums import SourceAction
from app.schemas.admin import (
    AdminPlanOut,
    AdminUserOut,
    CreateSourcePolicyRequest,
    SourcePolicyOut,
    UpdatePlanLimitsRequest,
    UpdateSourcePolicyRequest,
)
from app.services.flow_service import validate_domain

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_platform_admin)])


def _validate_actions(actions: list[str]) -> None:
    valid = {a.value for a in SourceAction}
    unknown = set(actions) - valid
    if unknown:
        raise AppError("VALIDATION_ERROR", f"Неизвестные действия: {sorted(unknown)}")


@router.get("/source-policies", response_model=list[SourcePolicyOut])
async def list_source_policies(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(SourcePolicy).order_by(SourcePolicy.domain))).all()
    return [SourcePolicyOut(**{k: getattr(r, k) for k in SourcePolicyOut.model_fields}) for r in rows]


@router.post("/source-policies", response_model=SourcePolicyOut, status_code=201, dependencies=[Depends(require_csrf)])
async def create_source_policy(payload: CreateSourcePolicyRequest, db: AsyncSession = Depends(get_db)):
    _validate_actions(payload.allowed_actions)
    domain = validate_domain(payload.domain)
    existing = await db.scalar(
        select(SourcePolicy).where(SourcePolicy.domain == domain, SourcePolicy.material_path == payload.material_path)
    )
    if existing is not None:
        raise AppError("DUPLICATE", "Правило для этого домена/материала уже существует.")

    policy = SourcePolicy(
        domain=domain,
        material_path=payload.material_path,
        reviewed_at=datetime.now(timezone.utc),
        basis=payload.basis,
        responsible=payload.responsible,
        allowed_actions=payload.allowed_actions,
        attribution_required=payload.attribution_required,
        attribution_text=payload.attribution_text,
    )
    db.add(policy)
    await db.commit()
    return SourcePolicyOut(**{k: getattr(policy, k) for k in SourcePolicyOut.model_fields})


@router.patch("/source-policies/{policy_id}", response_model=SourcePolicyOut, dependencies=[Depends(require_csrf)])
async def update_source_policy(policy_id: uuid.UUID, payload: UpdateSourcePolicyRequest, db: AsyncSession = Depends(get_db)):
    policy = await db.get(SourcePolicy, policy_id)
    if policy is None:
        raise AppError("VALIDATION_ERROR", "Правило не найдено.")
    if payload.allowed_actions is not None:
        _validate_actions(payload.allowed_actions)
    for field_name in ("basis", "responsible", "allowed_actions", "attribution_required", "attribution_text"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(policy, field_name, value)
    policy.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return SourcePolicyOut(**{k: getattr(policy, k) for k in SourcePolicyOut.model_fields})


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(User).order_by(User.created_at.desc()))).all()
    return [AdminUserOut(id=u.id, name=u.name, email=u.email, is_platform_admin=u.is_platform_admin, created_at=u.created_at) for u in rows]


@router.get("/plans", response_model=list[AdminPlanOut])
async def list_plans_admin(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Plan).order_by(Plan.price_rub))).all()
    return [AdminPlanOut(id=p.id, code=p.code, name=p.name, price_rub=p.price_rub, limits=p.limits) for p in rows]


@router.patch("/plans/{plan_id}/limits", response_model=AdminPlanOut, dependencies=[Depends(require_csrf)])
async def update_plan_limits(plan_id: uuid.UUID, payload: UpdatePlanLimitsRequest, db: AsyncSession = Depends(get_db)):
    """Лимиты можно скорректировать здесь; цену — нет: цены зафиксированы в ТЗ §7 и
    меняются только отдельным согласованным решением, а не через этот эндпоинт."""
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise AppError("VALIDATION_ERROR", "Тариф не найден.")
    plan.limits = payload.limits
    await db.commit()
    return AdminPlanOut(id=plan.id, code=plan.code, name=plan.name, price_rub=plan.price_rub, limits=plan.limits)

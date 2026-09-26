import json
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership, require_csrf
from app.errors import AppError
from app.models import Membership, Order
from app.schemas.billing import CreateOrderRequest, FakeWebhookRequest, OrderOut, PlanOut
from app.services import billing_service

router = APIRouter(prefix="/api/v1", tags=["billing"])


@router.get("/plans", response_model=list[PlanOut])
async def get_plans(db: AsyncSession = Depends(get_db)):
    plans = await billing_service.list_plans(db)
    return [PlanOut(id=p.id, code=p.code, name=p.name, price_rub=p.price_rub, limits=p.limits) for p in plans]


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    orders = (
        await db.scalars(
            select(Order).where(Order.workspace_id == membership.workspace_id).order_by(Order.created_at.desc())
        )
    ).all()
    return [OrderOut(id=o.id, plan_id=o.plan_id, status=o.status.value, amount_rub=o.amount_rub, created_at=o.created_at) for o in orders]


@router.post("/orders", response_model=OrderOut, status_code=201, dependencies=[Depends(require_csrf)])
async def create_order(
    payload: CreateOrderRequest,
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    plan = await billing_service.get_plan_by_code(db, payload.plan_code)
    if plan is None:
        raise AppError("VALIDATION_ERROR", "Тариф не найден.")
    order = await billing_service.create_order(
        db, workspace_id=membership.workspace_id, plan_id=plan.id, idempotency_key=payload.idempotency_key
    )
    return OrderOut(id=order.id, plan_id=order.plan_id, status=order.status.value, amount_rub=order.amount_rub, created_at=order.created_at)


@router.post("/orders/{order_id}/pay", dependencies=[Depends(require_csrf)])
async def pay_order(
    order_id: uuid.UUID,
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(Order, order_id)
    if order is None or order.workspace_id != membership.workspace_id:
        raise AppError("VALIDATION_ERROR", "Заказ не найден.")
    await billing_service.attempt_card_payment(db, order=order)


@router.post("/orders/{order_id}/webhook/fake", status_code=204)
async def fake_webhook(order_id: uuid.UUID, payload: FakeWebhookRequest, db: AsyncSession = Depends(get_db)):
    """Только для автоматических тестов при PAYMENT_PROVIDER=fake — не является
    реальной интеграцией и не должно вызываться из production-конфигурации."""
    raw_payload = json.dumps(
        {"provider_event_id": payload.provider_event_id, "amount_rub": payload.amount_rub, "currency": payload.currency},
        sort_keys=True,
    ).encode("utf-8")
    await billing_service.apply_fake_webhook(
        db,
        order_id=order_id,
        provider_event_id=payload.provider_event_id,
        amount_rub=payload.amount_rub,
        currency=payload.currency,
        signature=payload.signature,
        raw_payload=raw_payload,
    )

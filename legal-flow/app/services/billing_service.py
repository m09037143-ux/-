import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import Order, PaymentEvent, Plan, Subscription
from app.models.enums import OrderStatus


async def list_plans(db: AsyncSession) -> list[Plan]:
    return list((await db.scalars(select(Plan).order_by(Plan.price_rub.asc()))).all())


async def get_plan_by_code(db: AsyncSession, code: str) -> Plan | None:
    return await db.scalar(select(Plan).where(Plan.code == code))


async def create_order(db: AsyncSession, *, workspace_id: uuid.UUID, plan_id: uuid.UUID, idempotency_key: str | None) -> Order:
    if idempotency_key:
        existing = await db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise AppError("VALIDATION_ERROR", "Тариф не найден.")
    order = Order(
        workspace_id=workspace_id,
        plan_id=plan_id,
        status=OrderStatus.pending,
        amount_rub=plan.price_rub,
        idempotency_key=idempotency_key,
    )
    db.add(order)
    await db.commit()
    return order


async def attempt_card_payment(db: AsyncSession, *, order: Order) -> None:
    """The "Оплатить картой" button. Никогда не запрашивает карту на нашем сайте
    и никогда не переводит заказ в paid без настоящего провайдера (ТЗ §7)."""
    settings = get_settings()
    if settings.payment_provider == "not_configured":
        raise AppError("PAYMENT_NOT_CONFIGURED", "Приём платежей пока не подключён.")
    # Ветка ниже существует только для settings.payment_provider == "fake" в тестах.
    raise AppError("PAYMENT_NOT_CONFIGURED", "Платёжный провайдер не подтверждён для этого окружения.")


def sign_fake_webhook(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def apply_fake_webhook(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider_event_id: str,
    amount_rub: int,
    currency: str,
    signature: str,
    raw_payload: bytes,
) -> Order:
    """Test-only provider used by the automated test suite to exercise the
    webhook → order → subscription path without a real payment gateway (ТЗ §7).
    Must never be reachable when settings.payment_provider != 'fake'."""
    settings = get_settings()
    if settings.payment_provider != "fake":
        raise AppError("PAYMENT_NOT_CONFIGURED", "Fake-провайдер отключён в этом окружении.")

    expected_sig = sign_fake_webhook(raw_payload, settings.payment_webhook_secret)
    signature_valid = hmac.compare_digest(expected_sig, signature)

    order = await db.get(Order, order_id)
    if order is None:
        raise AppError("VALIDATION_ERROR", "Заказ не найден.")

    # Идемпотентность: повтор того же provider_event_id никогда не создаёт вторую подписку.
    existing_event = await db.scalar(
        select(PaymentEvent).where(
            PaymentEvent.provider == "fake", PaymentEvent.provider_event_id == provider_event_id
        )
    )
    if existing_event is not None:
        return order

    db.add(
        PaymentEvent(
            order_id=order.id,
            provider="fake",
            provider_event_id=provider_event_id,
            signature_valid=signature_valid,
            payload={"amount_rub": amount_rub, "currency": currency},
        )
    )

    if not signature_valid:
        await db.commit()
        raise AppError("VALIDATION_ERROR", "Недействительная подпись webhook.")
    if amount_rub != order.amount_rub or currency != "RUB":
        await db.commit()
        raise AppError("VALIDATION_ERROR", "Сумма или валюта не совпадают с заказом.")

    order.status = OrderStatus.paid

    sub = await db.scalar(select(Subscription).where(Subscription.workspace_id == order.workspace_id))
    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=30)
    if sub is None:
        sub = Subscription(workspace_id=order.workspace_id, plan_id=order.plan_id, active=True, current_period_end=period_end)
        db.add(sub)
    else:
        sub.plan_id = order.plan_id
        sub.active = True
        sub.current_period_end = period_end

    await db.commit()
    return order

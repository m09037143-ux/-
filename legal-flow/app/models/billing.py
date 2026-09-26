import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import TimestampMixin, UUIDPk
from app.models.enums import OrderStatus


class Plan(Base, UUIDPk):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    limits: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Например: {"max_flows":1,"max_sources":3,"max_news_per_run":3,"schedule":"manual_or_daily"}


class Subscription(Base, UUIDPk, TimestampMixin):
    __tablename__ = "subscriptions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Order(Base, UUIDPk, TimestampMixin):
    __tablename__ = "orders"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(nullable=False, default=OrderStatus.pending)
    amount_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)


class PaymentEvent(Base, UUIDPk, TimestampMixin):
    """Raw webhook events, kept for audit even though no real provider is wired up yet."""

    __tablename__ = "payment_events"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    signature_valid: Mapped[bool] = mapped_column(nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_event_provider_id"),
    )

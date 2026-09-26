import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    price_rub: int
    limits: dict


class CreateOrderRequest(BaseModel):
    plan_code: str
    idempotency_key: str | None = Field(default=None, max_length=200)


class OrderOut(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    status: str
    amount_rub: int
    created_at: datetime


class FakeWebhookRequest(BaseModel):
    provider_event_id: str
    amount_rub: int
    currency: str = "RUB"
    signature: str

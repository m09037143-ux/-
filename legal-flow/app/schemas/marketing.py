import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

_VISITOR_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# События воронки: только этот список — произвольные имена сервер не принимает.
EVENT_NAMES = ("landing_view", "cta_click", "demo_click", "lead_form_open", "lead_submit")


class PublicConfigOut(BaseModel):
    registration_open: bool


class EarlyAccessIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    organization: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=100)
    comment: str = Field(default="", max_length=1000)
    consent: bool
    visitor_id: str = Field(default="", max_length=64)
    website: str = Field(default="", max_length=200)  # ловушка для ботов: человек это поле не видит и не заполняет

    @field_validator("name", "organization", "role", "comment")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("укажите имя")
        return v

    @field_validator("consent")
    @classmethod
    def _consent(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("нужно согласие на обработку данных")
        return v

    @field_validator("visitor_id")
    @classmethod
    def _visitor(cls, v: str) -> str:
        v = v.strip()
        return v if _VISITOR_RE.match(v) else ""


class EarlyAccessOut(BaseModel):
    ok: bool = True
    already: bool = False
    message: str


class EventIn(BaseModel):
    visitor_id: str
    name: Literal["landing_view", "cta_click", "demo_click", "lead_form_open", "lead_submit"]

    @field_validator("visitor_id")
    @classmethod
    def _visitor(cls, v: str) -> str:
        v = v.strip()
        if not _VISITOR_RE.match(v):
            raise ValueError("некорректный идентификатор посетителя")
        return v


class LeadOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    organization: str
    role: str
    comment: str
    created_at: datetime


class FunnelOut(BaseModel):
    """Уникальные посетители на каждом шаге и конверсия (в процентах, один знак после запятой)."""

    landing_visitors: int
    cta_clicks: int
    demo_clicks: int
    form_opens: int
    leads: int
    cta_rate_pct: float
    lead_rate_pct: float


class AdminLeadsOut(BaseModel):
    funnel: FunnelOut
    total: int
    leads: list[LeadOut]

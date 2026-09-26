import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SourcePolicyOut(BaseModel):
    id: uuid.UUID
    domain: str
    material_path: str
    reviewed_at: datetime | None
    basis: str
    responsible: str
    allowed_actions: list[str]
    attribution_required: bool
    attribution_text: str


class CreateSourcePolicyRequest(BaseModel):
    domain: str
    material_path: str = ""
    basis: str = Field(min_length=1)
    responsible: str = Field(min_length=1)
    allowed_actions: list[str]
    attribution_required: bool = False
    attribution_text: str = ""


class UpdateSourcePolicyRequest(BaseModel):
    basis: str | None = None
    responsible: str | None = None
    allowed_actions: list[str] | None = None
    attribution_required: bool | None = None
    attribution_text: str | None = None


class AdminUserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    is_platform_admin: bool
    created_at: datetime


class AdminPlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    price_rub: int
    limits: dict


class UpdatePlanLimitsRequest(BaseModel):
    limits: dict

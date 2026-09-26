import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateFlowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    theme: str = Field(min_length=1, max_length=300)
    schedule_period: str = "Вручную"
    schedule_time: str = "09:00"
    news_limit_per_run: int = Field(default=3, ge=1, le=100)
    domains: list[str] = Field(default_factory=list, max_length=50)


class UpdateFlowRequest(BaseModel):
    name: str | None = None
    theme: str | None = None
    schedule_period: str | None = None
    schedule_time: str | None = None
    news_limit_per_run: int | None = Field(default=None, ge=1, le=100)


class SourceSiteOut(BaseModel):
    id: uuid.UUID
    domain: str
    active: bool


class FlowOut(BaseModel):
    id: uuid.UUID
    name: str
    theme: str
    schedule_period: str
    schedule_time: str
    news_limit_per_run: int
    sources: list[SourceSiteOut] = Field(default_factory=list)


class AddSourceRequest(BaseModel):
    domain: str


class ScanJobOut(BaseModel):
    id: uuid.UUID
    flow_id: uuid.UUID
    status: str
    provider_name: str
    created_news_ids: list[str]
    error: str | None
    created_at: datetime
    finished_at: datetime | None

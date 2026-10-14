import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CreateExportRequest(BaseModel):
    filename: str = Field(default="legal-news.xml", max_length=255)
    # latest — последние `limit` опубликованных (по умолчанию); selected — выбранные вручную; all — все опубликованные.
    # Если переданы news_item_ids, выгружаются именно они.
    mode: Literal["latest", "selected", "all"] = "latest"
    limit: int = Field(default=10, ge=1, le=200)
    news_item_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)


class ExportCandidateOut(BaseModel):
    id: uuid.UUID
    title: str
    release_mode: str
    published_at: datetime | None = None
    created_at: datetime


class ExportOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_hash: str
    news_item_ids: list[str]
    status: str
    created_at: datetime


class ExportDownloadOut(BaseModel):
    filename: str
    content: str
    status: str


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreatedOut(ApiKeyOut):
    key: str = Field(description="Полный ключ — показывается один раз, восстановить его нельзя")


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FeedOfficialDocumentOut(BaseModel):
    title: str
    url: str
    requisites: str


class FeedAttributionOut(BaseModel):
    owner: str
    text: str


class FeedItemOut(BaseModel):
    id: str
    title: str
    text: str
    release_mode: str
    official_document: FeedOfficialDocumentOut | None = None
    attribution: FeedAttributionOut | None = None
    published_at: str | None = None


class FeedOut(BaseModel):
    items: list[FeedItemOut]
    count: int
    has_more: bool

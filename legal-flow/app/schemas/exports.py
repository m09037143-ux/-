import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateExportRequest(BaseModel):
    filename: str = Field(default="legal-news.xml", max_length=255)


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

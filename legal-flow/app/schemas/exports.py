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

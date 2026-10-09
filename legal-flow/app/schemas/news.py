import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OfficialDocumentOut(BaseModel):
    title: str
    url: str
    requisites: str
    checked_at: datetime | None
    currency_notes: str
    confirmation_limits: str


class FactPassportOut(BaseModel):
    text: str
    overall_status: str


class NewsItemOut(BaseModel):
    """InternalNews (ТЗ §11) — includes discovery source, only for the editor's own
    workspace. Never served as-is to the public preview / XML."""

    id: uuid.UUID
    flow_id: uuid.UUID
    title: str
    text: str
    status: str
    release_mode: str
    discovery_domain: str
    discovery_original_fragment: str
    official_reviewed: bool
    facts_reviewed: bool
    official_document: OfficialDocumentOut | None
    fact_passport: FactPassportOut | None
    attribution_owner: str
    attribution_text: str
    version: int
    rejected_reason: str
    created_at: datetime


class NewsListItemOut(BaseModel):
    id: uuid.UUID
    title: str
    discovery_domain: str
    status: str
    has_official_document: bool
    created_at: datetime


class UpdateDraftRequest(BaseModel):
    expected_version: int
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1)


class UpdateOfficialDocumentRequest(BaseModel):
    expected_version: int
    title: str = Field(min_length=1, max_length=500)
    url: str
    requisites: str = ""
    currency_notes: str = ""
    confirmation_limits: str = ""


class UpdateFactsRequest(BaseModel):
    expected_version: int
    text: str
    overall_status: str = "needs_review"


class ReviewRequest(BaseModel):
    official_reviewed: bool
    facts_reviewed: bool
    note: str = ""


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1)


class PublicNewsOut(BaseModel):
    """PublicNews (ТЗ §11): only the approved text, official document, and
    attribution when it is required. Never the discovery domain/original fragment."""

    id: uuid.UUID
    title: str
    text: str
    release_mode: str
    official_document: OfficialDocumentOut | None
    attribution_owner: str
    attribution_text: str
    published_at: datetime | None


class ActivityEventOut(BaseModel):
    id: uuid.UUID
    action: str
    details: dict
    created_at: datetime


class SimilarityOut(BaseModel):
    share: float
    longest_run_words: int
    longest_run_text: str
    warning: bool


class SourceViewOut(BaseModel):
    url: str | None
    domain: str
    fragment: str
    published: str
    live: bool
    models: dict | None = None
    text: str | None = None
    text_note: str | None = None
    similarity: SimilarityOut | None = None

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_MAX_WORDS = 30
_MAX_WORD_LEN = 60


def clean_word_list(items: list[str] | None) -> list[str] | None:
    """Обрезает пробелы, выкидывает пустые и повторы (без учёта регистра), ограничивает длину
    и число — чтобы в потоке не оказалось тысячи слов или слов-простыней."""
    if items is None:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in items:
        word = " ".join(str(raw).split())[:_MAX_WORD_LEN]
        key = word.lower()
        if word and key not in seen:
            seen.add(key)
            cleaned.append(word)
    return cleaned[:_MAX_WORDS]


class CreateFlowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    theme: str = Field(min_length=1, max_length=300)
    schedule_period: str = "Вручную"
    schedule_time: str = "09:00"
    news_limit_per_run: int = Field(default=3, ge=1, le=100)
    domains: list[str] = Field(default_factory=list, max_length=50)
    keywords: list[str] = Field(default_factory=list, max_length=200)
    stop_words: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("keywords", "stop_words")
    @classmethod
    def _clean_words(cls, v):
        return clean_word_list(v)


class UpdateFlowRequest(BaseModel):
    name: str | None = None
    theme: str | None = None
    schedule_period: str | None = None
    schedule_time: str | None = None
    news_limit_per_run: int | None = Field(default=None, ge=1, le=100)
    keywords: list[str] | None = Field(default=None, max_length=200)
    stop_words: list[str] | None = Field(default=None, max_length=200)

    @field_validator("keywords", "stop_words")
    @classmethod
    def _clean_words(cls, v):
        return clean_word_list(v)


class SourceSiteOut(BaseModel):
    id: uuid.UUID
    domain: str
    active: bool
    html_supported: bool = False


class FlowOut(BaseModel):
    id: uuid.UUID
    name: str
    theme: str
    schedule_period: str
    schedule_time: str
    news_limit_per_run: int
    keywords: list[str] = Field(default_factory=list)
    stop_words: list[str] = Field(default_factory=list)
    sources: list[SourceSiteOut] = Field(default_factory=list)
    real_collection_enabled: bool = False


class AddSourceRequest(BaseModel):
    domain: str


class UpdateSourceRequest(BaseModel):
    domain: str | None = None
    active: bool | None = None


class ScanJobOut(BaseModel):
    id: uuid.UUID
    flow_id: uuid.UUID
    status: str
    provider_name: str
    created_news_ids: list[str]
    duplicate_count: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None

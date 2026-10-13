import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$")
_FOLDER_RE = re.compile(r"^[A-Za-z0-9_\-]{5,64}$")
_KEY_RE = re.compile(r"^[!-~]{8,500}$")  # печатная латиница без пробелов: ключ уходит в HTTP-заголовок


def check_model_name(value: str) -> str:
    value = value.strip()
    if not _MODEL_RE.match(value):
        raise ValueError("Название модели: латиница, цифры и символы . _ : / @ + - (до 200 знаков)")
    return value


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["yandex", "openai_compatible"]
    api_key: str
    folder_id: str = ""
    base_url: str = ""

    @field_validator("api_key")
    @classmethod
    def _key(cls, v: str) -> str:
        v = v.strip()
        if not _KEY_RE.match(v):
            raise ValueError("API-ключ: от 8 знаков, только латиница, цифры и знаки, без пробелов")
        return v

    @field_validator("folder_id")
    @classmethod
    def _folder(cls, v: str) -> str:
        v = v.strip()
        if v and not _FOLDER_RE.match(v):
            raise ValueError("Идентификатор каталога: латиница, цифры, - и _ (5–64 знака)")
        return v


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = None  # не передан — прежний ключ сохраняется
    folder_id: str | None = None
    base_url: str | None = None

    @field_validator("api_key")
    @classmethod
    def _key(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        v = v.strip()
        if not _KEY_RE.match(v):
            raise ValueError("API-ключ: от 8 знаков, только латиница, цифры и знаки, без пробелов")
        return v

    @field_validator("folder_id")
    @classmethod
    def _folder(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not _FOLDER_RE.match(v):
            raise ValueError("Идентификатор каталога: латиница, цифры, - и _ (5–64 знака)")
        return v


class ConnectionOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    base_url: str
    folder_id: str
    key_hint: str
    last_check_at: datetime | None = None
    last_check_ok: bool | None = None
    last_check_note: str = ""


class StageIn(BaseModel):
    connection_id: uuid.UUID
    model: str

    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        return check_model_name(v)


class StagesUpdate(BaseModel):
    """Ключи — этапы relevance / facts / draft / fallback; null — сбросить этап к настройкам сервера."""

    stages: dict[str, StageIn | None]


class StageOut(BaseModel):
    stage: str
    connection_id: uuid.UUID | None = None
    connection_name: str = ""
    model: str = ""


class EffectiveOut(BaseModel):
    stage: str
    source: str  # admin | env | fixture | not_configured | none
    model: str = ""
    connection_name: str = ""


class AiOverviewOut(BaseModel):
    connections: list[ConnectionOut]
    stages: list[StageOut]
    effective: list[EffectiveOut]
    encryption_configured: bool
    model_suggestions: list[str]


class TestRequest(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        return check_model_name(v)


class TestResultOut(BaseModel):
    ok: bool
    latency_ms: int
    note: str

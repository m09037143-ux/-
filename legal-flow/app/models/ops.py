import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import TimestampMixin, UUIDPk
from app.models.enums import ExportStatus


class LlmCall(Base, UUIDPk):
    """One row per model call. Never stores the API key. Always stores the exact
    model URI used, so a silent swap is detectable in the log (ТЗ §10)."""

    __tablename__ = "llm_calls"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    news_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_items.id"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    model_uri: Mapped[str] = mapped_column(String(300), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_rub: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Export(Base, UUIDPk):
    __tablename__ = "exports"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    news_item_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[ExportStatus] = mapped_column(nullable=False, default=ExportStatus.NOT_SENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActivityEvent(Base, UUIDPk):
    __tablename__ = "activity_events"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiKey(Base, UUIDPk):
    """Ключ доступа к API (только чтение опубликованных материалов своего рабочего пространства).
    Сам ключ нигде не хранится — только SHA-256 (ключ длинный и случайный, соль не нужна);
    key_prefix — начало ключа, по которому его узнают в списке."""

    __tablename__ = "api_keys"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiConnection(Base, UUIDPk, TimestampMixin):
    """Подключение к ИИ-сервису, заведённое админом платформы: Яндекс Облако (каталог + ключ) или любой
    OpenAI-совместимый сервис (адрес API + ключ). Ключ хранится только зашифрованным (app/secrets_box.py),
    в key_hint — маска из последних четырёх символов для списка."""

    __tablename__ = "ai_connections"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # yandex | openai_compatible
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    folder_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AiStageModel(Base, UUIDPk, TimestampMixin):
    """Какая модель на каком подключении работает на этапе: relevance (отбор по теме), facts (паспорт фактов),
    draft (черновик), fallback (запасная при отказе/битом JSON). Нет строки — этап работает по настройкам
    сервера (переменные окружения)."""

    __tablename__ = "ai_stage_models"

    stage: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_connections.id", ondelete="RESTRICT"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

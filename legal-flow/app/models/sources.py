import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import TimestampMixin, UUIDPk
from app.models.enums import ScanJobStatus


class SourcePolicy(Base, UUIDPk, TimestampMixin):
    """Global (not workspace-scoped) rules per domain — ТЗ §3.
    One allowed action never implies another."""

    __tablename__ = "source_policies"
    __table_args__ = (UniqueConstraint("domain", "material_path", name="uq_source_policy_domain_path"),)

    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    material_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    basis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    responsible: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    allowed_actions: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attribution_text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class NewsFlow(Base, UUIDPk, TimestampMixin):
    __tablename__ = "news_flows"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    theme: Mapped[str] = mapped_column(String(300), nullable=False)
    schedule_period: Mapped[str] = mapped_column(String(50), nullable=False, default="Вручную")
    schedule_time: Mapped[str] = mapped_column(String(5), nullable=False, default="09:00")
    news_limit_per_run: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Ключевые слова: хотя бы одно должно встретиться в заголовке/анонсе. Стоп-слова: любое — исключает материал.
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list, server_default=text("'{}'"))
    stop_words: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list, server_default=text("'{}'"))


class SourceSite(Base, UUIDPk, TimestampMixin):
    __tablename__ = "source_sites"
    __table_args__ = (UniqueConstraint("flow_id", "domain", name="uq_source_site_flow_domain"),)

    flow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_flows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Как собирать с сайта: builtin (свой парсер), feed (RSS/Atom), sitemap (новостной sitemap),
    # html (список статей по эвристике). None — ещё не определено (определится при сборе).
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    list_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # ready — можно собирать; unavailable — не нашли ленту/сайт не отвечает (причина в status_note)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready", server_default="ready")
    status_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # id пользователя, подтвердившего право использовать материалы сайта (нужно для повторного подключения при сборе)
    rights_confirmed_by: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")


class ScanJob(Base, UUIDPk, TimestampMixin):
    __tablename__ = "scan_jobs"
    __table_args__ = (
        UniqueConstraint("flow_id", "idempotency_key", name="uq_scan_job_flow_idempotency"),
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_flows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ScanJobStatus] = mapped_column(nullable=False, default=ScanJobStatus.pending)
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    created_news_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Discovery(Base, UUIDPk, TimestampMixin):
    """One row per candidate item a scan produced, before/after dedupe — kept for the
    duplicate journal required by ТЗ §9 even when it doesn't become a NewsItem."""

    __tablename__ = "discoveries"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scan_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scan_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    normalized_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    discovery_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    story_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of_news_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_items.id", use_alter=True, name="fk_discoveries_duplicate_of_news_id"),
        nullable=True,
    )
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

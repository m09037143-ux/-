import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import TimestampMixin, UUIDPk
from app.models.enums import FactStatus, NewsStatus, ReleaseMode


class Story(Base, UUIDPk, TimestampMixin):
    """Groups discoveries/news items that are the same underlying event, for antidup."""

    __tablename__ = "stories"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    story_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)


class OfficialDocument(Base, UUIDPk, TimestampMixin):
    __tablename__ = "official_documents"

    news_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_items.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    requisites: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    currency_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confirmation_limits: Mapped[str] = mapped_column(Text, nullable=False, default="")


class FactPassport(Base, UUIDPk, TimestampMixin):
    __tablename__ = "fact_passports"

    news_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_items.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    overall_status: Mapped[FactStatus] = mapped_column(nullable=False, default=FactStatus.needs_review)


class NewsItem(Base, UUIDPk, TimestampMixin):
    __tablename__ = "news_items"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    flow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_flows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    story_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stories.id"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    release_mode: Mapped[ReleaseMode] = mapped_column(nullable=False, default=ReleaseMode.INDEPENDENT_FACT_REPORT)
    status: Mapped[NewsStatus] = mapped_column(nullable=False, default=NewsStatus.DISCOVERED)

    # Внутреннее (никогда не выводится в PublicNews/XML для INDEPENDENT_FACT_REPORT).
    discovery_domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    discovery_original_fragment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    discovery_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discoveries.id"), nullable=True
    )

    # Совпадение черновика с оригиналом: {share, longest_run_words, warning, basis, checked_at, stale}.
    # Только числа — сам текст оригинала не хранится (нет retain_full_text).
    source_overlap: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    official_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    facts_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    attribution_owner: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    attribution_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


class NewsVersion(Base, UUIDPk):
    """Append-only version history of the editorial text — every edit gets a row."""

    __tablename__ = "news_versions"
    __table_args__ = (UniqueConstraint("news_item_id", "version", name="uq_news_version_item_version"),)

    news_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EditorReview(Base, UUIDPk):
    __tablename__ = "editor_reviews"

    news_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    official_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    facts_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

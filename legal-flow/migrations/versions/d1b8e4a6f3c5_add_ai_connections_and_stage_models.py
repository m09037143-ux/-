"""Подключения к ИИ-сервисам и модели по этапам (настраиваются в админке)

Revision ID: d1b8e4a6f3c5
Revises: c0a7d3e9f2b4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1b8e4a6f3c5"
down_revision: Union[str, None] = "c0a7d3e9f2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("folder_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(16), nullable=False, server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_ok", sa.Boolean(), nullable=True),
        sa.Column("last_check_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "ai_stage_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stage", sa.String(20), nullable=False, unique=True),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_connections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_stage_models")
    op.drop_table("ai_connections")

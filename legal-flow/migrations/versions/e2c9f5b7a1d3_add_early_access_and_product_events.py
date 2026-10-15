"""Заявки на ранний доступ и события воронки лендинга (проверка гипотезы)

Revision ID: e2c9f5b7a1d3
Revises: d1b8e4a6f3c5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2c9f5b7a1d3"
down_revision: Union[str, None] = "d1b8e4a6f3c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "early_access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("organization", sa.String(200), nullable=False, server_default=""),
        sa.Column("role", sa.String(100), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("consent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("visitor_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_early_access_requests_email", "early_access_requests", ["email"], unique=True)
    op.create_table(
        "product_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("visitor_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_product_events_visitor_id", "product_events", ["visitor_id"])
    op.create_index("ix_product_events_name", "product_events", ["name"])


def downgrade() -> None:
    op.drop_table("product_events")
    op.drop_table("early_access_requests")

"""Автоматический черновик после сбора (флаг потока)

Revision ID: a8e5f1b7c2d9
Revises: f6c3d8b2e4a5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8e5f1b7c2d9"
down_revision: Union[str, None] = "f6c3d8b2e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("news_flows", sa.Column("auto_draft", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    op.drop_column("news_flows", "auto_draft")

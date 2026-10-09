"""Ключевые слова и стоп-слова потока

Revision ID: e5b2c7a1d3f4
Revises: d4a8b1e6c9f2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5b2c7a1d3f4"
down_revision: Union[str, None] = "d4a8b1e6c9f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_flows",
        sa.Column("keywords", postgresql.ARRAY(sa.String()), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "news_flows",
        sa.Column("stop_words", postgresql.ARRAY(sa.String()), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("news_flows", "stop_words")
    op.drop_column("news_flows", "keywords")

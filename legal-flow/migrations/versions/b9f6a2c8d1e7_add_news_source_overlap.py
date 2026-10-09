"""Счётчик совпадения черновика с оригиналом (только числа, текст оригинала не хранится)

Revision ID: b9f6a2c8d1e7
Revises: a8e5f1b7c2d9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9f6a2c8d1e7"
down_revision: Union[str, None] = "a8e5f1b7c2d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("news_items", sa.Column("source_overlap", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("news_items", "source_overlap")

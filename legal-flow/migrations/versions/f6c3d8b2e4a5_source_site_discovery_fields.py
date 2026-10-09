"""Способ сбора и статус для сайтов, добавленных пользователем

Revision ID: f6c3d8b2e4a5
Revises: e5b2c7a1d3f4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6c3d8b2e4a5"
down_revision: Union[str, None] = "e5b2c7a1d3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("source_sites", sa.Column("kind", sa.String(20), nullable=True))
    op.add_column("source_sites", sa.Column("list_url", sa.String(2000), nullable=True))
    op.add_column("source_sites", sa.Column("status", sa.String(20), nullable=False, server_default="ready"))
    op.add_column("source_sites", sa.Column("status_note", sa.Text(), nullable=False, server_default=""))
    op.add_column("source_sites", sa.Column("rights_confirmed_by", sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("source_sites", "rights_confirmed_by")
    op.drop_column("source_sites", "status_note")
    op.drop_column("source_sites", "status")
    op.drop_column("source_sites", "list_url")
    op.drop_column("source_sites", "kind")

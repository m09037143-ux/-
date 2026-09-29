"""add news_flows.last_scan_at for schedule-based auto scans

Revision ID: c1a9f2d7e5b3
Revises: a23b06f68438
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c1a9f2d7e5b3"
down_revision: Union[str, None] = "a23b06f68438"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("news_flows", sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("news_flows", "last_scan_at")

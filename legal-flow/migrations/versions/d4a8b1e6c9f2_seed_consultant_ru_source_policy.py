"""seed consultant.ru source policy

Revision ID: d4a8b1e6c9f2
Revises: c1a9f2d7e5b3
Create Date: 2026-10-09

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = "d4a8b1e6c9f2"
down_revision: Union[str, None] = "c1a9f2d7e5b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

source_policies_table = sa.table(
    "source_policies",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("domain", sa.String),
    sa.column("material_path", sa.String),
    sa.column("reviewed_at", sa.DateTime(timezone=True)),
    sa.column("basis", sa.Text),
    sa.column("responsible", sa.String),
    sa.column("allowed_actions", ARRAY(sa.String)),
    sa.column("attribution_required", sa.Boolean),
    sa.column("attribution_text", sa.Text),
)


def upgrade() -> None:
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        source_policies_table,
        [
            {
                "id": uuid.uuid4(),
                "domain": "consultant.ru",
                "material_path": "",
                "reviewed_at": now,
                "basis": (
                    "Публично доступные новостные сообщения о правовых событиях; факты "
                    "сообщения используются как источник фактов (ТЗ §3). Авторская аналитика и "
                    "полнотекстовые материалы НЕ считаются свободными к копированию — вынесены "
                    "за пределы разрешённых действий до отдельного согласования."
                ),
                "responsible": "platform_admin",
                "allowed_actions": ["discover", "fetch", "extract_facts", "temporary_store"],
                "attribution_required": False,
                "attribution_text": "",
            }
        ],
    )


def downgrade() -> None:
    op.execute(source_policies_table.delete().where(source_policies_table.c.domain == "consultant.ru"))

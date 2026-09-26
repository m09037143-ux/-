"""seed plans and default source policies

Revision ID: a23b06f68438
Revises: 2bf4c67cb1b1
Create Date: 2026-09-26

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

from alembic import op

revision: str = "a23b06f68438"
down_revision: Union[str, None] = "2bf4c67cb1b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

plans_table = sa.table(
    "plans",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("price_rub", sa.Integer),
    sa.column("limits", sa.JSON),
)

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

PLANS = [
    {
        "code": "start",
        "name": "Старт",
        "price_rub": 1490,
        "limits": {
            "max_flows": 1,
            "max_sources_per_flow": 3,
            "max_news_per_run": 3,
            "manual_or_daily_schedule_only": True,
        },
    },
    {
        "code": "practice",
        "name": "Практика",
        "price_rub": 3990,
        "limits": {
            "max_flows": 5,
            "max_sources_per_flow": 15,
            "max_news_per_run": 10,
            "manual_or_daily_schedule_only": False,
        },
    },
    {
        "code": "editorial",
        "name": "Редакция",
        "price_rub": 8990,
        "limits": {
            "max_flows": 20,
            "max_sources_per_flow": 50,
            "max_news_per_run": 30,
            "manual_or_daily_schedule_only": False,
            # NOTE: точные лимиты «Редакции» не согласованы заказчиком (см. ТЗ §7, §15).
            "needs_customer_decision": True,
        },
    },
]

# Начальные кандидаты источников обнаружения из ТЗ §8. Разрешено лишь discover/
# fetch/extract_facts/temporary_store — НЕ retain_full_text/reuse_text/send_to_llm
# полного текста и НЕ publish без отдельной проверки редактором по каждому материалу.
DEFAULT_POLICIES = [
    {
        "domain": "garant.ru",
        "basis": "Публично доступные новостные сообщения о правовых событиях; факты "
        "сообщения используются как источник фактов (ТЗ §3). Авторская аналитика и "
        "полнотекстовые материалы НЕ считаются свободными к копированию — вынесены "
        "за пределы разрешённых действий до отдельного согласования.",
        "responsible": "platform_admin",
        "allowed_actions": ["discover", "fetch", "extract_facts", "temporary_store"],
        "attribution_required": False,
    },
    {
        "domain": "pravo.ru",
        "basis": "Публично доступные новостные сообщения о правовых событиях; факты "
        "сообщения используются как источник фактов (ТЗ §3). Условия автоматизированного "
        "доступа и переработки текста не согласованы — доступны только обнаружение и "
        "извлечение фактов.",
        "responsible": "platform_admin",
        "allowed_actions": ["discover", "fetch", "extract_facts", "temporary_store"],
        "attribution_required": False,
    },
]


def upgrade() -> None:
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        plans_table,
        [{"id": uuid.uuid4(), **p} for p in PLANS],
    )
    op.bulk_insert(
        source_policies_table,
        [
            {
                "id": uuid.uuid4(),
                "domain": p["domain"],
                "material_path": "",
                "reviewed_at": now,
                "basis": p["basis"],
                "responsible": p["responsible"],
                "allowed_actions": p["allowed_actions"],
                "attribution_required": p["attribution_required"],
                "attribution_text": "",
            }
            for p in DEFAULT_POLICIES
        ],
    )


def downgrade() -> None:
    op.execute(source_policies_table.delete().where(
        source_policies_table.c.domain.in_(["garant.ru", "pravo.ru"])
    ))
    op.execute(plans_table.delete().where(
        plans_table.c.code.in_(["start", "practice", "editorial"])
    ))

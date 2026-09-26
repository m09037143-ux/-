import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FactPassport, NewsItem
from app.models.enums import FactStatus
from app.providers.yandex_gpt import YandexGPTPro51Provider
from app.services import news_service
from app.services.activity_service import log_activity

_provider = YandexGPTPro51Provider()


async def generate_ai_draft(db: AsyncSession, *, news: NewsItem, expected_version: int, user_id: uuid.UUID) -> dict:
    """Runs stages 1-3 (relevance → fact passport → independent draft) and stores the
    result exactly like a manual edit would: as an unreviewed draft. Nothing here ever
    sets official_reviewed/facts_reviewed or advances past NEEDS_REVIEW — a human editor
    still has to check it (ТЗ §10: 'самокритика той же модели не является независимой
    юридической проверкой', and only a human review can set those flags)."""
    relevance = await _provider.check_relevance(
        db, workspace_id=news.workspace_id, news_item_id=news.id, title=news.title, theme=news.title
    )
    facts = await _provider.build_fact_passport(
        db, workspace_id=news.workspace_id, news_item_id=news.id, source_fragment=news.discovery_original_fragment
    )
    facts_text = "\n".join(
        f"- {s.statement} [{s.status}] (фрагмент: {s.source_fragment})" for s in facts.statements
    )
    draft = await _provider.draft_independent_text(
        db, workspace_id=news.workspace_id, news_item_id=news.id, title=news.title, facts_text=facts_text
    )

    news = await news_service.update_facts(
        db, news=news, expected_version=expected_version, text=facts_text, overall_status=FactStatus.needs_review.value
    )
    news = await news_service.update_draft(
        db, news=news, expected_version=news.version, title=draft.title, text=draft.text, user_id=user_id
    )

    await log_activity(
        db, workspace_id=news.workspace_id, action="ai_draft_generated",
        details={"news_id": str(news.id), "is_relevant": relevance.is_relevant, "reasoning": relevance.reasoning},
    )
    return {
        "relevance": relevance.model_dump(),
        "fact_statements": [s.model_dump() for s in facts.statements],
        "news": await news_service.to_internal_out(db, news),
    }


async def run_critique(db: AsyncSession, *, news: NewsItem) -> dict:
    facts = await db.scalar(select(FactPassport).where(FactPassport.news_item_id == news.id))
    facts_text = facts.text if facts else ""

    result = await _provider.critique(
        db, workspace_id=news.workspace_id, news_item_id=news.id, text=news.text, facts_text=facts_text
    )
    await log_activity(
        db, workspace_id=news.workspace_id, action="ai_critique_run",
        details={"news_id": str(news.id), **result.model_dump()},
    )
    return result.model_dump()

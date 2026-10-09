import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import Discovery, FactPassport, NewsFlow, NewsItem
from app.models.enums import FactStatus
from app.providers.html_source import fetch_article_text
from app.providers.yandex_gpt import RelevanceResult, YandexGPTPro51Provider
from app.services import news_service
from app.services.activity_service import log_activity

logger = logging.getLogger("app.services.ai_service")

_provider = YandexGPTPro51Provider()


async def _check_relevance_informational(db: AsyncSession, *, news: NewsItem) -> RelevanceResult:
    """Только для журнала активности (ai_draft_generated) — ничто здесь не блокирует
    и не гейтит генерацию черновика на этом результате (фактический отбор по теме
    делается раньше, на этапе сбора — см. scan_service.process_scan_job). Поэтому
    отказ модели (не-JSON ответ — например, встроенный отказ отвечать на чувствительную
    тему — или бюджет/конфигурация) не должен ломать остальной черновик: ловим
    AppError и возвращаем нейтральный результат."""
    flow = await db.get(NewsFlow, news.flow_id)
    theme = flow.theme if flow else news.title
    try:
        return await _provider.check_relevance(
            db, workspace_id=news.workspace_id, news_item_id=news.id, title=news.title, theme=theme
        )
    except AppError as exc:
        logger.warning("news %s: relevance check failed (%s), черновик продолжает без неё: %s", news.id, exc.code, exc.message)
        return RelevanceResult(is_relevant=True, reasoning=f"проверка недоступна: {exc.code}")


async def _fact_source_fragment(db: AsyncSession, news: NewsItem) -> str:
    """Короткий фрагмент со страницы списка (discovery_original_fragment) — вход по
    умолчанию для паспорта фактов. Для материалов с реально поддержанных сайтов
    (HtmlSourceProvider) вместо него пробуем прочитать статью целиком по ссылке —
    паспорт фактов получается полнее, чем по одному анонсу. Текст статьи нигде не
    сохраняется (source_policies разрешают fetch/extract_facts/temporary_store, но
    не retain_full_text) — используется только как вход этого одного вызова LLM.
    При любой ошибке (сайт недоступен, блокировка, разметка изменилась) молча
    откатываемся на короткий фрагмент — генерация черновика не должна падать
    из-за того, что не получилось дочитать статью."""
    if news.discovery_id is None:
        return news.discovery_original_fragment
    discovery = await db.get(Discovery, news.discovery_id)
    # Полный текст читаем только у материалов, найденных живым сбором (а не демо-фикстур).
    if discovery is None or (discovery.metadata_json or {}).get("label") != "LIVE_LISTING":
        return news.discovery_original_fragment
    full_text = await fetch_article_text(discovery.normalized_url, domain=news.discovery_domain)
    if not full_text:
        logger.info("news %s: full article fetch failed, falling back to short fragment", news.id)
        return news.discovery_original_fragment
    return full_text


async def generate_ai_draft(db: AsyncSession, *, news: NewsItem, expected_version: int, user_id: uuid.UUID) -> dict:
    """Runs stages 1-3 (relevance → fact passport → independent draft) and stores the
    result exactly like a manual edit would: as an unreviewed draft. Nothing here ever
    sets official_reviewed/facts_reviewed or advances past NEEDS_REVIEW — a human editor
    still has to check it (ТЗ §10: 'самокритика той же модели не является независимой
    юридической проверкой', and only a human review can set those flags)."""
    relevance = await _check_relevance_informational(db, news=news)
    source_fragment = await _fact_source_fragment(db, news)
    facts = await _provider.build_fact_passport(
        db, workspace_id=news.workspace_id, news_item_id=news.id, source_fragment=source_fragment
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
        details={
            "news_id": str(news.id), "is_relevant": relevance.is_relevant, "reasoning": relevance.reasoning,
            "models": {"relevance": relevance.model_used, "facts": facts.model_used, "draft": draft.model_used},
        },
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

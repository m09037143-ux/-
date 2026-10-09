import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import FactPassport, NewsItem, NewsVersion, OfficialDocument, EditorReview
from app.models.enums import FactStatus, NewsStatus, ReleaseMode
from app.schemas.news import FactPassportOut, NewsItemOut, OfficialDocumentOut
from app.services.activity_service import log_activity


async def get_owned_news(db: AsyncSession, *, workspace_id: uuid.UUID, news_id: uuid.UUID) -> NewsItem:
    news = await db.get(NewsItem, news_id)
    if news is None or news.workspace_id != workspace_id:
        raise AppError("VALIDATION_ERROR", "Материал не найден.")
    return news


def _check_version(news: NewsItem, expected_version: int) -> None:
    if news.version != expected_version:
        raise AppError(
            "VERSION_CONFLICT",
            "Материал изменён другим пользователем, обновите страницу и повторите правку.",
            details={"current_version": news.version},
        )


async def to_internal_out(db: AsyncSession, news: NewsItem) -> NewsItemOut:
    official = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == news.id))
    facts = await db.scalar(select(FactPassport).where(FactPassport.news_item_id == news.id))
    return NewsItemOut(
        id=news.id,
        flow_id=news.flow_id,
        title=news.title,
        text=news.text,
        status=news.status.value,
        release_mode=news.release_mode.value,
        discovery_domain=news.discovery_domain,
        discovery_original_fragment=news.discovery_original_fragment,
        official_reviewed=news.official_reviewed,
        facts_reviewed=news.facts_reviewed,
        official_document=OfficialDocumentOut(
            title=official.title, url=official.url, requisites=official.requisites,
            checked_at=official.checked_at, currency_notes=official.currency_notes,
            confirmation_limits=official.confirmation_limits,
        ) if official else None,
        fact_passport=FactPassportOut(text=facts.text, overall_status=facts.overall_status.value) if facts else None,
        attribution_owner=news.attribution_owner,
        attribution_text=news.attribution_text,
        version=news.version,
        rejected_reason=news.rejected_reason,
        source_overlap=news.source_overlap,
        created_at=news.created_at,
    )


async def mark_overlap_stale(db: AsyncSession, news: NewsItem) -> None:
    """Редактор поправил текст вручную — сохранённое совпадение с оригиналом уже не про этот текст."""
    if news.source_overlap and not news.source_overlap.get("stale"):
        news.source_overlap = {**news.source_overlap, "stale": True}
        await db.commit()


def _reset_review_flags_and_bump(news: NewsItem) -> None:
    news.official_reviewed = False
    news.facts_reviewed = False
    news.version += 1
    if news.status in (NewsStatus.READY_FOR_REVIEW, NewsStatus.APPROVED):
        news.status = NewsStatus.NEEDS_REVIEW


async def update_draft(
    db: AsyncSession, *, news: NewsItem, expected_version: int, title: str, text: str, user_id: uuid.UUID
) -> NewsItem:
    _check_version(news, expected_version)
    news.title = title
    news.text = text
    db.add(
        NewsVersion(
            news_item_id=news.id, version=news.version, title=title, text=text,
            edited_by_user_id=user_id, created_at=datetime.now(timezone.utc),
        )
    )
    if news.status in (NewsStatus.DISCOVERED, NewsStatus.NEEDS_SOURCE):
        news.status = NewsStatus.DRAFT
    _reset_review_flags_and_bump(news)
    await db.commit()
    await log_activity(db, workspace_id=news.workspace_id, action="news_draft_saved", details={"news_id": str(news.id)})
    return news


def _validate_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError("VALIDATION_ERROR", "Ссылка на официальный документ должна быть корректным http(s) URL.")


async def update_official_document(
    db: AsyncSession, *, news: NewsItem, expected_version: int, title: str, url: str,
    requisites: str, currency_notes: str, confirmation_limits: str,
) -> NewsItem:
    _check_version(news, expected_version)
    _validate_https_url(url)

    doc = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == news.id))
    if doc is None:
        doc = OfficialDocument(news_item_id=news.id)
        db.add(doc)
    doc.title = title
    doc.url = url
    doc.requisites = requisites
    doc.currency_notes = currency_notes
    doc.confirmation_limits = confirmation_limits
    # Внесение URL редактором — это НЕ подтверждение (ТЗ §9); отметка "проверено"
    # ставится отдельно через /review после того как редактор реально сверил документ.
    doc.checked_at = None

    if news.status in (NewsStatus.DISCOVERED, NewsStatus.NEEDS_SOURCE):
        news.status = NewsStatus.NEEDS_REVIEW
    _reset_review_flags_and_bump(news)
    await db.commit()
    await log_activity(db, workspace_id=news.workspace_id, action="official_document_saved", details={"news_id": str(news.id)})
    return news


async def update_facts(
    db: AsyncSession, *, news: NewsItem, expected_version: int, text: str, overall_status: str
) -> NewsItem:
    _check_version(news, expected_version)
    try:
        status_enum = FactStatus(overall_status)
    except ValueError:
        raise AppError("VALIDATION_ERROR", f"Некорректный статус паспорта фактов: {overall_status!r}")

    fp = await db.scalar(select(FactPassport).where(FactPassport.news_item_id == news.id))
    if fp is None:
        fp = FactPassport(news_item_id=news.id)
        db.add(fp)
    fp.text = text
    fp.overall_status = status_enum

    if news.status in (NewsStatus.DISCOVERED, NewsStatus.NEEDS_SOURCE):
        news.status = NewsStatus.NEEDS_REVIEW
    _reset_review_flags_and_bump(news)
    await db.commit()
    await log_activity(db, workspace_id=news.workspace_id, action="fact_passport_saved", details={"news_id": str(news.id)})
    return news


async def submit_review(
    db: AsyncSession, *, news: NewsItem, reviewer_id: uuid.UUID,
    official_reviewed: bool, facts_reviewed: bool, note: str,
) -> NewsItem:
    doc = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == news.id))
    if doc is None or not doc.url or not doc.title:
        raise AppError("OFFICIAL_SOURCE_MISSING", "Сначала укажите официальный документ и его ссылку.")

    now = datetime.now(timezone.utc)
    if official_reviewed and doc.checked_at is None:
        doc.checked_at = now

    news.official_reviewed = official_reviewed
    news.facts_reviewed = facts_reviewed
    news.status = NewsStatus.READY_FOR_REVIEW if (official_reviewed and facts_reviewed) else NewsStatus.NEEDS_REVIEW

    db.add(
        EditorReview(
            news_item_id=news.id, reviewer_user_id=reviewer_id,
            official_reviewed=official_reviewed, facts_reviewed=facts_reviewed,
            note=note, created_at=now,
        )
    )
    await db.commit()
    await log_activity(
        db, workspace_id=news.workspace_id, action="editor_review_recorded",
        details={"news_id": str(news.id), "official_reviewed": official_reviewed, "facts_reviewed": facts_reviewed},
    )
    return news


async def approve(db: AsyncSession, *, news: NewsItem) -> NewsItem:
    doc = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == news.id))
    if doc is None or not doc.url or not doc.title:
        raise AppError("OFFICIAL_SOURCE_MISSING", "Официальный документ не указан.")
    if not news.official_reviewed or not news.facts_reviewed:
        raise AppError("EDITOR_REVIEW_REQUIRED", "Требуется отметка редактора о проверке документа и фактов.")
    if news.status != NewsStatus.READY_FOR_REVIEW:
        raise AppError("EDITOR_REVIEW_REQUIRED", "Материал должен быть в статусе «готов к проверке».")

    news.status = NewsStatus.APPROVED
    news.approved_at = datetime.now(timezone.utc)
    await db.commit()
    await log_activity(db, workspace_id=news.workspace_id, action="news_approved", details={"news_id": str(news.id)})
    return news


async def publish(db: AsyncSession, *, news: NewsItem) -> NewsItem:
    if news.status != NewsStatus.APPROVED:
        raise AppError("EDITOR_REVIEW_REQUIRED", "Публикация возможна только из статуса «утверждено».")
    if news.release_mode == ReleaseMode.ATTRIBUTED_REUSE and not (news.attribution_owner and news.attribution_text):
        raise AppError("ATTRIBUTION_REQUIRED", "Для режима ATTRIBUTED_REUSE обязательна атрибуция источника.")

    news.status = NewsStatus.PUBLISHED
    news.published_at = datetime.now(timezone.utc)
    await db.commit()
    await log_activity(db, workspace_id=news.workspace_id, action="news_published", details={"news_id": str(news.id)})
    return news


async def reject(db: AsyncSession, *, news: NewsItem, reason: str) -> NewsItem:
    if news.status == NewsStatus.PUBLISHED:
        raise AppError("VALIDATION_ERROR", "Опубликованный материал нельзя отклонить.")
    news.status = NewsStatus.REJECTED
    news.rejected_reason = reason
    await db.commit()
    await log_activity(db, workspace_id=news.workspace_id, action="news_rejected", details={"news_id": str(news.id), "reason": reason})
    return news

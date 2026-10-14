import hashlib
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import Export, NewsItem, OfficialDocument
from app.models.enums import NewsStatus, ReleaseMode
from app.services.activity_service import log_activity

XML_SCHEMA_VERSION = "pravovoy-potok-news-1"  # согласование точной схемы с заказчиком — открытый вопрос (ТЗ §15)


def _xml_for_item(news: NewsItem, doc: OfficialDocument | None) -> str:
    """INDEPENDENT_FACT_REPORT никогда не несёт сайт обнаружения или внутренний
    фрагмент (ТЗ §11) — они физически отсутствуют в аргументах этой функции."""
    parts = [
        "  <item>",
        f"    <id>{escape(str(news.id))}</id>",
        f"    <title>{escape(news.title)}</title>",
        f"    <text>{escape(news.text)}</text>",
        f"    <release_mode>{escape(news.release_mode.value)}</release_mode>",
    ]
    if doc:
        parts.append(f"    <official_document_title>{escape(doc.title)}</official_document_title>")
        parts.append(f"    <official_document_url>{escape(doc.url)}</official_document_url>")
        parts.append(f"    <official_document_requisites>{escape(doc.requisites)}</official_document_requisites>")
    if news.release_mode == ReleaseMode.ATTRIBUTED_REUSE:
        parts.append(f"    <attribution_owner>{escape(news.attribution_owner)}</attribution_owner>")
        parts.append(f"    <attribution_text>{escape(news.attribution_text)}</attribution_text>")
    parts.append(f"    <published_at>{escape(news.published_at.isoformat() if news.published_at else '')}</published_at>")
    parts.append("  </item>")
    return "\n".join(parts)


def render_xml(items: list[tuple[NewsItem, OfficialDocument | None]]) -> str:
    body = "\n".join(_xml_for_item(n, d) for n, d in items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<news schema="{XML_SCHEMA_VERSION}">\n{body}\n</news>\n'
    )


DEFAULT_LATEST_COUNT = 10
MAX_EXPORT_ITEMS = 500


def _published_moment():
    """Момент «подготовки» материала: дата публикации (а если её нет — дата создания)."""
    return func.coalesce(NewsItem.published_at, NewsItem.created_at)


async def list_candidates(db: AsyncSession, *, workspace_id: uuid.UUID, limit: int = 200) -> list[NewsItem]:
    """Опубликованные материалы, новые сверху — из них пользователь выбирает, что выгрузить."""
    stmt = (
        select(NewsItem)
        .where(NewsItem.workspace_id == workspace_id, NewsItem.status == NewsStatus.PUBLISHED)
        .order_by(_published_moment().desc(), NewsItem.id.desc())
        .limit(limit)
    )
    return list((await db.scalars(stmt)).all())


async def _select_items(
    db: AsyncSession, *, workspace_id: uuid.UUID, mode: str, limit: int, news_item_ids: list[uuid.UUID] | None
) -> list[NewsItem]:
    base = select(NewsItem).where(NewsItem.workspace_id == workspace_id, NewsItem.status == NewsStatus.PUBLISHED)
    if news_item_ids:
        wanted = list(dict.fromkeys(news_item_ids))  # без повторов, порядок не важен — ниже сортируем по дате
        rows = list((await db.scalars(base.where(NewsItem.id.in_(wanted)))).all())
        if len(rows) != len(wanted):
            raise AppError(
                "VALIDATION_ERROR",
                "Часть выбранных материалов не найдена или ещё не опубликована — обновите страницу и выберите заново.",
            )
        rows.sort(key=lambda n: (n.published_at or n.created_at, str(n.id)), reverse=True)
        return rows
    if mode == "selected":
        raise AppError("VALIDATION_ERROR", "Отметьте хотя бы один материал для экспорта.")
    stmt = base.order_by(_published_moment().desc(), NewsItem.id.desc())
    if mode == "latest":
        stmt = stmt.limit(limit or DEFAULT_LATEST_COUNT)
    else:  # all
        stmt = stmt.limit(MAX_EXPORT_ITEMS)
    return list((await db.scalars(stmt)).all())


async def create_export(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    filename: str,
    mode: str = "latest",
    limit: int = DEFAULT_LATEST_COUNT,
    news_item_ids: list[uuid.UUID] | None = None,
) -> Export:
    """mode: latest — последние `limit` опубликованных (по умолчанию), selected — материалы из `news_item_ids`,
    all — все опубликованные. Если переданы news_item_ids, выгружаются именно они."""
    if not filename.endswith(".xml"):
        filename += ".xml"

    news_items = await _select_items(db, workspace_id=workspace_id, mode=mode, limit=limit, news_item_ids=news_item_ids)
    if not news_items:
        raise AppError("VALIDATION_ERROR", "Нет утверждённых и опубликованных материалов для экспорта.")

    pairs = []
    for n in news_items:
        doc = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == n.id))
        pairs.append((n, doc))

    xml_content = render_xml(pairs)
    content_hash = hashlib.sha256(xml_content.encode("utf-8")).hexdigest()

    latest = await db.scalar(
        select(Export).where(Export.workspace_id == workspace_id).order_by(Export.created_at.desc()).limit(1)
    )
    if latest is not None and latest.content_hash == content_hash:
        # Идемпотентность: тот же набор опубликованных материалов не создаёт вторую запись.
        return latest

    export = Export(
        workspace_id=workspace_id,
        filename=filename,
        content=xml_content,
        content_hash=content_hash,
        news_item_ids=[str(n.id) for n, _ in pairs],
        created_at=datetime.now(timezone.utc),
    )
    db.add(export)
    await db.commit()
    await log_activity(
        db, workspace_id=workspace_id, action="export_generated",
        details={"export_id": str(export.id), "news_count": len(pairs), "status": "NOT_SENT"},
    )
    return export

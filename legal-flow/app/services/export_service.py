import hashlib
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from sqlalchemy import select
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


async def create_export(db: AsyncSession, *, workspace_id: uuid.UUID, filename: str) -> Export:
    if not filename.endswith(".xml"):
        filename += ".xml"

    stmt = select(NewsItem).where(NewsItem.workspace_id == workspace_id, NewsItem.status == NewsStatus.PUBLISHED)
    news_items = (await db.scalars(stmt)).all()
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

"""Публичная лента опубликованных материалов рабочего пространства — для API-ключей.
Состав полей тот же, что у XML-экспорта: сайт обнаружения и внутренний фрагмент в ленту
никогда не попадают (ТЗ §11), атрибуция — только для ATTRIBUTED_REUSE."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsItem, OfficialDocument
from app.models.enums import NewsStatus, ReleaseMode


def feed_item(news: NewsItem, doc: OfficialDocument | None) -> dict:
    item = {
        "id": str(news.id),
        "title": news.title,
        "text": news.text,
        "release_mode": news.release_mode.value,
        "official_document": (
            {"title": doc.title, "url": doc.url, "requisites": doc.requisites} if doc else None
        ),
        "attribution": (
            {"owner": news.attribution_owner, "text": news.attribution_text}
            if news.release_mode == ReleaseMode.ATTRIBUTED_REUSE
            else None
        ),
        "published_at": news.published_at.isoformat() if news.published_at else None,
    }
    return item


async def published_items(
    db: AsyncSession, *, workspace_id: uuid.UUID, since: datetime | None, limit: int, offset: int = 0
) -> tuple[list[tuple[NewsItem, OfficialDocument | None]], bool]:
    """Опубликованные материалы по возрастанию даты публикации (удобно опрашивать с since=<последняя
    полученная дата>). Возвращает (пары, есть_ли_ещё)."""
    moment = func.coalesce(NewsItem.published_at, NewsItem.created_at)
    stmt = select(NewsItem).where(NewsItem.workspace_id == workspace_id, NewsItem.status == NewsStatus.PUBLISHED)
    if since is not None:
        stmt = stmt.where(moment > since)
    stmt = stmt.order_by(moment.asc(), NewsItem.id.asc()).limit(limit + 1).offset(offset)
    rows = (await db.scalars(stmt)).all()
    has_more = len(rows) > limit
    pairs = []
    for news in rows[:limit]:
        doc = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == news.id))
        pairs.append((news, doc))
    return pairs, has_more

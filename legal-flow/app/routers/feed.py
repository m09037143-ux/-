import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_api_key
from app.errors import AppError
from app.models import ApiKey, NewsItem, OfficialDocument
from app.models.enums import NewsStatus
from app.schemas.exports import FeedItemOut, FeedOut
from app.services import feed_service
from app.services.export_service import render_xml

router = APIRouter(prefix="/api/v1", tags=["feed"])


@router.get("/feed", response_model=FeedOut)
async def feed_json(
    since: datetime | None = Query(default=None, description="Только материалы, опубликованные позже этого момента (ISO 8601)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Опубликованные материалы вашего рабочего пространства (JSON). Доступ — по API-ключу, только чтение."""
    pairs, has_more = await feed_service.published_items(db, workspace_id=key.workspace_id, since=since, limit=limit, offset=offset)
    items = [FeedItemOut(**feed_service.feed_item(n, d)) for n, d in pairs]
    return FeedOut(items=items, count=len(items), has_more=has_more)


@router.get("/feed.xml")
async def feed_xml(
    response: Response,
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """То же, что /feed, в XML (формат экспорта «pravovoy-potok-news-1»). Есть ли ещё — в заголовке X-Has-More."""
    pairs, has_more = await feed_service.published_items(db, workspace_id=key.workspace_id, since=since, limit=limit, offset=offset)
    return Response(
        content=render_xml(pairs),
        media_type="application/xml; charset=utf-8",
        headers={"X-Has-More": "true" if has_more else "false"},
    )


@router.get("/feed/{news_id}", response_model=FeedItemOut)
async def feed_item(news_id: uuid.UUID, key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    news = await db.get(NewsItem, news_id)
    if news is None or news.workspace_id != key.workspace_id or news.status != NewsStatus.PUBLISHED:
        raise AppError("NOT_FOUND", "Опубликованный материал не найден.")
    doc = await db.scalar(select(OfficialDocument).where(OfficialDocument.news_item_id == news.id))
    return FeedItemOut(**feed_service.feed_item(news, doc))

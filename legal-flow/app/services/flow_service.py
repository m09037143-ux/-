import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import NewsFlow, SourceSite
from app.services.trial_service import get_effective_plan

_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.IGNORECASE)


def validate_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if not _DOMAIN_RE.match(domain):
        raise AppError("VALIDATION_ERROR", f"Некорректный домен: {domain!r}")
    return domain


async def create_flow(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    theme: str,
    schedule_period: str,
    schedule_time: str,
    news_limit_per_run: int,
    domains: list[str],
    keywords: list[str] | None = None,
    stop_words: list[str] | None = None,
) -> NewsFlow:
    plan = await get_effective_plan(db, workspace_id)
    limits = plan.limits if plan else {}

    flow_count = await db.scalar(select(func.count()).select_from(NewsFlow).where(NewsFlow.workspace_id == workspace_id))
    max_flows = limits.get("max_flows", 1)
    if flow_count >= max_flows:
        raise AppError("PLAN_LIMIT", f"Тариф допускает не более {max_flows} потоков.")

    max_sources = limits.get("max_sources_per_flow", 3)
    if len(domains) > max_sources:
        raise AppError("PLAN_LIMIT", f"Тариф допускает не более {max_sources} сайтов на поток.")

    max_news = limits.get("max_news_per_run", 3)
    if news_limit_per_run > max_news:
        raise AppError("PLAN_LIMIT", f"Тариф допускает не более {max_news} новостей за запуск.")

    if limits.get("manual_or_daily_schedule_only") and schedule_period not in ("Вручную", "Ежедневно"):
        raise AppError("PLAN_LIMIT", "Тариф допускает только ручной или ежедневный запуск.")

    flow = NewsFlow(
        workspace_id=workspace_id,
        name=name,
        theme=theme,
        schedule_period=schedule_period,
        schedule_time=schedule_time,
        news_limit_per_run=news_limit_per_run,
        keywords=list(keywords or []),
        stop_words=list(stop_words or []),
    )
    db.add(flow)
    await db.flush()

    for d in domains:
        db.add(SourceSite(flow_id=flow.id, domain=validate_domain(d), active=True))

    await db.commit()
    return flow


async def add_source(db: AsyncSession, *, flow: NewsFlow, workspace_id: uuid.UUID, domain: str) -> SourceSite:
    plan = await get_effective_plan(db, workspace_id)
    max_sources = (plan.limits if plan else {}).get("max_sources_per_flow", 3)
    count = await db.scalar(select(func.count()).select_from(SourceSite).where(SourceSite.flow_id == flow.id))
    if count >= max_sources:
        raise AppError("PLAN_LIMIT", f"Тариф допускает не более {max_sources} сайтов на поток.")

    domain = validate_domain(domain)
    existing = await db.scalar(select(SourceSite).where(SourceSite.flow_id == flow.id, SourceSite.domain == domain))
    if existing is not None:
        raise AppError("DUPLICATE", "Такой сайт уже добавлен в поток.")

    site = SourceSite(flow_id=flow.id, domain=domain, active=True)
    db.add(site)
    await db.commit()
    return site

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import NewsFlow, SourceSite
from app.services.source_onboarding import OnboardResult, SourceRejected, onboard_source
from app.services.trial_service import get_effective_plan

_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.IGNORECASE)


def validate_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if not _DOMAIN_RE.match(domain):
        raise AppError("VALIDATION_ERROR", f"Некорректный домен: {domain!r}")
    return domain


async def _onboard(db: AsyncSession, *, domain: str, confirmed: bool, confirmed_by: str) -> OnboardResult:
    try:
        return await onboard_source(db, domain=domain, confirmed=confirmed, confirmed_by=confirmed_by)
    except SourceRejected as exc:
        raise AppError("VALIDATION_ERROR", f"{domain}: {exc.message}") from exc


def apply_onboarding(site: SourceSite, result: OnboardResult, *, confirmed: bool, confirmed_by: str) -> None:
    site.kind = result.kind
    site.list_url = result.list_url
    site.status = result.status
    site.status_note = result.note
    if confirmed:
        site.rights_confirmed_by = confirmed_by


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
    rights_confirmed: bool = False,
    confirmed_by: str = "",
    auto_draft: bool = True,
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

    # Подключение сайтов (проверка robots.txt, поиск ленты) — до создания потока: отказ по одному
    # из сайтов не должен оставлять полусозданный поток.
    clean_domains = [validate_domain(d) for d in domains]
    if len(set(clean_domains)) != len(clean_domains):
        raise AppError("DUPLICATE", "В списке есть повторяющиеся сайты.")
    onboarded = [
        await _onboard(db, domain=d, confirmed=rights_confirmed, confirmed_by=confirmed_by) for d in clean_domains
    ]

    flow = NewsFlow(
        workspace_id=workspace_id,
        name=name,
        theme=theme,
        schedule_period=schedule_period,
        schedule_time=schedule_time,
        news_limit_per_run=news_limit_per_run,
        auto_draft=auto_draft,
        keywords=list(keywords or []),
        stop_words=list(stop_words or []),
    )
    db.add(flow)
    await db.flush()

    for d, result in zip(clean_domains, onboarded):
        site = SourceSite(flow_id=flow.id, domain=d, active=True)
        apply_onboarding(site, result, confirmed=rights_confirmed, confirmed_by=confirmed_by)
        db.add(site)

    await db.commit()
    return flow


async def add_source(
    db: AsyncSession, *, flow: NewsFlow, workspace_id: uuid.UUID, domain: str,
    rights_confirmed: bool = False, confirmed_by: str = "",
) -> SourceSite:
    plan = await get_effective_plan(db, workspace_id)
    max_sources = (plan.limits if plan else {}).get("max_sources_per_flow", 3)
    count = await db.scalar(select(func.count()).select_from(SourceSite).where(SourceSite.flow_id == flow.id))
    if count >= max_sources:
        raise AppError("PLAN_LIMIT", f"Тариф допускает не более {max_sources} сайтов на поток.")

    domain = validate_domain(domain)
    existing = await db.scalar(select(SourceSite).where(SourceSite.flow_id == flow.id, SourceSite.domain == domain))
    if existing is not None:
        raise AppError("DUPLICATE", "Такой сайт уже добавлен в поток.")

    result = await _onboard(db, domain=domain, confirmed=rights_confirmed, confirmed_by=confirmed_by)
    site = SourceSite(flow_id=flow.id, domain=domain, active=True)
    apply_onboarding(site, result, confirmed=rights_confirmed, confirmed_by=confirmed_by)
    db.add(site)
    await db.commit()
    return site


async def change_source_domain(
    db: AsyncSession, *, site: SourceSite, domain: str, rights_confirmed: bool = False, confirmed_by: str = ""
) -> None:
    """Смена адреса — это другой сайт: заново проверяем robots.txt и способ сбора."""
    domain = validate_domain(domain)
    # Тот же адрес пропускаем, только если сайт уже подключён и подтверждение не передаётся;
    # иначе (сайт добавлен до появления автоподключения или без способа сбора) — подключаем заново.
    if domain == site.domain and not rights_confirmed and site.status == "ready" and site.kind:
        return
    result = await _onboard(db, domain=domain, confirmed=rights_confirmed, confirmed_by=confirmed_by)
    site.domain = domain
    site.rights_confirmed_by = ""
    apply_onboarding(site, result, confirmed=rights_confirmed, confirmed_by=confirmed_by)

import logging
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import Discovery, NewsFlow, NewsItem, ScanJob, SourcePolicy, SourceSite, Story
from app.models.enums import NewsStatus, ScanJobStatus
from app.providers.base import CandidateItem
from app.providers.fixture_source import FixtureSourceProvider
from app.providers.html_source import SUPPORTED_DOMAINS as HTML_SUPPORTED_DOMAINS, HtmlSourceProvider, SourceHint
from app.providers.yandex_gpt import YandexGPTPro51Provider
from app.services.activity_service import log_activity
from app.services.source_onboarding import SourceRejected, onboard_source

logger = logging.getLogger("app.services.scan_service")

PROVIDERS = {"fixture": FixtureSourceProvider(), "html": HtmlSourceProvider()}
_llm_provider = YandexGPTPro51Provider()

MOSCOW_TZ = timezone(timedelta(hours=3))


def theme_for_llm(flow: NewsFlow) -> str:
    """Тема для ИИ-проверки релевантности: тема потока + ключевые слова и стоп-слова
    (чтобы модель учитывала то же, что и предфильтр)."""
    parts = [flow.theme]
    if flow.keywords:
        parts.append("Ключевые слова: " + ", ".join(flow.keywords))
    if flow.stop_words:
        parts.append("Не относится к теме, если речь о: " + ", ".join(flow.stop_words))
    return ". ".join(parts)


async def provider_name_for_flow(db: AsyncSession, flow_id: uuid.UUID) -> str:
    """"html" (реальный сбор) только если SOURCE_FIXTURE_MODE явно выключен (тот же
    принцип, что и LLM_FIXTURE_MODE — никогда не выбирается молча по догадке) и в потоке
    есть хотя бы один активный сайт. Сайты без готового способа сбора провайдер просто
    пропускает, а причина видна в «Источниках» и журнале активности."""
    if get_settings().source_fixture_mode:
        return "fixture"
    has_active = await db.scalar(select(SourceSite.id).where(SourceSite.flow_id == flow_id, SourceSite.active.is_(True)).limit(1))
    return "html" if has_active else "fixture"


async def get_or_create_scan_job(
    db: AsyncSession, *, flow_id: uuid.UUID, idempotency_key: str, provider_name: str
) -> ScanJob:
    """Shared by the manual 'Приступить к сбору' endpoint and the schedule-based
    auto-run below — same idempotency-key dedupe either way (ТЗ §9)."""
    existing = await db.scalar(
        select(ScanJob).where(ScanJob.flow_id == flow_id, ScanJob.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    job = ScanJob(flow_id=flow_id, idempotency_key=idempotency_key, status=ScanJobStatus.pending, provider_name=provider_name)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def _is_schedule_due(schedule_period: str, schedule_time: str, last_scan_at: datetime | None, now_moscow: datetime) -> bool:
    if schedule_period == "Вручную":
        return False
    try:
        due_hour, due_minute = (int(p) for p in schedule_time.split(":"))
    except ValueError:
        return False
    if now_moscow.time() < time(due_hour, due_minute):
        return False
    if schedule_period == "По рабочим дням" and now_moscow.weekday() >= 5:
        return False
    if last_scan_at is None:
        return True
    last_scan_moscow = last_scan_at.astimezone(MOSCOW_TZ)
    if schedule_period == "Раз в неделю":
        return (now_moscow.date() - last_scan_moscow.date()).days >= 7
    return now_moscow.date() > last_scan_moscow.date()


async def enqueue_due_scheduled_scans(db: AsyncSession) -> list[uuid.UUID]:
    """Called periodically by the worker (ТЗ: сбор день в день / с периодичностью,
    без нажатия кнопки). For each flow whose schedule is due, enqueues one scan job
    (reusing the pending one if a previous check already created it today) and
    advances last_scan_at so the same day never fires twice."""
    now_moscow = datetime.now(MOSCOW_TZ)
    flows = (await db.scalars(select(NewsFlow).where(NewsFlow.schedule_period != "Вручную"))).all()
    created_job_ids: list[uuid.UUID] = []
    for flow in flows:
        if not _is_schedule_due(flow.schedule_period, flow.schedule_time, flow.last_scan_at, now_moscow):
            continue
        key = f"auto-schedule-{now_moscow.date().isoformat()}"
        provider_name = await provider_name_for_flow(db, flow.id)
        job = await get_or_create_scan_job(db, flow_id=flow.id, idempotency_key=key, provider_name=provider_name)
        flow.last_scan_at = now_moscow
        await db.commit()
        created_job_ids.append(job.id)
    return created_job_ids


async def _policy_cleared_domains(db: AsyncSession, domains: list[str]) -> tuple[list[str], list[str]]:
    """Splits domains into those where 'fetch' is allowed vs. those needing review.
    One allowed action never implies another (ТЗ §3): discover alone is not enough."""
    if not domains:
        return [], []
    rows = (await db.scalars(select(SourcePolicy).where(SourcePolicy.domain.in_(domains)))).all()
    by_domain = {r.domain: r for r in rows}
    cleared, blocked = [], []
    for d in domains:
        policy = by_domain.get(d)
        if policy is not None and "fetch" in policy.allowed_actions and "extract_facts" in policy.allowed_actions:
            cleared.append(d)
        else:
            blocked.append(d)
    return cleared, blocked


async def _is_duplicate(db: AsyncSession, *, workspace_id: uuid.UUID, item: CandidateItem) -> Discovery | None:
    match_conditions = [Discovery.normalized_url == item.normalized_url, Discovery.story_key == item.story_key]
    if item.content_hash:
        match_conditions.append(Discovery.content_hash == item.content_hash)
    stmt = select(Discovery).where(
        Discovery.workspace_id == workspace_id,
        Discovery.is_duplicate.is_(False),
        or_(*match_conditions),
    )
    return await db.scalar(stmt.limit(1))


async def _prepare_generic_sources(db: AsyncSession, flow: NewsFlow, sites: list[SourceSite]) -> None:
    """Сайты, добавленные пользователем, у которых способ сбора ещё не определён (сайт был
    недоступен при добавлении) или ранее не нашёлся, определяем заново — с сохранённым
    подтверждением права. Ошибка одного сайта не мешает остальным."""
    for site in sites:
        if site.domain in HTML_SUPPORTED_DOMAINS:
            continue
        if site.kind in ("feed", "sitemap", "html") and site.status == "ready":
            continue
        try:
            result = await onboard_source(
                db, domain=site.domain, confirmed=bool(site.rights_confirmed_by), confirmed_by=site.rights_confirmed_by
            )
            site.kind, site.list_url, site.status, site.status_note = result.kind, result.list_url, result.status, result.note
        except SourceRejected as exc:
            site.status, site.status_note = "unavailable", exc.message
        except Exception:  # сетевой сбой на одном сайте не должен ронять весь сбор
            logger.exception("повторное подключение сайта %s не удалось", site.domain)
            site.status, site.status_note = "unavailable", "Не удалось проверить сайт — попробуем при следующем сборе."
        await db.commit()


async def process_scan_job(db: AsyncSession, job_id: uuid.UUID) -> ScanJob:
    """Idempotent: a job already 'done' or 'failed' is returned as-is, never reprocessed
    (ТЗ §9 — 'повторное выполнение задания не должно порождать новые редакционные
    карточки и затраты на модель'). This is what app/worker/runner.py calls for each
    queued job id; tests call it directly to exercise the same code path."""
    job = await db.get(ScanJob, job_id)
    if job is None:
        raise ValueError(f"scan job {job_id} not found")
    if job.status != ScanJobStatus.pending:
        return job

    job.status = ScanJobStatus.running
    await db.commit()

    flow = await db.get(NewsFlow, job.flow_id)
    sites = (
        await db.scalars(select(SourceSite).where(SourceSite.flow_id == flow.id, SourceSite.active.is_(True)))
    ).all()
    if job.provider_name == "html":
        await _prepare_generic_sources(db, flow, list(sites))
    all_domains = [s.domain for s in sites]
    cleared_domains, blocked_domains = await _policy_cleared_domains(db, all_domains)
    site_by_domain = {s.domain: s for s in sites}
    if job.provider_name == "html":
        not_ready = [d for d in cleared_domains if d not in HTML_SUPPORTED_DOMAINS and site_by_domain[d].status != "ready"]
        for domain in not_ready:
            await log_activity(
                db,
                workspace_id=flow.workspace_id,
                action="source_unavailable",
                details={"domain": domain, "flow_id": str(flow.id), "reason": site_by_domain[domain].status_note},
            )
        cleared_domains = [d for d in cleared_domains if d not in not_ready]
    hints = {
        s.domain: SourceHint(kind=s.kind, list_url=s.list_url)
        for s in sites
        if s.kind in ("feed", "sitemap", "html") and s.list_url and s.status == "ready"
    }

    for domain in blocked_domains:
        await log_activity(
            db,
            workspace_id=flow.workspace_id,
            action="source_policy_review_required",
            details={"domain": domain, "flow_id": str(flow.id), "reason": "SOURCE_POLICY_REVIEW"},
        )

    created_ids: list[str] = []
    if cleared_domains:
        provider = PROVIDERS[job.provider_name]
        candidates = await provider.discover(
            domains=cleared_domains,
            theme=flow.theme,
            limit=flow.news_limit_per_run,
            keywords=list(flow.keywords or []),
            stop_words=list(flow.stop_words or []),
            sources=hints,
        )
        llm_theme = theme_for_llm(flow)

        llm_relevance_unavailable = False
        for item in candidates:
            existing = await _is_duplicate(db, workspace_id=flow.workspace_id, item=item)
            is_dup = existing is not None

            # Разбор заголовка (provider.discover's matches_theme, для HtmlSourceProvider)
            # — дешёвый предфильтр по ключевым словам, не настоящее понимание темы.
            # Здесь — тот самый "ИИ сама изучила и отобрала по теме" шаг: реальная
            # проверка через YandexGPT (ТЗ не говорит иначе про отбор по теме, и это
            # прямой запрос владельца проекта). Не настоящий отказ в доступе к модели
            # (не настроена/бюджет исчерпан) не должен проваливать весь сбор — тогда
            # просто перестаём звать LLM до конца этого задания и не отбрасываем
            # оставшиеся кандидаты только из-за этого.
            is_relevant = True
            relevance_reasoning = ""
            if not is_dup and not llm_relevance_unavailable:
                try:
                    relevance = await _llm_provider.check_relevance(
                        db, workspace_id=flow.workspace_id, news_item_id=None, title=item.title, theme=llm_theme
                    )
                    is_relevant = relevance.is_relevant
                    relevance_reasoning = relevance.reasoning
                except AppError as exc:
                    logger.warning(
                        "LLM relevance check недоступна (%s), дальше в этом задании кандидаты "
                        "проходят без проверки темы: %s", exc.code, exc.message,
                    )
                    llm_relevance_unavailable = True

            if is_dup:
                decision_reason = "duplicate_of_existing_discovery"
            elif not is_relevant:
                decision_reason = "not_relevant_to_theme"
            else:
                decision_reason = "new_story"

            discovery = Discovery(
                workspace_id=flow.workspace_id,
                scan_job_id=job.id,
                normalized_url=item.normalized_url,
                discovery_domain=item.discovery_domain,
                content_hash=item.content_hash,
                story_key=item.story_key,
                is_duplicate=is_dup,
                duplicate_of_news_id=None,
                decision_reason=decision_reason,
                metadata_json={
                    "label": item.label,
                    "published": (item.metadata or {}).get("published", ""),
                    **({"relevance_reasoning": relevance_reasoning} if relevance_reasoning else {}),
                },
            )
            db.add(discovery)
            await db.flush()

            if is_dup or not is_relevant:
                continue

            story = await db.scalar(select(Story).where(Story.workspace_id == flow.workspace_id, Story.story_key == item.story_key))
            if story is None:
                story = Story(workspace_id=flow.workspace_id, story_key=item.story_key)
                db.add(story)
                await db.flush()

            news = NewsItem(
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                story_id=story.id,
                title=item.title,
                text="",
                status=NewsStatus.DISCOVERED,
                discovery_domain=item.discovery_domain,
                discovery_original_fragment=item.original_fragment,
                discovery_id=discovery.id,
            )
            db.add(news)
            await db.flush()
            created_ids.append(str(news.id))

    job.status = ScanJobStatus.done
    job.created_news_ids = created_ids
    job.finished_at = datetime.now(timezone.utc)
    await db.commit()

    await log_activity(
        db,
        workspace_id=flow.workspace_id,
        action="scan_job_completed",
        details={"flow_id": str(flow.id), "job_id": str(job.id), "created_news_count": len(created_ids)},
    )
    return job

"""Отдельный процесс фоновых задач (ТЗ §4: "один отдельный worker фоновых задач").
Запуск: python -m app.worker.runner (см. docker-compose.yml, сервис `worker`).

Опрашивает таблицу scan_jobs напрямую (SELECT ... FOR UPDATE SKIP LOCKED), а не
отдельную очередь в Redis: наличие строки со статусом pending в Postgres уже
само по себе и есть очередь. Это также значит, что worker'у для работы нужен
только доступ к Postgres, не к Redis — важно, когда worker запущен отдельно
от API (например, локально, а API — в облаке с закрытым для внешних
подключений Redis).

Помимо этого, раз в _SCHEDULE_CHECK_INTERVAL_SECONDS проверяет расписание
потоков (schedule_period/schedule_time) и сам создаёт scan_job для тех, кому
пора — это и есть автозапуск сбора без нажатия кнопки."""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session_factory
from app.models import ScanJob
from app.models.enums import ScanJobStatus
from app.services.ai_service import auto_generate_drafts
from app.services.scan_service import enqueue_due_scheduled_scans, process_scan_job

logger = logging.getLogger("app.worker")

_POLL_INTERVAL_SECONDS = 0.5
_SCHEDULE_CHECK_INTERVAL_SECONDS = 60.0


async def fetch_next_pending_job_id(db: AsyncSession) -> uuid.UUID | None:
    stmt = (
        select(ScanJob.id)
        .where(ScanJob.status == ScanJobStatus.pending)
        .order_by(ScanJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return await db.scalar(stmt)


async def run_forever() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("Правовой Поток worker запущен, опрос таблицы scan_jobs")
    session_factory = get_session_factory()
    loop = asyncio.get_event_loop()
    last_schedule_check = 0.0
    while True:
        if loop.time() - last_schedule_check >= _SCHEDULE_CHECK_INTERVAL_SECONDS:
            last_schedule_check = loop.time()
            try:
                async with session_factory() as db:
                    for job_id in await enqueue_due_scheduled_scans(db):
                        logger.info("Автозапуск по расписанию создал scan_job %s", job_id)
            except Exception:  # noqa: BLE001 -- one bad check must not kill the worker
                logger.exception("Ошибка проверки расписания потоков")

        found = False
        try:
            async with session_factory() as db:
                job_id = await fetch_next_pending_job_id(db)
                if job_id is None:
                    await db.rollback()
                else:
                    found = True
                    try:
                        job = await process_scan_job(db, job_id)
                        logger.info(
                            "scan_job %s обработан: provider=%s, новых материалов=%d",
                            job_id, job.provider_name, len(job.created_news_ids),
                        )
                        if job.status == ScanJobStatus.done:
                            try:
                                summary = await auto_generate_drafts(db, job)
                                logger.info("автоматические черновики по scan_job %s: %s", job_id, summary)
                            except Exception:  # noqa: BLE001 -- сбор уже завершён, черновики — отдельный шаг
                                logger.exception("Ошибка автоматических черновиков по scan_job %s", job_id)
                    except Exception:  # noqa: BLE001 -- worker must keep running past one bad job
                        logger.exception("Ошибка обработки scan_job %s", job_id)
        except Exception:  # noqa: BLE001 -- a transient DB hiccup must not kill the worker
            logger.exception("Ошибка при опросе scan_jobs, повтор через 2с")
            await asyncio.sleep(2)
            continue
        if not found:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_forever())

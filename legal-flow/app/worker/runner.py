"""Отдельный процесс фоновых задач (ТЗ §4: "один отдельный worker фоновых задач").
Запуск: python -m app.worker.runner (см. docker-compose.yml, сервис `worker`).

Опрашивает таблицу scan_jobs напрямую (SELECT ... FOR UPDATE SKIP LOCKED), а не
отдельную очередь в Redis: наличие строки со статусом pending в Postgres уже
само по себе и есть очередь. Это также значит, что worker'у для работы нужен
только доступ к Postgres, не к Redis — важно, когда worker запущен отдельно
от API (например, локально, а API — в облаке с закрытым для внешних
подключений Redis)."""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session_factory
from app.models import ScanJob
from app.models.enums import ScanJobStatus
from app.services.scan_service import process_scan_job

logger = logging.getLogger("app.worker")

_POLL_INTERVAL_SECONDS = 0.5


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
    while True:
        found = False
        try:
            async with session_factory() as db:
                job_id = await fetch_next_pending_job_id(db)
                if job_id is None:
                    await db.rollback()
                else:
                    found = True
                    try:
                        await process_scan_job(db, job_id)
                        logger.info("scan_job %s обработан", job_id)
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

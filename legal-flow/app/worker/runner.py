"""Отдельный процесс фоновых задач (ТЗ §4: "один отдельный worker фоновых задач").
Запуск: python -m app.worker.runner (см. docker-compose.yml, сервис `worker`)."""

import asyncio
import logging
import uuid

from app.config import get_settings
from app.db import get_session_factory
from app.services.scan_service import process_scan_job
from app.worker.queue import dequeue_scan_job

logger = logging.getLogger("app.worker")


async def run_forever() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("Правовой Поток worker запущен, ожидание задач в очереди Redis")
    session_factory = get_session_factory()
    while True:
        job_id_str = await dequeue_scan_job(timeout_seconds=5)
        if job_id_str is None:
            continue
        try:
            job_id = uuid.UUID(job_id_str)
        except ValueError:
            logger.error("Некорректный job_id в очереди: %s", job_id_str)
            continue
        async with session_factory() as db:
            try:
                await process_scan_job(db, job_id)
                logger.info("scan_job %s обработан", job_id)
            except Exception:  # noqa: BLE001 -- worker must keep running past one bad job
                logger.exception("Ошибка обработки scan_job %s", job_id)


if __name__ == "__main__":
    asyncio.run(run_forever())

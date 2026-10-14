import re
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership, require_active_access, require_csrf, require_role
from app.errors import AppError
from app.models import Export, Membership
from app.models.enums import Role
from app.schemas.exports import CreateExportRequest, ExportCandidateOut, ExportDownloadOut, ExportOut
from app.services import export_service

router = APIRouter(prefix="/api/v1", tags=["exports"])
_EDIT_ROLES = (Role.workspace_owner, Role.editor)


def _out(e: Export) -> ExportOut:
    return ExportOut(
        id=e.id, filename=e.filename, content_hash=e.content_hash,
        news_item_ids=e.news_item_ids, status=e.status.value, created_at=e.created_at,
    )


@router.get("/exports", response_model=list[ExportOut])
async def list_exports(membership: Membership = Depends(get_current_membership), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.scalars(
            select(Export).where(Export.workspace_id == membership.workspace_id).order_by(Export.created_at.desc())
        )
    ).all()
    return [_out(e) for e in rows]


@router.post("/exports", response_model=ExportOut, status_code=201, dependencies=[Depends(require_csrf)])
async def create_export(
    payload: CreateExportRequest,
    membership: Membership = Depends(require_role(*_EDIT_ROLES)),
    _access: Membership = Depends(require_active_access),
    db: AsyncSession = Depends(get_db),
):
    export = await export_service.create_export(
        db,
        workspace_id=membership.workspace_id,
        filename=payload.filename,
        mode=payload.mode,
        limit=payload.limit,
        news_item_ids=payload.news_item_ids,
    )
    return _out(export)


@router.get("/exports/candidates", response_model=list[ExportCandidateOut])
async def export_candidates(
    limit: int = Query(default=200, ge=1, le=500),
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    """Опубликованные материалы (новые сверху) — список для ручного выбора в разделе «Экспорт»."""
    rows = await export_service.list_candidates(db, workspace_id=membership.workspace_id, limit=limit)
    return [
        ExportCandidateOut(
            id=n.id, title=n.title, release_mode=n.release_mode.value, published_at=n.published_at, created_at=n.created_at
        )
        for n in rows
    ]


def attachment_headers(filename: str) -> dict:
    """Content-Disposition для скачивания: ASCII-имя для старых клиентов + filename* (UTF-8) для кириллицы."""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "export.xml"
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"}


@router.get("/exports/{export_id}/file")
async def download_export_file(
    export_id: uuid.UUID,
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    """Скачивание XML на компьютер (сам файл, а не JSON)."""
    export = await db.get(Export, export_id)
    if export is None or export.workspace_id != membership.workspace_id:
        raise AppError("VALIDATION_ERROR", "Экспорт не найден.")
    return Response(content=export.content, media_type="application/xml; charset=utf-8", headers=attachment_headers(export.filename))


@router.get("/exports/{export_id}/download", response_model=ExportDownloadOut)
async def download_export(
    export_id: uuid.UUID,
    membership: Membership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db),
):
    export = await db.get(Export, export_id)
    if export is None or export.workspace_id != membership.workspace_id:
        raise AppError("VALIDATION_ERROR", "Экспорт не найден.")
    # ТЗ §11: пока Яндекс.Диск не подключён — только предпросмотр/скачивание с NOT_SENT,
    # никогда не "отправлен".
    return ExportDownloadOut(filename=export.filename, content=export.content, status=export.status.value)

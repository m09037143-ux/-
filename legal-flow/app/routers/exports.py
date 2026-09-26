import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_membership, require_active_access, require_csrf, require_role
from app.errors import AppError
from app.models import Export, Membership
from app.models.enums import Role
from app.schemas.exports import CreateExportRequest, ExportDownloadOut, ExportOut
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
    export = await export_service.create_export(db, workspace_id=membership.workspace_id, filename=payload.filename)
    return _out(export)


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

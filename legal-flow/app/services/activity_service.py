import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityEvent


async def log_activity(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    action: str,
    details: dict | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    db.add(
        ActivityEvent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            details=details or {},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

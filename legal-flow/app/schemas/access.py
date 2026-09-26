from datetime import datetime

from pydantic import BaseModel


class AccessResponse(BaseModel):
    role: str
    trial_state: str
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    server_time: datetime
    plan_code: str | None
    plan_name: str | None
    can_mutate: bool
    limits: dict

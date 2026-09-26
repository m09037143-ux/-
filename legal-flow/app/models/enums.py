import enum


class Role(str, enum.Enum):
    platform_admin = "platform_admin"
    workspace_owner = "workspace_owner"
    editor = "editor"
    viewer = "viewer"


class NewsStatus(str, enum.Enum):
    DISCOVERED = "DISCOVERED"
    NEEDS_SOURCE = "NEEDS_SOURCE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DRAFT = "DRAFT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    SOURCE_POLICY_REVIEW = "SOURCE_POLICY_REVIEW"
    ACCESS_LIMITED = "ACCESS_LIMITED"
    AI_ERROR = "AI_ERROR"
    DUPLICATE = "DUPLICATE"


class ReleaseMode(str, enum.Enum):
    INDEPENDENT_FACT_REPORT = "INDEPENDENT_FACT_REPORT"
    ATTRIBUTED_REUSE = "ATTRIBUTED_REUSE"


class FactStatus(str, enum.Enum):
    confirmed = "confirmed"
    unknown = "unknown"
    needs_review = "needs_review"
    contradictory = "contradictory"


class SourceAction(str, enum.Enum):
    discover = "discover"
    fetch = "fetch"
    extract_facts = "extract_facts"
    temporary_store = "temporary_store"
    retain_full_text = "retain_full_text"
    send_to_llm = "send_to_llm"
    reuse_text = "reuse_text"
    publish = "publish"


class ScanJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    canceled = "canceled"


class ExportStatus(str, enum.Enum):
    NOT_SENT = "NOT_SENT"

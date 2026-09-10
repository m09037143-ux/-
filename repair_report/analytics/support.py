"""Section 11 -- Аналитика Технической Поддержки. Spec §2.13, revised.

Built from a SEPARATE, OPTIONAL third file (a tech-support ticket export) --
see repair_report/ingest/support_reader.py and docs/REVERSE_ENGINEERING.md
§14 for the full investigation that confirmed every formula here against
the reference DOCX (section 11's narrative text and tables 26/27/28).

11.2 "Инженеры поддержки" has NO implementation here on purpose: the
source file's 'Кто ответил' (who answered) column is 100% empty (672/672
rows), and -- confirmed by inspecting the reference DOCX's raw XML body --
the reference report ITSELF has no table under that heading either, just
the bare subheading. This isn't a gap to fill; it's the verified, correct
absence of data, reproduced faithfully rather than invented.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.periods import Period
from repair_report.config import loader


def period_window_df(support_df: pd.DataFrame, current: Period, previous: Period | None) -> tuple[pd.DataFrame, list[Period]]:
    """Same 2-month (current + calendar-previous) window as analytics/parts.py."""
    cfg = loader.support_config()
    field = cfg["period_field"]
    dates = support_df[field]
    window_periods = [p for p in (previous, current) if p is not None]

    def in_window(ts) -> bool:
        if pd.isna(ts):
            return False
        return any(ts.year == p.year and ts.month == p.month for p in window_periods)

    mask = dates.apply(in_window)
    return support_df[mask].copy(), window_periods


@dataclass
class SupportSummary:
    ticket_count: int
    closed_count: int
    closed_share_pct: float
    leader_org: str | None
    leader_count: int
    laggard_org: str | None
    laggard_count: int

    def leader_follower_text(self) -> str:
        if self.leader_org is None:
            return "Недостаточно данных для определения активности обращений в техподдержку."
        return (
            f"Абсолютным лидером является {self.leader_org} ({self.leader_count} шт.). "
            f"Наименьшие показатели зафиксированы у {self.laggard_org} ({self.laggard_count} шт.)."
        )


def compute_summary(window_df: pd.DataFrame) -> SupportSummary:
    cfg = loader.support_config()
    closed_statuses = set(cfg["closed_statuses"])

    if window_df.empty:
        return SupportSummary(0, 0, 0.0, None, 0, None, 0)

    closed_count = int(window_df["status"].isin(closed_statuses).sum())

    orgs = window_df["organization"].dropna()
    first_seen_order = list(dict.fromkeys(orgs))
    rank = {o: i for i, o in enumerate(first_seen_order)}
    counts = orgs.value_counts()

    leader = laggard = None
    leader_count = laggard_count = 0
    if len(counts):
        leader = min(counts.index, key=lambda o: (-counts[o], rank[o]))
        laggard = min(counts.index, key=lambda o: (counts[o], rank[o]))
        leader_count = int(counts[leader])
        laggard_count = int(counts[laggard])

    return SupportSummary(
        ticket_count=len(window_df),
        closed_count=closed_count,
        closed_share_pct=(closed_count / len(window_df) * 100) if len(window_df) else 0.0,
        leader_org=leader,
        leader_count=leader_count,
        laggard_org=laggard,
        laggard_count=laggard_count,
    )


@dataclass
class OrgRow:
    organization: str
    count: int


def organizations_table(window_df: pd.DataFrame, top_n: int | None = None) -> list[OrgRow]:
    cfg = loader.support_config()
    top_n = top_n or cfg["top_n_organizations"]
    orgs = window_df["organization"].dropna()
    first_seen_order = list(dict.fromkeys(orgs))
    counts = orgs.value_counts().reindex(first_seen_order).sort_values(ascending=False, kind="mergesort")
    return [OrgRow(organization=str(o), count=int(c)) for o, c in counts.items()][:top_n]


@dataclass
class TopicRow:
    topic: str
    count: int


def topics_table(window_df: pd.DataFrame, top_n: int | None = None) -> list[TopicRow]:
    cfg = loader.support_config()
    top_n = top_n or cfg["top_n_topics"]
    topics = window_df["topic"].dropna()
    first_seen_order = list(dict.fromkeys(topics))
    counts = topics.value_counts().reindex(first_seen_order).sort_values(ascending=False, kind="mergesort")
    return [TopicRow(topic=str(t), count=int(c)) for t, c in counts.items()][:top_n]


@dataclass
class QualityAuditRow:
    field_label: str
    filled: int
    missing: int
    fill_pct: float


def quality_audit_table(window_df: pd.DataFrame) -> list[QualityAuditRow]:
    cfg = loader.support_config()
    total = len(window_df)
    rows = []
    for f in cfg["quality_audit_fields"]:
        key, label = f["key"], f["label"]
        filled = int(window_df[key].notna().sum()) if key in window_df.columns else 0
        rows.append(
            QualityAuditRow(
                field_label=label,
                filled=filled,
                missing=total - filled,
                fill_pct=(filled / total * 100) if total else 0.0,
            )
        )
    return rows

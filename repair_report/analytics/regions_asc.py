"""Section 4 (regions, ASC, brands) and 4.1 (ASC visit spend). Spec §2.6/§2.7.

All of the "full" (non-top-15) listings here are verified 1:1 against the
Косовов workbook's Регионы/АСЦ/Изготовители/Выезды_АСЦ sheets, which the
client's own manual process used and which include rows the docx truncates
away at top-15 (including ASC/regions with count_cur == 0, i.e. no longer
active this period).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.common import MISSING_LABELS, fill_missing_categoricals
from repair_report.analytics.tables import DynamicsTable, build_dynamics_table

PLACEHOLDER_LABELS = set(MISSING_LABELS.values())


def _prep(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None:
        return None
    return fill_missing_categoricals(df)


def regions_table(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> DynamicsTable:
    return build_dynamics_table(_prep(current_df), _prep(previous_df), "asc_region_grp", sort_by="count_cur")


def asc_table(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> DynamicsTable:
    return build_dynamics_table(_prep(current_df), _prep(previous_df), "asc_name_grp", sort_by="count_cur")


def brands_table(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> DynamicsTable:
    return build_dynamics_table(_prep(current_df), _prep(previous_df), "brand_grp", sort_by="count_cur")


@dataclass
class VisitRow:
    name: str
    visit_count: int
    visit_sum: float
    first_seen_rank: int = 0


@dataclass
class VisitsTable:
    rows: list[VisitRow]
    total_count: int
    total_sum: float

    def top(self, n: int) -> list[VisitRow]:
        return self.rows[:n]


def asc_visits_table(current_df: pd.DataFrame) -> VisitsTable:
    """Section 4.1 -- ASC ranked by visit ('Выезд') activity. Current period only
    (the reference report shows no previous-period comparison for this table)."""
    filled = fill_missing_categoricals(current_df)
    visits = filled[filled["visit_cost"] > 0]
    first_seen_order = list(dict.fromkeys(visits["asc_name_grp"]))
    rank_by_name = {name: i for i, name in enumerate(first_seen_order)}
    grp = visits.groupby("asc_name_grp", sort=False)["visit_cost"].agg(count="size", sum="sum")
    grp = grp.reindex(first_seen_order).sort_values("count", ascending=False, kind="mergesort")
    rows = [
        VisitRow(name=str(idx), visit_count=int(r["count"]), visit_sum=float(r["sum"]), first_seen_rank=rank_by_name.get(idx, 0))
        for idx, r in grp.iterrows()
    ]
    return VisitsTable(rows=rows, total_count=len(visits), total_sum=float(visits["visit_cost"].sum()))


@dataclass
class LeaderFollowerText:
    """Template data for the recurring 'Абсолютным лидером является ... /
    Наименьшие показатели ... зафиксированы у ...' narrative blurb used in
    sections 4, 4.1, 6, 7.2, 8.1 (spec §2.6)."""

    leader_name: str
    leader_count: int
    leader_sum: float
    laggard_name: str
    laggard_count: int
    laggard_sum: float

    def render(self, count_unit: str = "шт.") -> str:
        return (
            f"Абсолютным лидером является {self.leader_name} "
            f"({self.leader_count} {count_unit}, сумма {_fmt_rub(self.leader_sum)}). "
            f"Наименьшие показатели в выборке зафиксированы у {self.laggard_name} "
            f"({self.laggard_count} {count_unit}, сумма {_fmt_rub(self.laggard_sum)})."
        )


def _fmt_rub(v: float) -> str:
    from repair_report.analytics.common import format_rub

    return format_rub(v)


def leader_follower_from_dynamics(table: DynamicsTable, rank_by: str = "count_cur") -> LeaderFollowerText | None:
    """Leader/laggard picked from the FULL group set (not just a displayed
    top-N), per spec §2.6 ('не только топ-15').

    Verified tie-break rule (see docs/REVERSE_ENGINEERING.md §leader-laggard):
    ranking is by `rank_by` (count_cur for regions/ASC/brands, sum_cur for
    the manufacturers table, matching each table's own primary sort column);
    ties are broken by EARLIEST first-appearance row order in the source
    file, for both the leader and the laggard end -- confirmed independently
    against the ASC table (tied ASC at count=1: 'ИП Лавринов ...', the
    earliest-occurring, wins over lower/higher-sum alternatives) and the
    brands table (tied brands at count=1: 'Maunfeld', earlier-occurring,
    wins over 'Home' despite having a HIGHER sum -- ruling out any sum-based
    tie-break). Placeholder labels ('Неизвестный АСЦ', '—', 'Не указан',
    'Бренд не указан') are excluded from candidacy on both ends, and groups
    absent this period (count_cur == 0) are excluded as well.
    """
    active = [r for r in table.rows if r.count_cur > 0 and r.name not in PLACEHOLDER_LABELS]
    if not active:
        return None
    value = (lambda r: r.count_cur) if rank_by == "count_cur" else (lambda r: r.sum_cur)
    leader = min(active, key=lambda r: (-value(r), r.first_seen_rank))
    laggard = min(active, key=lambda r: (value(r), r.first_seen_rank))
    return LeaderFollowerText(
        leader_name=leader.name,
        leader_count=leader.count_cur,
        leader_sum=leader.sum_cur,
        laggard_name=laggard.name,
        laggard_count=laggard.count_cur,
        laggard_sum=laggard.sum_cur,
    )


def leader_follower_from_visits(table: VisitsTable) -> LeaderFollowerText | None:
    active = [r for r in table.rows if r.name not in PLACEHOLDER_LABELS]
    if not active:
        return None
    leader = min(active, key=lambda r: (-r.visit_count, r.first_seen_rank))
    laggard = min(active, key=lambda r: (r.visit_count, r.first_seen_rank))
    return LeaderFollowerText(
        leader_name=leader.name,
        leader_count=leader.visit_count,
        leader_sum=leader.visit_sum,
        laggard_name=laggard.name,
        laggard_count=laggard.visit_count,
        laggard_sum=laggard.visit_sum,
    )

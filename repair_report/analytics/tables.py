"""Generic "group by category, current vs previous period" table builder.

Sections 3 (categories, repair level), 4 (regions, ASC, brands) and 7.1 (TV
diagonals) all follow the same shape: group rows by some categorical column,
count + sum 'total_amount' for the current period and (if available) the
calendar-previous period, sort, and always report a grand total ('ИТОГ') over
the FULL group set even when the displayed table is truncated to a top-N.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class DynamicsRow:
    name: str
    count_cur: int
    sum_cur: float
    count_prev: float | None
    sum_prev: float | None
    first_seen_rank: int = 0
    """Position (0 = earliest) of this group's first appearance in the current
    period's row order. Used only to break ties when picking a leader/laggard
    for the narrative blurbs (§2.6) -- verified against the reference report
    to be the correct tie-break (earliest occurrence wins), which is NOT the
    same as the table's own display tie-break (see build_dynamics_table)."""


@dataclass
class DynamicsTable:
    rows: list[DynamicsRow]  # ALL groups, sorted
    total_count_cur: int
    total_sum_cur: float
    total_count_prev: float | None
    total_sum_prev: float | None
    has_previous: bool

    def top(self, n: int) -> list[DynamicsRow]:
        return self.rows[:n]


def build_dynamics_table(
    current_df: pd.DataFrame,
    previous_df: pd.DataFrame | None,
    group_col: str,
    sort_by: str = "count_cur",
    ascending: bool = False,
    drop_zero_current: bool = False,
) -> DynamicsTable:
    """`drop_zero_current`: when True, groups absent from the current period
    (count_cur == 0, i.e. only ever appeared in the previous period) are
    dropped from the result entirely. Verified against the reference report:
    section 3's equipment-category/repair-level tables DO drop such groups
    (a category with 0 repairs this month, e.g. 'Пылесос робот', is omitted
    even though it had previous-period activity), while the full
    region/ASC/manufacturer listings (verified via the Косовов workbook) DO
    keep them, to make churn visible -- so this defaults to False."""
    # Tie-break order: verified against the reference report (section 3
    # 'Категория' table) that ties in the sort column are broken by the
    # group's FIRST ROW OF APPEARANCE in the current period, not
    # alphabetically -- e.g. 'Плиты' (first seen at row 16) sorts ahead of
    # 'Планшет' (first seen at row 657) despite 'Планшет' < 'Плиты'
    # alphabetically, because both have count=2. A stable sort preserves
    # this as long as groups are laid out in first-appearance order first.
    first_seen_order = list(dict.fromkeys(current_df[group_col]))

    cur_grp = current_df.groupby(group_col, sort=False)["total_amount"].agg(count="size", sum="sum")
    cur_grp = cur_grp.reindex(first_seen_order)

    if previous_df is not None:
        prev_grp = previous_df.groupby(group_col, sort=False)["total_amount"].agg(count="size", sum="sum")
    else:
        prev_grp = pd.DataFrame(columns=["count", "sum"])

    combined = cur_grp.join(prev_grp, how="left", lsuffix="_cur", rsuffix="_prev")
    combined[["count_cur", "sum_cur"]] = combined[["count_cur", "sum_cur"]].fillna(0)

    if previous_df is not None:
        # Groups present only in the previous period (absent from current)
        # still need to appear (count_cur=0) so "disappeared" categories show up.
        only_prev = prev_grp.index.difference(combined.index)
        if len(only_prev):
            extra = prev_grp.loc[only_prev].rename(columns={"count": "count_prev", "sum": "sum_prev"})
            extra["count_cur"] = 0.0
            extra["sum_cur"] = 0.0
            combined = pd.concat([combined, extra[["count_cur", "sum_cur", "count_prev", "sum_prev"]]])
        combined[["count_prev", "sum_prev"]] = combined[["count_prev", "sum_prev"]].fillna(0)

    if drop_zero_current:
        combined = combined[combined["count_cur"] > 0]

    combined = combined.sort_values(sort_by, ascending=ascending, kind="mergesort")

    rank_by_name = {name: i for i, name in enumerate(first_seen_order)}
    fallback_rank = len(first_seen_order)

    rows = [
        DynamicsRow(
            name=str(idx),
            count_cur=int(r["count_cur"]),
            sum_cur=float(r["sum_cur"]),
            count_prev=(float(r["count_prev"]) if previous_df is not None else None),
            sum_prev=(float(r["sum_prev"]) if previous_df is not None else None),
            first_seen_rank=rank_by_name.get(idx, fallback_rank),
        )
        for idx, r in combined.iterrows()
    ]

    return DynamicsTable(
        rows=rows,
        total_count_cur=int(cur_grp["count"].sum()) if len(cur_grp) else 0,
        total_sum_cur=float(cur_grp["sum"].sum()) if len(cur_grp) else 0.0,
        total_count_prev=(float(prev_grp["count"].sum()) if previous_df is not None and len(prev_grp) else (0.0 if previous_df is not None else None)),
        total_sum_prev=(float(prev_grp["sum"].sum()) if previous_df is not None and len(prev_grp) else (0.0 if previous_df is not None else None)),
        has_previous=previous_df is not None,
    )

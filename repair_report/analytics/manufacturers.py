"""Section 6 -- Аналитика по изготовителям. Spec §2.9.

Verified 1:1 (all 10 rows, including the 'Не указан' placeholder from §1.3)
against the Косовов workbook's 'Изготовители' sheet.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.common import fill_missing_categoricals, safe_share_pct


@dataclass
class ManufacturerRow:
    name: str
    sum_cur: float
    share_pct: float
    sum_prev: float | None
    count_cur: int
    model_count: int
    first_seen_rank: int = 0


@dataclass
class ManufacturersTable:
    rows: list[ManufacturerRow]  # sorted by sum_cur descending

    def top(self, n: int) -> list[ManufacturerRow]:
        return self.rows[:n]


def manufacturers_table(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> ManufacturersTable:
    cur = fill_missing_categoricals(current_df)
    total_sum = float(cur["total_amount"].sum())

    first_seen_order = list(dict.fromkeys(cur["manufacturer_grp"]))
    rank_by_name = {name: i for i, name in enumerate(first_seen_order)}

    cur_grp = cur.groupby("manufacturer_grp", sort=False).agg(
        sum=("total_amount", "sum"),
        count=("total_amount", "size"),
        model_count=("model", "nunique"),
    )
    cur_grp = cur_grp.reindex(first_seen_order)

    if previous_df is not None:
        prev = fill_missing_categoricals(previous_df)
        prev_sum = prev.groupby("manufacturer_grp")["total_amount"].sum()
    else:
        prev_sum = pd.Series(dtype=float)

    rows = []
    for name, r in cur_grp.iterrows():
        rows.append(
            ManufacturerRow(
                name=str(name),
                sum_cur=float(r["sum"]),
                share_pct=safe_share_pct(r["sum"], total_sum),
                sum_prev=(float(prev_sum.get(name, 0.0)) if previous_df is not None else None),
                count_cur=int(r["count"]),
                model_count=int(r["model_count"]),
                first_seen_rank=rank_by_name.get(name, 0),
            )
        )
    rows.sort(key=lambda r: r.sum_cur, reverse=True)
    return ManufacturersTable(rows=rows)

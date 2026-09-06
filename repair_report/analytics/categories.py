"""Section 3 -- Структура затрat по видам техники. Spec §1.4, §2.4, §2.5.

Two tables share the same rows (grouped by consolidated 'Вид техники'), just
sorted differently: 'Затраты по категориям' by sum descending, 'По
категориям (в разрезе количества)' by count descending. A third table groups
by repair level instead.
"""
from __future__ import annotations

import pandas as pd

from repair_report.analytics.common import add_equipment_category_group, repair_level_code
from repair_report.analytics.tables import DynamicsTable, build_dynamics_table


def _prep(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None:
        return None
    return add_equipment_category_group(df)


def equipment_by_sum(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> DynamicsTable:
    return build_dynamics_table(_prep(current_df), _prep(previous_df), "equipment_category_grp", sort_by="sum_cur", drop_zero_current=True)


def equipment_by_count(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> DynamicsTable:
    return build_dynamics_table(_prep(current_df), _prep(previous_df), "equipment_category_grp", sort_by="count_cur", drop_zero_current=True)


def _prep_level(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None:
        return None
    out = df.copy()
    out["repair_level_grp"] = repair_level_code(out)
    return out


def repair_level_table(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> DynamicsTable:
    return build_dynamics_table(_prep_level(current_df), _prep_level(previous_df), "repair_level_grp", sort_by="count_cur", drop_zero_current=True)

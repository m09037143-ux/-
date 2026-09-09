"""Section 3 -- Структура затрат по видам техники. Spec §1.4, §2.4, §2.5.

Two tables share the same rows (grouped by consolidated 'Вид техники'), just
sorted differently: 'Затраты по категориям' by sum descending, 'По
категориям (в разрезе количества)' by count descending. A third table groups
by repair level instead.

NOTE on the 'nan' category (revised 2026-09-09, overrides §1.4/§3 of
docs/REVERSE_ENGINEERING.md): the July reference report DOES show a literal
'nan' row for rows with a blank 'Категория техники' (verified exactly), so
that is what the original implementation reproduced. After hands-on testing
with real (August) data, the client explicitly asked for this row to be
dropped: it is not a real equipment type, just blank cells in that column,
and should not be counted as a category. So rows that resolve to the 'nan'
placeholder (see common.MISSING_CATEGORY_RAW) are now excluded from BOTH
category tables (and therefore from their own ИТОГ row, and from the
section's charts) -- this is a deliberate, client-directed change, not a
bug in the original July-matching behavior. It does NOT touch any other
section: the overall 'Количество ремонтов' KPI (section 2), and the
ASC/region/manufacturer/brand placeholder-label rows (§1.3, e.g.
'Неизвестный АСЦ'), are unaffected -- those still count every row,
continuation rows included, exactly as before.
"""
from __future__ import annotations

import pandas as pd

from repair_report.analytics.common import MISSING_CATEGORY_RAW, add_equipment_category_group, repair_level_code
from repair_report.analytics.tables import DynamicsTable, build_dynamics_table


def _prep(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None:
        return None
    tagged = add_equipment_category_group(df)
    return tagged[tagged["equipment_category_grp"] != MISSING_CATEGORY_RAW]


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

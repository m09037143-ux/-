"""Section 12 -- Детализация ремонтов (текущий период). Spec §2.14.

Column set and order verified against the reference DOCX's own detail table
(8 columns) -- note this is NOT the same as the illustrative column list from
the original product spec text (which additionally named "IRIS код условия"
in a different position); the actual reference report uses exactly these 8.
"""
from __future__ import annotations

import pandas as pd

DETAIL_COLUMNS = [
    ("asc_code", "Код АСЦ"),
    ("brand", "Бренд"),
    ("model", "Модель"),
    ("iris_symptom_code", "IRIS код симптома"),
    ("iris_section_code", "IRIS код секции"),
    ("iris_defect_code", "IRIS код дефекта"),
    ("iris_repair_code", "IRIS код ремонта"),
    ("total_amount", "Сумма (итого)"),
]


def detail_rows(current_df: pd.DataFrame) -> list[dict]:
    """One dict per row, in original spreadsheet order, using display labels
    as keys. Every source row is included, continuation rows (§1.3) too --
    they show up with mostly-blank cells, matching the reference report."""
    cols = [c for c, _ in DETAIL_COLUMNS if c in current_df.columns]
    labels = {c: label for c, label in DETAIL_COLUMNS}
    out = []
    for _, r in current_df[cols].iterrows():
        out.append({labels[c]: (r[c] if pd.notna(r[c]) else "") for c in cols})
    return out

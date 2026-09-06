"""Section 8 -- Аналитика по дефектам и IRIS кодам (только ТВ техника). Spec §2.11.

8.1: chain = IRIS код секции -> IRIS код дефекта -> IRIS код ремонта (verified
column order against ТВ_IRIS sheet's actual codes, which does NOT match the
prose order suggested by the original product spec -- see
docs/REVERSE_ENGINEERING.md).
8.2: exact-text grouping of 'Описание дефекта' (case-sensitive, per spec, to
match the reference 1:1).
8.3: IRIS coding-error audit -- see config/iris_rules.json; the rule set
itself is unconfirmed (the reference period has zero errors), only the
output shape is confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.tv_analysis import tv_only
from repair_report.config import loader


@dataclass
class IrisChainRow:
    chain: str
    count: int


def iris_chain_table(current_df: pd.DataFrame, top_n: int | None = None) -> tuple[list[IrisChainRow], int]:
    """Returns (top-N rows, total distinct chain count).

    IMPORTANT verified quirk: the reference workbook's "full" ТВ_IRIS sheet is
    itself capped at 50 rows -- it is NOT an exhaustive list of every
    distinct chain (July has 194 distinct (section, defect, repair) combos
    among 1342 fully-coded TV rows; the reference's 50 rows sum to exactly
    1144, matching this function's own top-50 sum exactly). So `top_n=50`
    is the correct comparison point for tests against that fixture, not "no
    limit" -- do not chase a larger figure trying to find "the true full
    list" match, there isn't one to find.
    """
    top_n = top_n or loader.app_settings()["top_n_iris_chains"]
    tv = tv_only(current_df).copy()
    tv = tv.dropna(subset=["iris_section_code", "iris_defect_code", "iris_repair_code"])
    tv["chain"] = tv["iris_section_code"] + " → " + tv["iris_defect_code"] + " → " + tv["iris_repair_code"]

    first_seen_order = list(dict.fromkeys(tv["chain"]))
    rank = {c: i for i, c in enumerate(first_seen_order)}
    counts = tv.groupby("chain", sort=False).size().reindex(first_seen_order)
    counts = counts.sort_values(ascending=False, kind="mergesort")

    rows = [IrisChainRow(chain=str(idx), count=int(v)) for idx, v in counts.items()]
    return rows[:top_n], len(rows)


@dataclass
class DefectTextRow:
    text: str
    count: int


def defect_text_table(current_df: pd.DataFrame, top_n: int | None = None, case_insensitive: bool | None = None) -> list[DefectTextRow]:
    settings = loader.app_settings()
    top_n = top_n or settings["top_n_defect_text"]
    if case_insensitive is None:
        case_insensitive = settings["defect_text_case_insensitive"]

    tv = tv_only(current_df)
    texts = tv["defect_description"].dropna()
    key = texts.str.lower() if case_insensitive else texts
    display = texts if not case_insensitive else texts  # display uses original casing of first occurrence

    first_seen_order = list(dict.fromkeys(key))
    rank = {k: i for i, k in enumerate(first_seen_order)}
    first_display = dict(zip(key, display)) if case_insensitive else None

    counts = key.value_counts()
    counts = counts.reindex(first_seen_order).sort_values(ascending=False, kind="mergesort")

    rows = []
    for k, v in counts.items():
        label = first_display[k] if case_insensitive else k
        rows.append(DefectTextRow(text=str(label), count=int(v)))
    return rows[:top_n]


@dataclass
class IrisErrorRow:
    asc_name: str
    brand: str
    model: str
    section: str
    defect: str
    repair: str
    errors: str


def iris_errors_table(current_df: pd.DataFrame) -> list[IrisErrorRow]:
    """Section 8.3. Output shape verified against the Косовов workbook's
    'IRIS_ошибки' sheet (7 columns, 0 data rows in the reference period).
    Only `required_fields` from config/iris_rules.json is enforced -- the
    real validity rule set is unconfirmed (see docs/REVERSE_ENGINEERING.md)."""
    rules = loader.iris_rules()
    required = rules.get("required_fields", [])
    field_to_col = {
        "IRIS код секции": "iris_section_code",
        "IRIS код дефекта": "iris_defect_code",
        "IRIS код ремонта": "iris_repair_code",
    }
    tv = tv_only(current_df)
    rows: list[IrisErrorRow] = []
    for _, r in tv.iterrows():
        errors = []
        for field_label in required:
            col = field_to_col.get(field_label)
            if col and pd.isna(r.get(col)):
                errors.append(f"Не заполнен код: {field_label}")
        if errors:
            rows.append(
                IrisErrorRow(
                    asc_name=str(r.get("asc_name")) if pd.notna(r.get("asc_name")) else "",
                    brand=str(r.get("brand")) if pd.notna(r.get("brand")) else "",
                    model=str(r.get("model")) if pd.notna(r.get("model")) else "",
                    section=str(r.get("iris_section_code")) if pd.notna(r.get("iris_section_code")) else "",
                    defect=str(r.get("iris_defect_code")) if pd.notna(r.get("iris_defect_code")) else "",
                    repair=str(r.get("iris_repair_code")) if pd.notna(r.get("iris_repair_code")) else "",
                    errors="; ".join(errors),
                )
            )
    return rows

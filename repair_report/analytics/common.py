"""Shared normalization/formatting helpers used by every analytics/*.py section.

The central, non-obvious rule this module encodes (see docs/REVERSE_ENGINEERING.md
§1.3 for the full evidence trail): rows are NEVER deduplicated or dropped for
counting purposes. A "continuation row" -- an extra spare-part line for a
repair that already has its own row, identifiable by having every
categorical/date field blank except the part fields -- counts as its own
repair-record unit in every categorical breakdown (ASC, region, manufacturer,
brand). Its blank categorical fields are replaced with an explicit label
BEFORE grouping, rather than being dropped or re-linked to the "parent" row.

The one exception is 'Уровень ремонта' (repair level): a blank value there is
folded into the ANR bucket rather than given its own label -- verified by
exact reproduction of the reference ANR count (699) and sum (999 575 руб.) and
of the network-wide ANR share text rounding to 46%.
"""
from __future__ import annotations

import math

import pandas as pd

from repair_report.config import loader

MISSING_ASC_NAME = "Неизвестный АСЦ"
MISSING_REGION = "—"
MISSING_MANUFACTURER = "Не указан"
MISSING_BRAND = "Бренд не указан"
MISSING_CATEGORY_RAW = "nan"  # literal string, matches the reference report exactly

MISSING_LABELS = {
    "asc_name": MISSING_ASC_NAME,
    "asc_region": MISSING_REGION,
    "manufacturer": MISSING_MANUFACTURER,
    "brand": MISSING_BRAND,
}

TYPOGRAPHIC_MINUS = "−"


def fill_missing_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with grouping-ready *_grp columns for the categorical
    dimensions that get an explicit placeholder label when blank (§1.3)."""
    out = df.copy()
    for col, label in MISSING_LABELS.items():
        if col in out.columns:
            out[f"{col}_grp"] = out[col].where(out[col].notna() & (out[col].astype(str).str.strip() != ""), label)
    return out


def equipment_category_group(raw_category: object) -> str:
    """Map a raw 'Категория техники' value to its consolidated 'Вид техники'
    group (section 3), per config/category_mapping.json. A blank/NaN value
    maps to the literal string 'nan' -- verified against the reference report,
    which shows a 'nan' row rather than hiding or relabeling it (§1.4)."""
    if raw_category is None or (isinstance(raw_category, float) and math.isnan(raw_category)):
        return MISSING_CATEGORY_RAW
    text = str(raw_category).strip()
    if not text or text.lower() == "nan":
        return MISSING_CATEGORY_RAW

    mapping = loader.category_mapping()
    lowered = text.lower()
    exact = mapping.get("exact", {})
    if lowered in exact:
        return exact[lowered]
    for prefix, group in mapping.get("startswith", {}).items():
        if lowered.startswith(prefix):
            return group
    return text  # unrecognized category passed through unchanged, per config


def add_equipment_category_group(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'equipment_category_grp' (section 3's consolidated 'Вид техники').

    Base rule: map the raw 'Категория техники' text via category_mapping.json;
    a blank/NaN value maps to the literal string 'nan' (§1.4).

    Fallback for blank categories (verified against the reference report,
    which counts 'TOPDEVICE'/'TDWC24ВН3260V' rows as ТВ despite a blank
    'Категория техники' -- see docs/REVERSE_ENGINEERING.md §equipment
    categories): if a row's category is blank but its 'Бренд' is filled, and
    every OTHER row of that same brand in this data with a non-blank category
    maps to exactly one consolidated group, that group is used instead of
    'nan'. This is data-driven (no hardcoded model list) and stays
    conservative -- a brand that repairs more than one type of equipment
    (e.g. a general-appliance brand) is left as 'nan' for its blank rows,
    same as a genuine multi-part continuation row (§1.3), whose brand is
    itself blank and therefore never eligible for this fallback.
    """
    out = df.copy()
    out["equipment_category_grp"] = out["equipment_category"].apply(equipment_category_group)

    is_blank = out["equipment_category_grp"] == MISSING_CATEGORY_RAW
    has_brand = out["brand"].notna() & (out["brand"].astype(str).str.strip() != "")
    fallback_candidates = out[is_blank & has_brand]
    if len(fallback_candidates):
        known = out[~is_blank]
        brand_groups = known.groupby("brand")["equipment_category_grp"].nunique()
        brand_single_group = known.groupby("brand")["equipment_category_grp"].first()
        homogeneous_brands = set(brand_groups[brand_groups == 1].index)
        for idx, row in fallback_candidates.iterrows():
            brand = row["brand"]
            if brand in homogeneous_brands:
                out.at[idx, "equipment_category_grp"] = brand_single_group[brand]
    return out


def repair_level_code(df: pd.DataFrame) -> pd.Series:
    """Raw repair-level code with blanks folded into the ANR bucket (see module
    docstring). Use this (not the raw column) for any repair-level grouping."""
    labels_cfg = loader.repair_level_labels()
    fallback_code = labels_cfg.get("missing_level_maps_to", "AN")
    return df["repair_level"].where(df["repair_level"].notna(), fallback_code)


def repair_level_label(code: str) -> str:
    labels_cfg = loader.repair_level_labels()
    return labels_cfg.get(code, code)


def is_anr(code: str) -> bool:
    labels_cfg = loader.repair_level_labels()
    return code in labels_cfg.get("anr_codes", ["AN", "ANR"])


def format_number(value: float) -> str:
    """Thousands-space-separated integer, e.g. 2655000 -> '2 655 000'."""
    n = int(round(value))
    return format(abs(n), ",").replace(",", " ") if n >= 0 else "-" + format(abs(n), ",").replace(",", " ")


def format_rub(value: float) -> str:
    return f"{format_number(value)} ₽"


def format_dynamics(current: float, previous: float | None, unit: str = "") -> str:
    """Format a current-vs-previous delta per spec §2.2:
      - '+322 (+26.5%)' / '−32.5%'-style typographic minus for negatives
      - '+30' with no percent when previous is 0 (or unavailable) and diff != 0
      - '+0 (+0.0%)' when current == previous and both are non-zero (or zero)
    `previous=None` means "no comparison period available" -- callers should
    check for this and render "n/a" rather than call this function.
    """
    if previous is None:
        raise ValueError("format_dynamics requires a numeric previous value; check availability first")
    diff = current - previous
    sign = "+" if diff >= 0 else TYPOGRAPHIC_MINUS
    diff_str = f"{format_number(abs(diff))}{unit}"
    if previous == 0:
        return f"{sign}{diff_str}"
    pct = diff / previous * 100
    pct_str = f"{abs(pct):.1f}%"
    return f"{sign}{diff_str} ({sign}{pct_str})"


def safe_share_pct(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return numerator / denominator * 100

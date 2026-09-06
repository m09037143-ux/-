"""Section 7 -- Анализ ТВ техники. Spec §2.10.

7.1 groups TVs by their RAW (un-consolidated) diagonal subcategory --
'Телевизоры NN-NN' -- not by the section-3 consolidated 'ТВ' group, so the
diagonal ranges stay visible. 7.2 drills into the top brands' top models.

A row counts as "TV" here using the same equipment_category_grp computed in
common.add_equipment_category_group (which includes the brand-homogeneity
fallback for blank categories, verified against the reference report's
TOPDEVICE/TDWC24VN3260V rows -- see docs/REVERSE_ENGINEERING.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.common import add_equipment_category_group
from repair_report.analytics.tables import DynamicsTable, build_dynamics_table
from repair_report.config import loader

DIAGONAL_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
MODEL_SIZE_RE = re.compile(r"(\d{2,3})")
DIAGONAL_RANGES = [(24, 39, '24-39"'), (40, 55, '40-55"'), (65, 77, '65-77"'), (85, 100, '85-100"')]


def _tv_rows(df: pd.DataFrame) -> pd.DataFrame:
    return add_equipment_category_group(df)


def tv_only(df: pd.DataFrame) -> pd.DataFrame:
    tagged = _tv_rows(df)
    return tagged[tagged["equipment_category_grp"] == "ТВ"]


def diagonal_table(current_df: pd.DataFrame, previous_df: pd.DataFrame | None) -> DynamicsTable:
    cur_tv = tv_only(current_df).copy()
    cur_tv["diagonal_grp"] = cur_tv.apply(_diagonal_label_from_row, axis=1)
    prev_tv = None
    if previous_df is not None:
        prev_tv = tv_only(previous_df).copy()
        prev_tv["diagonal_grp"] = prev_tv.apply(_diagonal_label_from_row, axis=1)
    return build_dynamics_table(cur_tv, prev_tv, "diagonal_grp", sort_by="count_cur")


def _diagonal_label_from_row(row: pd.Series) -> str:
    raw_category = row.get("equipment_category")
    if raw_category is not None and not (isinstance(raw_category, float) and pd.isna(raw_category)):
        m = DIAGONAL_RE.search(str(raw_category))
        if m:
            return f'{m.group(1)}-{m.group(2)}"'
        return str(raw_category)
    # Blank 'Категория техники' but already resolved to ТВ via the brand
    # fallback (common.add_equipment_category_group) -- verified against the
    # reference report (§7.1): fall back to a diagonal-size digit found in
    # the model code (e.g. 'TDWC24VN3260V' -> 24 -> '24-39"'), bucketed into
    # the same ranges observed elsewhere in the data. This is what makes the
    # reference's 24-39" count of 595 (not 591) reproducible.
    model = row.get("model")
    if isinstance(model, str):
        for match in MODEL_SIZE_RE.finditer(model):
            n = int(match.group(1))
            for lo, hi, label in DIAGONAL_RANGES:
                if lo <= n <= hi:
                    return label
    return "?"


@dataclass
class ModelRow:
    model: str
    count: int


@dataclass
class BrandModels:
    brand: str
    total_count: int
    models: list[ModelRow]


def top_brand_models(current_df: pd.DataFrame, top_brands: int | None = None, top_models: int | None = None) -> list[BrandModels]:
    settings = loader.app_settings()
    top_brands = top_brands or settings["top_n_tv_brands"]
    top_models = top_models or settings["top_n_tv_models_per_brand"]

    tv = tv_only(current_df)
    brand_counts = tv.groupby("brand").size().sort_values(ascending=False)
    result = []
    for brand in brand_counts.head(top_brands).index:
        sub = tv[tv["brand"] == brand]
        model_counts = sub.groupby("model").size().sort_values(ascending=False)
        models = [ModelRow(model=str(m), count=int(c)) for m, c in model_counts.head(top_models).items()]
        result.append(BrandModels(brand=str(brand), total_count=int(brand_counts[brand]), models=models))
    return result


@dataclass
class ModelLeaderFollower:
    leader_model: str
    leader_count: int
    laggard_model: str
    laggard_count: int

    def render(self) -> str:
        return (
            f"Абсолютным лидером является {self.leader_model} ({self.leader_count} шт.). "
            f"Наименьшие показатели зафиксированы у {self.laggard_model} ({self.laggard_count} шт.)."
        )


def model_leader_follower(current_df: pd.DataFrame) -> ModelLeaderFollower | None:
    """Leader/laggard across ALL TV models (not just the displayed top brands'
    top models), per the recurring '§2.6-style' blurb pattern. Verified
    against the reference: leader = model with the single highest repair
    count network-wide; laggard uses the same earliest-occurrence tie-break
    established for section 4 (see regions_asc.leader_follower_from_dynamics)."""
    tv = tv_only(current_df)
    if tv.empty:
        return None
    first_seen_order = list(dict.fromkeys(tv["model"]))
    rank = {m: i for i, m in enumerate(first_seen_order)}
    counts = tv.groupby("model", sort=False).size().reindex(first_seen_order)
    leader_model = min(counts.index, key=lambda m: (-counts[m], rank[m]))
    laggard_model = min(counts.index, key=lambda m: (counts[m], rank[m]))
    return ModelLeaderFollower(
        leader_model=str(leader_model),
        leader_count=int(counts[leader_model]),
        laggard_model=str(laggard_model),
        laggard_count=int(counts[laggard_model]),
    )

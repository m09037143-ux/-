"""Section 1 -- Реестры текущего отчетного периода. Simple listings, no
dynamics. 1.1: distinct ASC roster (alphabetical). 1.2: distinct
(category, brand, model) equipment roster grouped by category/brand,
sorted by case count descending within each brand.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.common import fill_missing_categoricals


@dataclass
class AscRegistryRow:
    seq: int
    asc_name: str
    asc_code: str
    region: str


def asc_registry(current_df: pd.DataFrame) -> list[AscRegistryRow]:
    filled = fill_missing_categoricals(current_df)
    grp = filled.groupby("asc_name_grp").agg(
        asc_code=("asc_code", "first"),
        region=("asc_region_grp", "first"),
    )
    grp = grp.sort_index(key=lambda idx: idx.str.lower())
    rows = []
    for i, (name, r) in enumerate(grp.iterrows(), start=1):
        code = r["asc_code"]
        code_str = str(int(code)) if pd.notna(code) else ""
        rows.append(AscRegistryRow(seq=i, asc_name=str(name), asc_code=code_str, region=str(r["region"])))
    return rows


@dataclass
class EquipmentRegistryRow:
    category: str
    brand: str
    model: str
    count: int


def equipment_registry(current_df: pd.DataFrame) -> list[EquipmentRegistryRow]:
    df = current_df.dropna(subset=["equipment_category", "brand", "model"])
    grp = df.groupby(["equipment_category", "brand", "model"]).size().reset_index(name="count")
    grp = grp.sort_values(
        by=["equipment_category", "brand", "count"],
        ascending=[True, True, False],
        key=lambda col: col.str.lower() if col.dtype == object else col,
    )
    return [
        EquipmentRegistryRow(category=str(r["equipment_category"]), brand=str(r["brand"]), model=str(r["model"]), count=int(r["count"]))
        for _, r in grp.iterrows()
    ]

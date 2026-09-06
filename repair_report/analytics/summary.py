"""Section 2 -- Управленческое резюме (management summary KPIs). Spec §2.1/§2.2."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.common import fill_missing_categoricals


@dataclass
class SummaryKpis:
    repair_count: int
    total_sum: float
    avg_check: float
    avg_duration_days: float | None
    asc_count: int
    region_count: int
    manufacturer_count: int
    brand_count: int
    model_count: int
    visits_count: int
    visits_sum: float
    parts_count: int
    parts_sum: float


def compute_summary(df: pd.DataFrame) -> SummaryKpis:
    """`df` must already be filtered to the target period."""
    filled = fill_missing_categoricals(df)
    n = len(df)
    total_sum = float(df["total_amount"].sum())

    duration = (df["repair_date"] - df["receipt_date"]).dt.days
    duration = duration.dropna()

    visits = df[df["visit_cost"] > 0]
    parts = df[df["parts_compensation"] > 0]

    return SummaryKpis(
        repair_count=n,
        total_sum=total_sum,
        avg_check=(total_sum / n) if n else 0.0,
        avg_duration_days=(float(duration.mean()) if len(duration) else None),
        asc_count=int(filled["asc_name_grp"].nunique()),
        region_count=int(filled["asc_region_grp"].nunique()),
        manufacturer_count=int(filled["manufacturer_grp"].nunique()),
        brand_count=int(filled["brand_grp"].nunique()),
        model_count=int(df["model"].nunique(dropna=True)),
        visits_count=len(visits),
        visits_sum=float(visits["visit_cost"].sum()),
        parts_count=len(parts),
        parts_sum=float(parts["parts_compensation"].sum()),
    )

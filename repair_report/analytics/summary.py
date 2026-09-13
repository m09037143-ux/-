"""Section 2 -- Управленческое резюме (management summary KPIs). Spec §2.1/§2.2."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.common import fill_missing_categoricals
from repair_report.analytics.periods import Period


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


@dataclass
class MonthlyKpiRow:
    """One row of the '2.1 Динамика по месяцам' subsection -- shown only
    when the selected report period spans more than one calendar month
    (quarter/year). Built by simply re-running compute_summary() on each
    present month individually; no new aggregation logic needed."""

    period: Period
    repair_count: int
    total_sum: float
    avg_check: float
    visits_count: int
    visits_sum: float
    asc_count: int


def monthly_kpi_table(df: pd.DataFrame, months: list[Period], periods_series: pd.Series) -> list[MonthlyKpiRow]:
    """`df`/`periods_series` are the FULL (unfiltered-by-period) frame and its
    per-row Period assignment (as returned by assign_periods) -- this filters
    to each month in `months` in turn, so the caller doesn't need to slice
    per month itself."""
    rows = []
    for m in months:
        sub = df[periods_series == m]
        k = compute_summary(sub)
        rows.append(
            MonthlyKpiRow(
                period=m,
                repair_count=k.repair_count,
                total_sum=k.total_sum,
                avg_check=k.avg_check,
                visits_count=k.visits_count,
                visits_sum=k.visits_sum,
                asc_count=k.asc_count,
            )
        )
    return rows

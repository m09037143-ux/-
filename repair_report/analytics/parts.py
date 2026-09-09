"""Section 10 -- Аналитика поставок запасных частей. Spec §2.13, revised.

Built from a SEPARATE, OPTIONAL file (a spare-parts logistics export) --
see repair_report/ingest/parts_reader.py and docs/REVERSE_ENGINEERING.md
§13 for the full investigation that confirmed every formula here against
the reference DOCX (sections 10 / 10.1 / 10.2's narrative text and tables).

Section 11 (tech support) is NOT covered by this file either -- it has no
ticket/support-shaped columns at all -- and remains an unconfirmed
placeholder (see parts_support.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.periods import Period
from repair_report.config import loader

MUNICIPAL_DISTRICT_RE = re.compile(r"^вн\.?\s*тер\.?\s*г\.?\s*муниципальный округ\s+(.+)", re.IGNORECASE)
CITY_OKRUG_RE = re.compile(r"^г\.о\.\s*город\s+(.+)", re.IGNORECASE)
BROAD_CITY_RE = re.compile(r"^Город\s", re.IGNORECASE)
PLAIN_CITY_RE = re.compile(r"^г\.?\s")


def extract_city(address: object) -> str | None:
    """Best-effort city/district label from a free-text delivery address.

    Verified against 15/15 rows of the reference's section-10.2 table: 14
    match exactly, 1 (Санкт-Петербург) is off by one in its 'Отгружено'
    count only -- almost certainly the same live-status-changes-over-time
    effect documented for the status breakdown (§13), not a parsing error.
    There is NO guarantee this matches the client's own address-resolution
    system row-for-row on data outside what was checked here -- this is a
    deliberately accepted approximation (see docs/REVERSE_ENGINEERING.md
    §13 and the product decision recorded there).
    """
    if not isinstance(address, str):
        return None
    parts = [p.strip() for p in address.split(",")][1:]  # drop the postal index

    for p in parts:
        m = MUNICIPAL_DISTRICT_RE.match(p)
        if m:
            return "г. муниципальный округ " + m.group(1)

    for p in parts:
        m = CITY_OKRUG_RE.match(p)
        if m:
            return "г. " + m.group(1)

    plain_city = None
    broad_city = None
    for p in parts:
        if BROAD_CITY_RE.match(p):
            broad_city = broad_city or p
            continue
        if PLAIN_CITY_RE.match(p):
            plain_city = plain_city or p
    if plain_city:
        return plain_city
    if broad_city:
        return broad_city
    return parts[0] if parts else None


def period_window_df(parts_df: pd.DataFrame, current: Period, previous: Period | None) -> tuple[pd.DataFrame, list[Period]]:
    """Filter to the current+previous month window (§13: section 10 covers
    both months together, not just the current one). Returns (filtered_df,
    periods_present_in_window) -- periods_present preserves calendar order
    and only includes periods that are both in [previous, current] AND
    actually have at least one date field parsed (skips entirely if the
    parts file simply doesn't cover this month, rather than crashing)."""
    cfg = loader.parts_config()
    field = cfg["period_field"]
    dates = parts_df[field]
    window_periods = [p for p in (previous, current) if p is not None]

    def in_window(ts) -> bool:
        if pd.isna(ts):
            return False
        return any(ts.year == p.year and ts.month == p.month for p in window_periods)

    mask = dates.apply(in_window)
    return parts_df[mask].copy(), window_periods


@dataclass
class PartsSummary:
    row_count: int
    unique_orders: int
    total_qty_ordered: float
    leader_part_number: str | None
    leader_count: int
    laggard_part_number: str | None
    laggard_count: int

    def leader_follower_text(self) -> str:
        if self.leader_part_number is None:
            return "Недостаточно данных для определения лидера по востребованности запасных частей."
        return (
            f"Абсолютным лидером является {self.leader_part_number} ({self.leader_count} шт.). "
            f"Наименьшие показатели зафиксированы у {self.laggard_part_number} ({self.laggard_count} шт.)."
        )


def compute_summary(window_df: pd.DataFrame) -> PartsSummary:
    if window_df.empty:
        return PartsSummary(0, 0, 0.0, None, 0, None, 0)

    first_seen_order = list(dict.fromkeys(window_df["ordered_part_number"]))
    rank = {p: i for i, p in enumerate(first_seen_order)}
    counts = window_df["ordered_part_number"].value_counts()

    leader = min(counts.index, key=lambda p: (-counts[p], rank[p]))
    laggard = min(counts.index, key=lambda p: (counts[p], rank[p]))

    return PartsSummary(
        row_count=len(window_df),
        unique_orders=int(window_df["order_number"].nunique()),
        total_qty_ordered=float(window_df["requested_total_qty"].sum()),
        leader_part_number=str(leader),
        leader_count=int(counts[leader]),
        laggard_part_number=str(laggard),
        laggard_count=int(counts[laggard]),
    )


@dataclass
class StatusMonthTable:
    statuses: list[str]
    months: list[str]  # e.g. ["2026-06", "2026-07"]
    counts: dict[str, dict[str, int]]  # status -> month_key -> count
    row_totals: dict[str, int]
    month_totals: dict[str, int]
    grand_total: int


def status_by_month_table(window_df: pd.DataFrame, window_periods: list[Period]) -> StatusMonthTable:
    cfg = loader.parts_config()
    ordered_statuses = list(cfg["line_status_order"])
    months = [p.key for p in window_periods]

    field = cfg["period_field"]
    df = window_df.copy()
    df["_month_key"] = df[field].apply(lambda ts: f"{ts.year:04d}-{ts.month:02d}" if pd.notna(ts) else None)

    present_statuses = list(df["line_status"].dropna().unique())
    extra_statuses = sorted(s for s in present_statuses if s not in ordered_statuses)
    all_statuses = ordered_statuses + extra_statuses

    counts: dict[str, dict[str, int]] = {s: {m: 0 for m in months} for s in all_statuses}
    for (status, month_key), n in df.groupby(["line_status", "_month_key"]).size().items():
        if status in counts and month_key in months:
            counts[status][month_key] = int(n)

    row_totals = {s: sum(counts[s].values()) for s in all_statuses}
    month_totals = {m: sum(counts[s][m] for s in all_statuses) for m in months}
    grand_total = sum(row_totals.values())

    # The 4 canonical statuses are always shown as fixed rows (even at 0 --
    # a status this period had zero lines in is still meaningful, same as a
    # churned-out ASC/region showing count=0 elsewhere in this app) --
    # verified against the reference table, which shows 'Зарезервирована на
    # складе' as a 0/2/2 row rather than omitting it. Only genuinely unknown
    # extra statuses are dropped if they never occur in this window.
    displayed_statuses = list(ordered_statuses) + [s for s in extra_statuses if row_totals[s] > 0]

    return StatusMonthTable(
        statuses=displayed_statuses,
        months=months,
        counts=counts,
        row_totals=row_totals,
        month_totals=month_totals,
        grand_total=grand_total,
    )


@dataclass
class GeographyRow:
    city: str
    total: int
    shipped: int
    fulfillment_pct: float


def geography_table(window_df: pd.DataFrame, top_n: int | None = None) -> list[GeographyRow]:
    cfg = loader.parts_config()
    top_n = top_n or cfg["top_n_geography"]
    shipped_status = cfg["shipped_status_value"]

    df = window_df.copy()
    df["_city"] = df["delivery_address"].apply(extract_city)
    df = df.dropna(subset=["_city"])

    grp = df.groupby("_city").size().sort_values(ascending=False)
    shipped = df[df["line_status"] == shipped_status].groupby("_city").size()

    rows = []
    for city, total in grp.items():
        shipped_n = int(shipped.get(city, 0))
        pct = (shipped_n / total * 100) if total else 0.0
        rows.append(GeographyRow(city=str(city), total=int(total), shipped=shipped_n, fulfillment_pct=pct))
    return rows[:top_n]

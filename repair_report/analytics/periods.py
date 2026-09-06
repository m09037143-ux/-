"""Period detection and current/previous period selection (spec §1.2, §2.2).

Key facts this module encodes, verified by direct row-level analysis of the
multi-month reference export (see docs/REVERSE_ENGINEERING.md):

  * 'Дата акта' is a period-closing date: every row belonging to the same
    (year, month) batch carries the SAME 'Дата акта' value, equal to the last
    calendar day of that month. So grouping by (year, month) of 'Дата акта'
    is exactly grouping by report period -- no day-level logic is needed or
    correct.
  * A "continuation row" (§1.3: an extra spare-part line for an already-
    recorded repair, identifiable by a blank 'Дата акта') has no period of
    its own. Its period is resolved via its '№ п/п' match to the nearest
    row(s) that DO carry a 'Дата акта' -- this is the one place in the whole
    pipeline where the №п/п link between a continuation row and its "parent"
    is used. It is used ONLY for period assignment, never to inherit any
    other field (ASC, region, brand, etc. still get the placeholder labels
    from common.fill_missing_categoricals).
  * The file is append-only across months (no №Акта appears in two different
    months), so once a period is isolated it can be filtered from the whole
    file with no de-duplication risk.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Period:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def previous(self) -> "Period":
        if self.month == 1:
            return Period(self.year - 1, 12)
        return Period(self.year, self.month - 1)

    def __str__(self) -> str:  # e.g. "июль 2026"
        return f"{MONTH_NAMES_RU[self.month]} {self.year}"


MONTH_NAMES_RU = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь",
    7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}


def assign_periods(df: pd.DataFrame) -> pd.Series:
    """Return a Series of Period objects (or None), one per row of df.

    Rows with a populated 'act_date' get their period directly. Rows with a
    blank 'act_date' (continuation rows, §1.3) get the period of the nearest
    row sharing the same 'row_no' that does have an act_date. If no such row
    exists (shouldn't happen in practice), the row's period is None and it is
    excluded from period-scoped analysis (but still exists in the raw frame).
    """
    dated = df[df["act_date"].notna()]
    parent_period_by_row_no = (
        dated.groupby("row_no")["act_date"]
        .first()
        .apply(lambda ts: Period(ts.year, ts.month))
    )

    def resolve(row) -> Period | None:
        if pd.notna(row["act_date"]):
            ts = row["act_date"]
            return Period(ts.year, ts.month)
        return parent_period_by_row_no.get(row["row_no"])

    return df.apply(resolve, axis=1)


def available_periods(periods: pd.Series) -> list[Period]:
    """Distinct periods present, sorted ascending. Excludes None."""
    uniq = {p for p in periods if p is not None}
    return sorted(uniq, key=lambda p: (p.year, p.month))


@dataclass
class PeriodSelection:
    current: Period
    previous: Period | None
    previous_available: bool
    previous_row_count: int
    note: str


def select_periods(all_periods: list[Period], requested_current: Period | None = None) -> PeriodSelection:
    """Pick the current period (default: the latest one present) and determine
    whether the calendar-previous month is actually present in the file.

    Per spec §2.2: the "previous period" is always the calendar month
    immediately before the current one -- NOT simply "the second most recent
    period in the file" -- so a gap in the data is reported explicitly rather
    than silently compared against an older period.
    """
    if not all_periods:
        raise ValueError("No periods found in file (no rows with a valid act_date).")

    current = requested_current if requested_current is not None else all_periods[-1]
    calendar_previous = current.previous()
    if calendar_previous in all_periods:
        return PeriodSelection(
            current=current,
            previous=calendar_previous,
            previous_available=True,
            previous_row_count=0,  # filled in by caller once it has filtered data
            note=f"Найден предыдущий период ({calendar_previous}) — сравнение будет включено.",
        )
    return PeriodSelection(
        current=current,
        previous=None,
        previous_available=False,
        previous_row_count=0,
        note=f"Календарно предыдущий месяц ({calendar_previous}) отсутствует в файле — сравнение недоступно.",
    )

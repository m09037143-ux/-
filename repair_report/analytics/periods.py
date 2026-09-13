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


SPAN_KINDS = ("month", "quarter", "year")


@dataclass(frozen=True)
class PeriodSpan:
    """A report period of any granularity: one calendar month, one calendar
    quarter (3 months), or one calendar year (12 months). Added to support
    quarter/year reports (see docs/REVERSE_ENGINEERING.md §15) alongside the
    original month-only `Period`/`select_periods` above, which are untouched
    and still used exactly as before for the month case.

    `index` means: month number (1-12) when kind == "month", quarter number
    (1-4) when kind == "quarter", unused (0) when kind == "year".
    """

    kind: str
    year: int
    index: int

    @property
    def key(self) -> str:
        if self.kind == "month":
            return f"month:{self.year:04d}-{self.index:02d}"
        if self.kind == "quarter":
            return f"quarter:{self.year:04d}-{self.index}"
        return f"year:{self.year:04d}"

    def months(self) -> list[Period]:
        """Every calendar month belonging to this span, in order -- regardless
        of whether the source file actually has data for all of them."""
        if self.kind == "month":
            return [Period(self.year, self.index)]
        if self.kind == "quarter":
            start = (self.index - 1) * 3 + 1
            return [Period(self.year, m) for m in range(start, start + 3)]
        return [Period(self.year, m) for m in range(1, 13)]

    def previous(self) -> "PeriodSpan":
        """The calendar-previous span of the SAME kind/length (previous
        month / previous quarter / previous year) -- never just 'the next
        span back in the file', same philosophy as Period.previous()."""
        if self.kind == "month":
            p = Period(self.year, self.index).previous()
            return PeriodSpan("month", p.year, p.month)
        if self.kind == "quarter":
            if self.index == 1:
                return PeriodSpan("quarter", self.year - 1, 4)
            return PeriodSpan("quarter", self.year, self.index - 1)
        return PeriodSpan("year", self.year - 1, 0)

    def __str__(self) -> str:
        if self.kind == "month":
            return str(Period(self.year, self.index))
        if self.kind == "quarter":
            return f"{self.index} квартал {self.year}"
        return f"{self.year} год"


def parse_span_key(key: str) -> PeriodSpan:
    """Inverse of PeriodSpan.key. A bare 'YYYY-MM' (no 'kind:' prefix) is
    accepted as a month span too, for robustness."""
    if ":" not in key:
        year, month = (int(x) for x in key.split("-"))
        return PeriodSpan("month", year, month)
    kind, rest = key.split(":", 1)
    if kind == "month":
        year, month = (int(x) for x in rest.split("-"))
        return PeriodSpan("month", year, month)
    if kind == "quarter":
        year, q = (int(x) for x in rest.split("-"))
        return PeriodSpan("quarter", year, q)
    if kind == "year":
        return PeriodSpan("year", int(rest), 0)
    raise ValueError(f"Unknown period span key: {key!r}")


def available_spans(all_periods: list[Period], kind: str) -> list[PeriodSpan]:
    """Distinct spans of `kind` that have at least one present month in the
    file, sorted ascending. A quarter/year need not be FULLY present to be
    offered here -- SpanSelection separately flags whether the chosen span
    is complete, so a partial period is still selectable, just clearly
    labelled (see select_span)."""
    if kind == "month":
        return [PeriodSpan("month", p.year, p.month) for p in all_periods]
    if kind == "quarter":
        seen: dict[tuple[int, int], None] = {}
        for p in all_periods:
            q = (p.month - 1) // 3 + 1
            seen[(p.year, q)] = None
        return [PeriodSpan("quarter", y, q) for (y, q) in sorted(seen)]
    if kind == "year":
        years = sorted({p.year for p in all_periods})
        return [PeriodSpan("year", y, 0) for y in years]
    raise ValueError(f"Unknown span kind: {kind!r}")


@dataclass
class SpanSelection:
    current: PeriodSpan
    current_months: list[Period]  # every calendar month in the span
    current_present_months: list[Period]  # subset actually present in the file
    current_complete: bool  # True iff every calendar month of the span is present
    previous: PeriodSpan
    previous_available: bool  # True iff the ENTIRE previous span is present
    note: str


def select_span(all_periods: list[Period], requested_key: str | None = None) -> SpanSelection:
    """Generalizes select_periods() to month/quarter/year. `requested_key`
    (from PeriodSpan.key) also carries the span kind -- there is no separate
    'kind' parameter. With no key, defaults to the MONTH containing the
    latest available period (identical default to the original
    select_periods(), so existing single-month callers are unaffected).

    A quarter/year is offered (available_spans) as soon as it has ONE
    present month, but comparison against the previous span is only enabled
    when that entire previous span is present -- and the current span's own
    completeness is reported separately, so an aggregate over a partial
    quarter/year is never mistaken for the whole thing.
    """
    if not all_periods:
        raise ValueError("No periods found in file (no rows with a valid act_date).")

    if requested_key:
        current = parse_span_key(requested_key)
    else:
        latest = all_periods[-1]
        current = PeriodSpan("month", latest.year, latest.month)

    current_months = current.months()
    current_present = [p for p in current_months if p in all_periods]
    current_complete = len(current_present) == len(current_months)

    previous = current.previous()
    previous_months = previous.months()
    previous_present = [p for p in previous_months if p in all_periods]
    previous_available = len(previous_present) == len(previous_months)

    if previous_available:
        note = f"Найден предыдущий период ({previous}) — сравнение будет включено."
    elif current.kind == "month":
        note = f"Календарно предыдущий месяц ({previous}) отсутствует в файле — сравнение недоступно."
    else:
        note = f"Период сравнения ({previous}) отсутствует в файле полностью — сравнение недоступно."

    if not current_complete:
        missing = [p for p in current_months if p not in current_present]
        note += (
            f" Внимание: в выбранном периоде отсутствуют данные за {', '.join(str(p) for p in missing)} — "
            "показатели по факту относятся только к фактически представленным месяцам."
        )

    return SpanSelection(
        current=current,
        current_months=current_months,
        current_present_months=current_present,
        current_complete=current_complete,
        previous=previous,
        previous_available=previous_available,
        note=note,
    )


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

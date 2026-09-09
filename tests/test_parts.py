"""Section 10 (spare parts) vs. the reference DOCX's narrative text and
tables (24/25). See docs/REVERSE_ENGINEERING.md §13 for the full
investigation -- these formulas were reverse-engineered from a NEW file
provided after the original report shipped, so there is no Kosovov-style
pre-computed pivot to check against; every number here was independently
derived and cross-checked directly against the reference DOCX instead.

One expected, understood discrepancy: 'Статус линии заказа' is a LIVE,
mutable field (an order's status changes over time as it's processed) --
unlike the main export's immutable 'Дата акта'. This parts file is a
snapshot taken ~1 month after the reference DOCX was generated, so per-
status counts have legitimately moved on (e.g. an order 'Рассматривается'
in August may be 'Отгружена' or 'Отказана' by the time of this file's
snapshot). The ROW/ORDER/QUANTITY totals (which don't depend on a mutable
status field) match exactly; the status BREAKDOWN and one geography row's
shipped count do not, by design -- see the module docstrings for why that
is not a bug.
"""
from pathlib import Path

import pytest

from repair_report.analytics.parts import compute_summary, geography_table, period_window_df, status_by_month_table
from repair_report.analytics.periods import Period
from repair_report.ingest.parts_reader import load_parts_data

FIXTURES = Path(__file__).parent / "fixtures"
PARTS_FILE = FIXTURES / "Запчасти_090926.xlsx"


@pytest.fixture(scope="session")
def parts_window():
    df, _ = load_parts_data(str(PARTS_FILE))
    window_df, window_periods = period_window_df(df, Period(2026, 7), Period(2026, 6))
    return window_df, window_periods


def test_ingest_resolves_all_critical_columns():
    df, report = load_parts_data(str(PARTS_FILE))
    assert report.ok
    assert report.missing_critical == []
    assert len(df) == 1500


def test_summary_matches_reference_exactly(parts_window):
    window_df, _ = parts_window
    s = compute_summary(window_df)
    assert s.row_count == 746
    assert s.unique_orders == 709
    assert s.total_qty_ordered == 756
    assert s.leader_part_number == "Y010123-000232-01V"
    assert s.leader_count == 24


def test_leader_follower_text_matches_reference(parts_window):
    window_df, _ = parts_window
    s = compute_summary(window_df)
    text = s.leader_follower_text()
    assert text.startswith("Абсолютным лидером является Y010123-000232-01V (24 шт.).")


def test_status_by_month_totals_match_reference(parts_window):
    window_df, window_periods = parts_window
    t = status_by_month_table(window_df, window_periods)
    assert t.months == ["2026-06", "2026-07"]
    assert t.month_totals == {"2026-06": 321, "2026-07": 425}
    assert t.grand_total == 746
    # All 4 canonical statuses always present as fixed rows, even at 0.
    assert set(t.statuses) >= {
        "Зарезервирована на складе", "Отгружена", "Отказана менеджером", "Рассматривается",
    }


def test_geography_table_matches_reference_for_14_of_15_cities(parts_window):
    window_df, _ = parts_window
    rows = {r.city: r for r in geography_table(window_df)}

    exact_expected = {
        "г. Волгоград": (117, 2, 1.7),
        "г. Краснодар": (35, 1, 2.9),
        "г Челябинск": (31, 1, 3.2),
        "г. Ростов-на-Дону": (27, 2, 7.4),
        "Город Москва": (27, 4, 14.8),
        "г. Омск": (24, 2, 8.3),
        "г. Новосибирск": (22, 1, 4.5),
        "г. Казань": (21, 1, 4.8),
        "Ставропольский край": (21, 1, 4.8),
        "г. Иркутск": (17, 7, 41.2),
        "г. муниципальный округ Куркино": (17, 0, 0.0),
        "г. Самара": (16, 0, 0.0),
        "г. Воронеж": (16, 0, 0.0),
        "г. Ижевск": (15, 3, 20.0),
    }
    for city, (total, shipped, pct) in exact_expected.items():
        assert rows[city].total == total, city
        assert rows[city].shipped == shipped, city
        assert round(rows[city].fulfillment_pct, 1) == pct, city

    # The one documented near-miss (live-status drift, not a parsing bug):
    # reference shows 27/5/18.5%, this snapshot shows 27/6/22.2%.
    assert rows["Город Санкт-Петербург"].total == 27
    assert rows["Город Санкт-Петербург"].shipped in (5, 6)


def test_period_window_handles_no_overlap_gracefully():
    df, _ = load_parts_data(str(PARTS_FILE))
    # A period far outside the parts file's May-September 2026 coverage.
    window_df, window_periods = period_window_df(df, Period(2024, 1), Period(2023, 12))
    assert len(window_df) == 0
    s = compute_summary(window_df)
    assert s.row_count == 0
    assert s.leader_part_number is None
    assert "Недостаточно данных" in s.leader_follower_text()

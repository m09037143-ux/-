"""§1.2/§1.3 period detection, including the continuation-row parent lookup."""
from repair_report.ingest.excel_reader import load_repair_data
from repair_report.analytics.periods import assign_periods, available_periods, select_periods
from tests.conftest import MULTI_MONTH_FILE, JULY_ONLY_FILE


def test_multi_month_row_counts_include_continuation_rows():
    df, _ = load_repair_data(str(MULTI_MONTH_FILE))
    periods = assign_periods(df)
    counts = {str(p): (periods == p).sum() for p in available_periods(periods)}
    # Verified by direct row-level analysis (see docs/REVERSE_ENGINEERING.md
    # §1.3): each month's continuation rows (blank Дата акта, resolved via
    # №п/п to their dated "parent") are folded into that month's total.
    assert counts["апрель 2026"] == 1252
    assert counts["май 2026"] == 1124
    assert counts["июнь 2026"] == 1213
    assert counts["июль 2026"] == 1535


def test_period_sums_match_reference():
    df, _ = load_repair_data(str(MULTI_MONTH_FILE))
    periods = assign_periods(df)
    df = df.assign(_period=periods)
    june_sum = df.loc[df["_period"].apply(str) == "июнь 2026", "total_amount"].sum()
    july_sum = df.loc[df["_period"].apply(str) == "июль 2026", "total_amount"].sum()
    assert june_sum == 2_086_875
    assert july_sum == 2_655_000


def test_select_periods_finds_calendar_previous():
    df, _ = load_repair_data(str(MULTI_MONTH_FILE))
    periods = available_periods(assign_periods(df))
    sel = select_periods(periods)
    assert str(sel.current) == "июль 2026"
    assert str(sel.previous) == "июнь 2026"
    assert sel.previous_available


def test_select_periods_reports_missing_previous_explicitly():
    df, _ = load_repair_data(str(JULY_ONLY_FILE))
    periods = available_periods(assign_periods(df))
    sel = select_periods(periods)
    assert str(sel.current) == "июль 2026"
    assert sel.previous is None
    assert not sel.previous_available
    assert "недоступно" in sel.note

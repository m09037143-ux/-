"""Section 8 vs. the reference DOCX (top-15) and the Kosovov ТВ_IRIS sheet.

Note (see analytics/iris_defects.py docstring): the Kosovov "full" ТВ_IRIS
sheet is itself capped at 50 rows, not exhaustive -- the correct comparison
is this module's own top-50, not "all distinct chains" (194 in July).
"""


def test_iris_chain_top15_matches_reference(july_report):
    top15 = july_report.iris_chains
    expected = [
        ("LCD → N → R3", 228), ("SYS → N → R3", 163), ("LCD → N → R4", 94),
        ("SYS → 3 → L", 65), ("SYS → N → R4", 53), ("G90 → 11 → 2", 50),
        ("LCD → 3 → L", 42), ("SYS → N → G", 38), ("G00 → 3 → L", 34),
        ("G15 → N → R3", 26), ("SYS → 11 → 2", 26), ("SWO → 11 → 2", 24),
        ("SYS → N → A", 22), ("CTR → Z → Z7", 20), ("SYS → N → A2", 20),
    ]
    assert [(r.chain, r.count) for r in top15] == expected


def test_iris_chain_top50_sum_matches_kosovov_full_sheet(kosovov_sheets):
    from repair_report.analytics.iris_defects import iris_chain_table
    from repair_report.analytics.periods import assign_periods
    from repair_report.ingest.excel_reader import load_repair_data
    from tests.conftest import MULTI_MONTH_FILE

    df, _ = load_repair_data(str(MULTI_MONTH_FILE))
    periods = assign_periods(df)
    current_df = df[periods.apply(str) == "июль 2026"]

    top50, total_distinct = iris_chain_table(current_df, top_n=50)
    assert total_distinct == 194  # true distinct chain count (not capped)
    assert sum(r.count for r in top50) == 1144

    kosovov_rows = kosovov_sheets["ТВ_IRIS"]
    assert len(kosovov_rows) == 50
    assert sum(r[1] for r in kosovov_rows) == 1144
    assert [(r.chain, r.count) for r in top50] == [(name, count) for name, count in kosovov_rows]


def test_defect_text_is_case_sensitive_by_default(july_report):
    texts = {r.text: r.count for r in july_report.defect_texts}
    assert texts["не включается"] == 118
    assert texts["Не включается"] == 91
    assert "не включается" != "Не включается"  # sanity: distinct dict keys


def test_iris_errors_empty_for_july(july_report):
    # Reference: "Ошибок кодирования IRIS для телевизоров не выявлено."
    assert july_report.iris_errors == []

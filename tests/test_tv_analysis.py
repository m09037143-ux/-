"""Section 7 vs. the reference DOCX (table15) and the Kosovov workbook's
ТВ_Модели sheet."""
from repair_report.analytics.common import format_rub

EXPECTED_DIAGONAL = [
    ('40-55"', 645, 545, "1 228 583 ₽"),
    ('24-39"', 595, 444, "891 917 ₽"),
    ('65-77"', 101, 64, "239 000 ₽"),
    ('85-100"', 3, 3, "15 750 ₽"),
]


def test_diagonal_table_matches_reference(july_report):
    rows = july_report.tv_diagonal.rows
    assert [(r.name, r.count_cur, int(r.count_prev), format_rub(r.sum_cur)) for r in rows] == EXPECTED_DIAGONAL
    assert july_report.tv_diagonal.total_count_cur == 1344
    assert format_rub(july_report.tv_diagonal.total_sum_cur) == "2 375 250 ₽"


def test_brand_models_total_matches_kosovov_tv_models_sheet(july_report, kosovov_sheets):
    total_from_kosovov = sum(row[2] for row in kosovov_sheets["ТВ_Модели"])
    assert total_from_kosovov == 1344

    total_top_brands = sum(b.total_count for b in july_report.tv_brand_models)
    expected_brands = ["Hi", "TUVIO", "HARTENS", "ASANO", "HOLLEBERG"]
    assert [b.brand for b in july_report.tv_brand_models] == expected_brands
    assert total_top_brands == 305 + 240 + 211 + 84 + 78


def test_tv_model_leader_follower(july_report):
    # Leader is unambiguous (58 is a unique max) and matches the reference
    # exactly. The laggard is NOT asserted by name: 117 distinct TV models
    # are tied at count=1 in July, and none of the tie-break rules verified
    # elsewhere in this codebase (earliest-occurrence, alphabetical,
    # smallest-brand) reproduce the reference's specific pick ('CRL430')
    # here -- see docs/REVERSE_ENGINEERING.md §tv-model-laggard. Only the
    # count is asserted.
    lf = july_report.tv_model_leader_follower
    assert lf.leader_model == "TD43FFBCH11"
    assert lf.leader_count == 58
    assert lf.laggard_count == 1

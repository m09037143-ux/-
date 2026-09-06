"""Section 3 tables vs. the reference DOCX (tables 4/5/6 -- no Kosovov sheet
exists for these, the DOCX numbers are themselves the full listing since
there are under 10 groups)."""
from repair_report.analytics.common import format_dynamics, format_rub

EXPECTED_BY_SUM = [
    ("ТВ", 1344, 1056, "2 375 250 ₽"),
    ("Мониторы", 102, 76, "135 275 ₽"),
    ("Ноутбук", 58, 6, "97 200 ₽"),
    ("СВЧ", 20, 23, "25 500 ₽"),
    ("Стиральные машины", 5, 46, "13 575 ₽"),
    ("Холодильники", 1, 0, "4 200 ₽"),
    ("Планшет", 2, 1, "2 400 ₽"),
    ("Плиты", 2, 0, "1 600 ₽"),
    ("nan", 1, 3, "0 ₽"),
]

EXPECTED_LEVEL = [
    ("AN", "АНР (акт о неремонтопригодности)", 699, 570, "999 575 ₽"),
    ("TO", "ТО (техническое обслуживание)", 346, 233, "486 766 ₽"),
    ("L1", "L1 (простой ремонт)", 253, 195, "502 850 ₽"),
    ("L3", "L3 (сложный ремонт)", 161, 156, "476 650 ₽"),
    ("L2", "L2 (средний ремонт)", 76, 59, "189 159 ₽"),
]


def test_equipment_by_sum_matches_reference(july_report):
    rows = july_report.equipment_by_sum.rows
    assert [(r.name, r.count_cur, int(r.count_prev), format_rub(r.sum_cur)) for r in rows] == EXPECTED_BY_SUM
    assert july_report.equipment_by_sum.total_count_cur == 1535
    assert format_rub(july_report.equipment_by_sum.total_sum_cur) == "2 655 000 ₽"


def test_repair_level_table_matches_reference(july_report):
    from repair_report.analytics.common import repair_level_label

    rows = july_report.repair_level.rows
    got = [(r.name, repair_level_label(r.name), r.count_cur, int(r.count_prev), format_rub(r.sum_cur)) for r in rows]
    assert got == EXPECTED_LEVEL
    assert july_report.repair_level.total_count_cur == 1535
    assert format_rub(july_report.repair_level.total_sum_cur) == "2 655 000 ₽"


def test_equipment_dynamics_strings(july_report):
    by_name = {r.name: r for r in july_report.equipment_by_sum.rows}
    assert format_dynamics(by_name["ТВ"].count_cur, by_name["ТВ"].count_prev) == "+288 (+27.3%)"
    assert format_dynamics(by_name["СВЧ"].count_cur, by_name["СВЧ"].count_prev) == "−3 (−13.0%)"
    assert format_dynamics(by_name["Холодильники"].count_cur, by_name["Холодильники"].count_prev) == "+1"

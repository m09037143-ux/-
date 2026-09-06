"""Section 4 full (non-top-15) listings vs. the Kosovov workbook's
Регионы/АСЦ sheets -- these include churned-out groups (count_cur == 0),
unlike section 3's category tables.

NOTE on row order: Kosovov's own sheets are not perfectly reproducible by a
single sort rule -- e.g. its 'Изготовители' sheet turns out to be sorted by
COUNT while the reference DOCX's manufacturer table is sorted by SUM (they
agree at the top, diverge at the bottom). Rather than chase an
Excel-internal tie-break that may not even be a single consistent rule, these
tests compare CONTENT (as name -> values, order-independent) against Kosovov,
and separately assert the exact DISPLAY order (top-15, sorted by count/sum
descending) against the reference DOCX, which is what actually ships in the
report.
"""


def _as_dict(table):
    return {r.name: (r.count_cur, int(r.count_prev), r.sum_cur, r.sum_prev) for r in table.rows}


def _kosovov_dict(rows):
    return {name: (count, prev, sum_, prev_sum) for name, count, prev, _delta, sum_, prev_sum, _delta_sum in rows}


def test_regions_full_table_matches_kosovov_content(july_report, kosovov_sheets):
    assert _as_dict(july_report.regions) == _kosovov_dict(kosovov_sheets["Регионы"])


def test_asc_full_table_matches_kosovov_content(july_report, kosovov_sheets):
    assert _as_dict(july_report.asc) == _kosovov_dict(kosovov_sheets["АСЦ"])


def test_asc_visits_full_table_matches_kosovov_content(july_report, kosovov_sheets):
    got = {r.name: (r.visit_count, r.visit_sum) for r in july_report.asc_visits.rows}
    expected = {name: (count, s) for name, count, s in kosovov_sheets["Выезды_АСЦ"]}
    assert got == expected
    assert july_report.asc_visits.total_count == 122
    assert july_report.asc_visits.total_sum == 345_925


def test_leader_follower_blurbs_match_reference(july_report):
    assert july_report.asc_leader_follower.render() == (
        "Абсолютным лидером является ИП Гусак Александр Федорович (185 шт., сумма 264 275 ₽). "
        "Наименьшие показатели в выборке зафиксированы у ИП Лавринов Алексей Владимирович (1 шт., сумма 1 100 ₽)."
    )
    assert july_report.brands_leader_follower.render() == (
        'Абсолютным лидером является Hi (305 шт., сумма 522 740 ₽). '
        "Наименьшие показатели в выборке зафиксированы у Maunfeld (1 шт., сумма 1 200 ₽)."
    )
    assert july_report.asc_visits_leader_follower.render() == (
        'Абсолютным лидером является ООО "ЭЛЕКТРО-Н" (24 шт., сумма 55 800 ₽). '
        'Наименьшие показатели в выборке зафиксированы у ООО "МОРОЗКО" (1 шт., сумма 4 325 ₽).'
    )


def test_regions_and_asc_row_counts_match_kosovov(july_report, kosovov_sheets):
    # Full listing includes churned-out (count_cur == 0) groups too.
    assert len(july_report.regions.rows) == len(kosovov_sheets["Регионы"])
    assert len(july_report.asc.rows) == len(kosovov_sheets["АСЦ"])


def test_brands_top15_matches_reference(july_report):
    top = july_report.brands.rows[:15]
    expected_names = [
        "Hi", "HARTENS", "TUVIO", "ASANO", "HOLLEBERG", "DOFFLER", "RAZZ", "H",
        "LERAN", "EVO", "Skyworth", "LEFF", "TOPDEVICE", "HORIZONT", "General Electronics (GE)",
    ]
    assert [r.name for r in top] == expected_names
    assert [r.count_cur for r in top] == [305, 304, 240, 84, 78, 71, 66, 61, 61, 41, 41, 41, 40, 38, 22]

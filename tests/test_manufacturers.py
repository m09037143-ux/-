"""Section 6 vs. the Kosovov workbook's Изготовители sheet.

NOTE: Kosovov's own sheet is sorted by COUNT, while this table (matching the
reference DOCX's rendering) is sorted by SUM -- they agree everywhere except
the bottom two rows, where count-order and sum-order disagree on which of
'СООО "МИДЕА-ГОРИЗОНТ"' (count 8, sum 10900) and 'ОАО "Минский завод
"Термопласт""' (count 5, sum 13575) ranks higher. So this compares content
(by name), not row order, against Kosovov; the DOCX-matching sum-sorted
order is asserted directly against the DOCX's own table14 numbers instead.
"""


def test_manufacturers_content_matches_kosovov(july_report, kosovov_sheets):
    got = {r.name: (r.count_cur, r.sum_cur, r.sum_prev, r.model_count) for r in july_report.manufacturers.rows}
    expected = {
        name: (count, sum_, prev_sum, model_count)
        for name, count, _prev, _delta_count, sum_, prev_sum, _delta_sum, model_count in kosovov_sheets["Изготовители"]
    }
    assert got == expected


def test_manufacturers_sum_sorted_order_matches_docx(july_report):
    # This is the order that actually ships in the report (sorted by sum
    # descending), verified against the reference DOCX's table14.
    got = [r.name for r in july_report.manufacturers.rows]
    expected = [
        'ПУП "Н-ТиВи"', 'ОАО "МПОВТ"', 'ОАО "Брестский электроламповый завод"',
        "ЗЭБТ Горизонт", 'ООО "Дженерал Электроникс""', 'ОАО "Минский завод "Термопласт""',
        'СООО "МИДЕА-ГОРИЗОНТ"', 'ООО "Хоум Апплиансес"', 'ООО "Мидеа Рефрижератор Мануфактуринг"',
        "Не указан",
    ]
    assert got == expected


def test_manufacturers_leader_follower(july_report):
    assert july_report.manufacturers_leader_follower.render() == (
        'Абсолютным лидером является ПУП "Н-ТиВи" (1129 шт., сумма 2 007 144 ₽). '
        'Наименьшие показатели в выборке зафиксированы у ООО "Мидеа Рефрижератор Мануфактуринг" (2 шт., сумма 5 300 ₽).'
    )


def test_manufacturer_shares_sum_to_100(july_report):
    total_share = sum(r.share_pct for r in july_report.manufacturers.rows)
    assert round(total_share) == 100

"""Section 5 vs. the reference DOCX (tables 6/11/12/13 and narrative text)."""


def test_golden_standard_empty(july_report):
    # Reference: "Центров, полностью соответствующих 'Золотому стандарту', не выявлено."
    # Confirmed to require min_claims_threshold=5 (see app_settings.json) --
    # without it, 7 ASCs with 1-3 claims spuriously qualify.
    assert july_report.golden_standard == []


def test_risk_zone_matches_reference(july_report):
    assert round(july_report.risk_zone.network_anr_share_pct) == 46
    got = {r.asc_name: round(r.anr_share_pct) for r in july_report.risk_zone.rows}
    expected = {
        "ИП Воробьев Сергей Александрович": 90,
        "ИП Волков Сергей Юрьевич": 86,
        "ИП Камалов Мухтар Жолдгалиевич": 75,
        "ИП Пилюгин Роман Юрьевич": 75,
        'ООО "ТЕХХАУС"': 75,
        'ООО "ИНТЕРФЕЙС"': 73,
        'ООО "АРС"': 71,
        "ИП Соболев Геннадий Юрьевич": 70,
        'ООО "РАДУГА"': 70,
        'ООО "СИБ-МАСТЕР"': 70,
        "ИП Гилазова Наталья Магомедаминовна": 100,
    }
    assert got == expected
    assert len(july_report.risk_zone.rows) == 11


def test_duration_extremes_match_reference(july_report):
    d = july_report.duration
    assert d.fastest_asc.asc_name == 'ООО "АРХСЕРВИС-ЦЕНТР"'
    assert round(d.fastest_asc.avg_days) == 0
    assert d.slowest_asc.asc_name == "ИП Филин Сергей Иванович"
    assert round(d.slowest_asc.avg_days) == 88


def test_top10_slow_repairs_matches_reference(july_report):
    got = [(r.asc_name, r.brand, r.model, r.duration_days) for r in july_report.duration.top_slow_repairs]
    expected = [
        ('ООО "МОРОЗКО"', "CARRERA", "CRLG508", 194),
        ('ООО "МОРОЗКО"', "Hi", "HX-32F01FB", 176),
        ('ООО СЕРВИСНЫЙ ЦЕНТР "РУБИН"', "Hi", "HT-32H01FB", 167),
        ('ООО СЕРВИСНЫЙ ЦЕНТР "РУБИН"', "Hi", "HT-40H01FB", 158),
        ('ООО "МОРОЗКО"', "Hi", "HY-43U01FB", 153),
        ("ИП Кириллин Сергей Михайлович", "Skyworth", "32E55G", 152),
        ("ИП Кириллин Сергей Михайлович", "Skyworth", "32E55G", 152),
        ("ИП Филин Сергей Иванович", "Hikers", "32HTF01", 148),
        ('ООО "ТЕХПОДДЕРЖКА"', "HARTENS", "HTM27GC180", 131),
        ('ООО "МОРОЗКО"', "Hi", "HX-32H01FB", 120),
    ]
    assert got == expected


def test_doa_top_counts_match_reference(july_report):
    got_counts = [r.count for r in july_report.doa_rows]
    expected_counts = [42, 24, 16, 9, 9, 9, 7, 7, 6, 6, 6, 6, 6, 5, 5]
    assert got_counts == expected_counts
    assert july_report.doa_rows[0].brand == "TUVIO"
    assert july_report.doa_rows[0].model == "TD43FFBCH11"
    assert july_report.doa_total == 377

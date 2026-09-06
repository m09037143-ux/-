"""Section 2 KPIs vs. the reference report's narrative text (spec §2.1)."""
from repair_report.analytics.common import format_dynamics, format_rub


def test_summary_kpis_match_reference_text(july_report):
    k = july_report.kpis
    assert k.repair_count == 1535
    assert k.total_sum == 2_655_000
    assert round(k.avg_check) == 1730
    assert round(k.avg_duration_days) == 12
    assert k.asc_count == 104
    assert k.region_count == 67
    assert k.manufacturer_count == 10
    assert k.brand_count == 23
    assert k.model_count == 366
    assert k.visits_count == 122
    assert k.visits_sum == 345_925
    assert k.parts_count == 17
    assert k.parts_sum == 10_475


def test_summary_kpi_dynamics_table(july_report):
    cur, prev = july_report.kpis, july_report.kpis_prev
    assert prev.repair_count == 1213
    assert prev.total_sum == 2_086_875
    assert format_dynamics(cur.repair_count, prev.repair_count) == "+322 (+26.5%)"
    assert format_dynamics(cur.total_sum, prev.total_sum, " ₽") == "+568 125 ₽ (+27.2%)"
    # NOTE: the diff is computed from the FULL-PRECISION avg_check values,
    # not from the rounded display values (1730 - 1720 would give +10, but
    # the reference shows +9 -- confirmed by the unrounded diff of 9.22,
    # which rounds to 9). Round-then-subtract is the wrong order of
    # operations here; format_dynamics must receive unrounded inputs.
    assert format_dynamics(cur.avg_check, prev.avg_check, " ₽") == "+9 ₽ (+0.5%)"
    assert format_rub(cur.total_sum) == "2 655 000 ₽"


def test_no_previous_period_kpis_still_compute(july_only_report):
    assert july_only_report.kpis.repair_count == 1535
    assert july_only_report.kpis_prev is None

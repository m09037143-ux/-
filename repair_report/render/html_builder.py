"""Builds the single self-contained HTML report -- the source-of-truth
template also used (as HTML string) by the PDF renderer (weasyprint).
Charts are embedded as base64 data URIs so the .html file is portable on
its own (no sibling image files to lose).
"""
from __future__ import annotations

import base64
import dataclasses
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from repair_report.analytics.common import format_dynamics, format_number, format_rub, repair_level_label
from repair_report.analytics.engine import ReportData
from repair_report.analytics.periods import MONTH_NAMES_RU
from repair_report.config import loader
from repair_report.profile import ClientProfile
from repair_report.render.chart_pipeline import generate_charts

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _b64_image(path: str) -> str:
    data = Path(path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _truncate_table(table, n):
    return dataclasses.replace(table, rows=table.rows[:n])


def _detail_columns():
    from repair_report.analytics.detail import DETAIL_COLUMNS

    return [label for _, label in DETAIL_COLUMNS]


def _build_parts_context(report: ReportData) -> dict | None:
    if report.parts is None:
        return None
    p = report.parts
    window_label = " / ".join(p.window_periods and [str(x) for x in p.window_periods] or []) or "—"
    if not p.has_data_for_window:
        return {"has_data": False, "window_label": window_label}

    st = p.status_by_month
    status_rows = [
        {"status": s, "vals": [st.counts[s][m] for m in st.months], "total": st.row_totals[s]}
        for s in st.statuses
    ]
    return {
        "has_data": True,
        "window_label": window_label,
        "row_count": format_number(p.summary.row_count),
        "unique_orders": format_number(p.summary.unique_orders),
        "total_qty": format_number(p.summary.total_qty_ordered),
        "leader_follower_text": p.summary.leader_follower_text(),
        "months": st.months,
        "status_rows": status_rows,
        "month_totals": [st.month_totals[m] for m in st.months],
        "grand_total": st.grand_total,
        "geography": p.geography,
    }


def _build_support_context(report: ReportData) -> dict | None:
    if report.support is None:
        return None
    s = report.support
    window_label = " / ".join(s.window_periods and [str(x) for x in s.window_periods] or []) or "—"
    if not s.has_data_for_window:
        return {"has_data": False, "window_label": window_label}

    return {
        "has_data": True,
        "window_label": window_label,
        "ticket_count": format_number(s.summary.ticket_count),
        "closed_share_fmt": f"{s.summary.closed_share_pct:.1f}%",
        "leader_follower_text": s.summary.leader_follower_text(),
        "organizations": s.organizations,
        "topics": s.topics,
        "quality_audit": s.quality_audit,
        "no_engineers_note": (
            "Данные не найдены — поле «Кто ответил» пустое во всей загруженной выгрузке "
            "(этот раздел отсутствует и в эталонном отчёте по той же причине)."
        ),
    }


def build_html(report: ReportData, profile: ClientProfile, work_dir: str | Path, chart_paths: dict[str, str] | None = None) -> str:
    """Returns the rendered HTML as a string.

    `chart_paths`: pre-generated chart PNGs (see chart_pipeline.generate_charts).
    Pass this in (shared across HTML/PDF/DOCX for one report run) to avoid
    re-rendering every matplotlib chart 3 times over; if omitted, this
    function generates its own (useful for calling build_html standalone).
    """
    work_dir = Path(work_dir)
    if chart_paths is None:
        chart_paths = generate_charts(report, work_dir / "charts")
    charts_b64 = {k: _b64_image(v) for k, v in chart_paths.items()}

    settings = loader.app_settings()
    top_n = settings["top_n_default"]

    kpi = report.kpis
    kpi_prev = report.kpis_prev
    has_prev = kpi_prev is not None

    def dyn(cur, prev, unit=""):
        return format_dynamics(cur, prev, unit) if has_prev else "н/д"

    summary_narrative = (
        f"Представленные данные отражают фактическое количество ремонтируемой техники за период из целевого файла .xlsx. "
        f"В текущем периоде в обслуживании приняли участие {kpi.asc_count} АСЦ из {kpi.region_count} регионов. "
        f"Всего выполнено {format_number(kpi.repair_count)} ремонтов на общую сумму {format_rub(kpi.total_sum)}. "
        f"Средний срок ремонта по сети составил {round(kpi.avg_duration_days) if kpi.avg_duration_days is not None else '—'} дн. "
        f"Суммарно зафиксировано {kpi.visits_count} выездов на сумму {format_rub(kpi.visits_sum)}. "
        f"Запасные части использованы в {kpi.parts_count} ремонтах на сумму {format_rub(kpi.parts_sum)}. "
        f"В ремонтном массиве представлена техника от {kpi.manufacturer_count} изготовителей, охватывающая "
        f"{kpi.brand_count} брендов и {kpi.model_count} уникальных моделей."
    )

    duration_narrative = "Недостаточно данных для расчёта сроков ремонта."
    d = report.duration
    if d.fastest_asc and d.slowest_asc:
        duration_narrative = (
            f"Самые быстрые ремонты показывает АСЦ «{d.fastest_asc.asc_name}» (ср. срок {round(d.fastest_asc.avg_days)} дн.), "
            f"самые затяжные — АСЦ «{d.slowest_asc.asc_name}» (ср. срок {round(d.slowest_asc.avg_days)} дн.)."
        )

    manufacturers_ctx = [
        {
            "name": r.name,
            "sum_fmt": format_rub(r.sum_cur),
            "share_fmt": f"{r.share_pct:.1f}%",
            "sum_prev_fmt": format_rub(r.sum_prev) if r.sum_prev is not None else "н/д",
            "count_cur": format_number(r.count_cur),
            "model_count": r.model_count,
        }
        for r in report.manufacturers.rows
    ]

    golden_ctx = [
        {
            "asc_name": r.asc_name,
            "claims": r.claims,
            "avg_check_fmt": format_rub(r.avg_check),
            "anr_share_fmt": f"{r.anr_share_pct:.0f}%",
        }
        for r in report.golden_standard
    ]
    risk_ctx = [
        {"asc_name": r.asc_name, "claims": r.claims, "anr_share_fmt": f"{r.anr_share_pct:.0f}%"}
        for r in report.risk_zone.rows
    ]

    visits_rows_ctx = [
        {"name": r.name, "visit_count": format_number(r.visit_count), "visit_sum_fmt": format_rub(r.visit_sum)}
        for r in report.asc_visits.top(settings["top_n_asc_visits"])
    ]

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
    env.globals["format_number"] = format_number
    env.globals["format_rub"] = format_rub
    env.globals["format_dynamics"] = lambda cur, prev: dyn(cur, prev)
    template = env.get_template("report.html.jinja")

    period = report.current_period
    context = dict(
        report_title=f"Отчёт {period.year} {MONTH_NAMES_RU[period.month]}",
        period_label=str(period),
        generated_date=__import__("datetime").date.today().strftime("%d.%m.%Y"),
        profile=profile,
        previous_available=report.previous_available,
        previous_note=report.previous_note,
        asc_registry=report.asc_registry,
        equipment_registry=report.equipment_registry,
        summary_narrative=summary_narrative,
        kpi_count_cur=format_number(kpi.repair_count),
        kpi_count_prev=format_number(kpi_prev.repair_count) if has_prev else "н/д",
        kpi_count_dyn=dyn(kpi.repair_count, kpi_prev.repair_count if has_prev else None),
        kpi_sum_cur=format_rub(kpi.total_sum),
        kpi_sum_prev=format_rub(kpi_prev.total_sum) if has_prev else "н/д",
        kpi_sum_dyn=dyn(kpi.total_sum, kpi_prev.total_sum if has_prev else None, " ₽"),
        kpi_avg_cur=format_rub(kpi.avg_check),
        kpi_avg_prev=format_rub(kpi_prev.avg_check) if has_prev else "н/д",
        kpi_avg_dyn=dyn(kpi.avg_check, kpi_prev.avg_check if has_prev else None, " ₽"),
        charts=charts_b64,
        equipment_by_sum=report.equipment_by_sum,
        equipment_by_count=report.equipment_by_count,
        repair_level=report.repair_level,
        repair_level_labels={r.name: repair_level_label(r.name) for r in report.repair_level.rows},
        regions_top=_truncate_table(report.regions, top_n),
        asc_top=_truncate_table(report.asc, top_n),
        brands_top=_truncate_table(report.brands, top_n),
        asc_leader_follower=(report.asc_leader_follower.render() if report.asc_leader_follower else ""),
        asc_visits_leader_follower=(report.asc_visits_leader_follower.render() if report.asc_visits_leader_follower else ""),
        brands_leader_follower=(report.brands_leader_follower.render() if report.brands_leader_follower else ""),
        asc_visits_top=visits_rows_ctx,
        asc_visits_total_count=format_number(report.asc_visits.total_count),
        asc_visits_total_sum=format_rub(report.asc_visits.total_sum),
        golden_max_anr_pct=settings["golden_standard"]["max_anr_share_pct"],
        golden_standard=golden_ctx,
        risk_zone_multiplier=settings["risk_zone"]["multiplier_over_network_avg"],
        risk_zone_network_pct=round(report.risk_zone.network_anr_share_pct),
        risk_zone_rows=risk_ctx,
        duration_narrative=duration_narrative,
        top_slow_repairs=report.duration.top_slow_repairs,
        doa_max_days=settings["doa"]["max_days"],
        doa_rows=report.doa_rows,
        manufacturers=manufacturers_ctx,
        manufacturers_leader_follower=(report.manufacturers_leader_follower.render() if report.manufacturers_leader_follower else ""),
        tv_diagonal=report.tv_diagonal,
        tv_brand_models=report.tv_brand_models,
        tv_model_leader_follower=(report.tv_model_leader_follower.render() if report.tv_model_leader_follower else ""),
        iris_chains=report.iris_chains,
        iris_chain_leader_follower=(
            f"Абсолютным лидером является {report.iris_chains[0].chain} ({report.iris_chains[0].count} шт.). "
            f"Наименьшие показатели зафиксированы у {report.iris_chains[-1].chain} ({report.iris_chains[-1].count} шт.)."
            if report.iris_chains else ""
        ),
        defect_texts=report.defect_texts,
        iris_errors=report.iris_errors,
        fraud_rows=report.fraud_rows,
        parts=_build_parts_context(report),
        support=_build_support_context(report),
        detail_columns=_detail_columns(),
        detail_rows=report.detail_rows,
    )
    return template.render(**context)


def save_html(report: ReportData, profile: ClientProfile, out_path: str | Path, work_dir: str | Path, chart_paths: dict[str, str] | None = None) -> None:
    html = build_html(report, profile, work_dir, chart_paths)
    Path(out_path).write_text(html, encoding="utf-8")

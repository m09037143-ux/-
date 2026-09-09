"""Generates every chart PNG for a ReportData once, shared by all 3 output
formats (spec §3/§4)."""
from __future__ import annotations

from pathlib import Path

from repair_report.analytics.engine import ReportData
from repair_report.charts import chart_builder as cb
from repair_report.config import loader


def generate_charts(report: ReportData, out_dir: str | Path) -> dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = loader.app_settings()
    top_n = settings["top_n_default"]
    paths: dict[str, str] = {}

    def bar(key: str, rows, name_attr="name", value_attr="count_cur", value_format="int", n=top_n, single_color=None):
        rows = rows[:n]
        p = out_dir / f"{key}.png"
        cb.horizontal_bar_chart(
            str(p),
            [getattr(r, name_attr) for r in rows],
            [getattr(r, value_attr) for r in rows],
            value_format=value_format,
            single_color=single_color,
        )
        paths[key] = str(p)

    # Section 2: cost-structure pie (Услуги / Выезды / Запчасти)
    services_sum = report.kpis.total_sum - report.kpis.visits_sum - report.kpis.parts_sum
    pie_path = out_dir / "section2_pie.png"
    cb.pie_chart(str(pie_path), ["Услуги", "Выезды", "Запчасти"], [services_sum, report.kpis.visits_sum, report.kpis.parts_sum])
    paths["section2_pie"] = str(pie_path)

    # Section 3
    bar("section3_by_sum", report.equipment_by_sum.rows, value_attr="sum_cur", value_format="rub", n=len(report.equipment_by_sum.rows))
    bar("section3_by_count", report.equipment_by_count.rows, n=len(report.equipment_by_count.rows))
    bar("section3_level", report.repair_level.rows, n=len(report.repair_level.rows))

    # Section 4
    bar("section4_regions", report.regions.rows, n=top_n)
    bar("section4_asc", report.asc.rows, n=top_n)
    bar("section4_brands", report.brands.rows, n=top_n)
    bar("section4_1_visits", report.asc_visits.rows, name_attr="name", value_attr="visit_count", n=settings["top_n_asc_visits"])

    # Section 6 -- manufacturers, by sum
    bar("section6_manufacturers", report.manufacturers.rows, value_attr="sum_cur", value_format="rub", n=len(report.manufacturers.rows))

    # Section 7.1 -- TV diagonals
    bar("section7_1_diagonal", report.tv_diagonal.rows, n=len(report.tv_diagonal.rows))

    # Section 10 -- only when a parts file was supplied (see engine.py)
    if report.parts is not None and report.parts.has_data_for_window:
        st = report.parts.status_by_month
        series = {status: [st.counts[status][m] for m in st.months] for status in st.statuses}
        status_colors = {
            "Зарезервирована на складе": "#4682b4",
            "Отгружена": "#2e7d32",
            "Отказана менеджером": "#c00000",
            "Рассматривается": "#e0a800",
        }
        p10_1 = out_dir / "section10_1_status.png"
        cb.stacked_vertical_bar_chart(str(p10_1), st.months, series, series_colors=status_colors)
        paths["section10_1_status"] = str(p10_1)

        geo = report.parts.geography[:top_n]
        p10_2 = out_dir / "section10_2_geo.png"
        cb.threshold_colored_bar_chart(
            str(p10_2),
            [r.city for r in geo],
            [r.fulfillment_pct for r in geo],
        )
        paths["section10_2_geo"] = str(p10_2)

    return paths

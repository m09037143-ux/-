"""DOCX export -- structurally mirrors the same sections/tables/charts as the
HTML/PDF renderer, built natively with python-docx (Word's table/pagination
model is different enough from HTML/CSS that sharing markup isn't practical,
but every number and PNG chart comes from the same ReportData + chart cache
so the three formats never disagree on content)."""
from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

from repair_report.analytics.common import format_dynamics, format_number, format_rub, repair_level_label
from repair_report.analytics.detail import DETAIL_COLUMNS
from repair_report.analytics.engine import ReportData
from repair_report.analytics.parts_support import SECTION_10_PLACEHOLDER, SECTION_11_PLACEHOLDER
from repair_report.config import loader
from repair_report.profile import ClientProfile
from repair_report.render.chart_pipeline import generate_charts

HEADER_BLUE = RGBColor(0x1F, 0x38, 0x64)


def _heading(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    return h


def _add_table(doc, headers, rows, col_aligns=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = str(h)
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = "" if val is None else str(val)
    return table


def _dynamics_rows(table, has_previous, name_map=None, money=True):
    rows = []
    for r in table.rows:
        name = name_map.get(r.name, r.name) if name_map else r.name
        rows.append(
            [
                name,
                format_number(r.count_cur),
                format_number(r.count_prev) if has_previous else "н/д",
                format_dynamics(r.count_cur, r.count_prev) if has_previous else "н/д",
                format_rub(r.sum_cur) if money else format_number(r.sum_cur),
            ]
        )
    rows.append(
        [
            "ИТОГ",
            format_number(table.total_count_cur),
            format_number(table.total_count_prev) if has_previous else "н/д",
            format_dynamics(table.total_count_cur, table.total_count_prev) if has_previous else "н/д",
            format_rub(table.total_sum_cur) if money else format_number(table.total_sum_cur),
        ]
    )
    return rows


def save_docx(report: ReportData, profile: ClientProfile, out_path: str | Path, work_dir: str | Path) -> None:
    work_dir = Path(work_dir)
    charts = generate_charts(report, work_dir / "charts")
    settings = loader.app_settings()
    top_n = settings["top_n_default"]
    has_prev = report.previous_available

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    title = doc.add_paragraph()
    run = title.add_run("ЕЖЕМЕСЯЧНЫЙ ОТЧЕТ О ВЫПОЛНЕННОЙ РАБОТЕ")
    run.bold = True
    run.font.size = Pt(18)

    doc.add_paragraph(f"Период: {report.current_period}\nСформирован: {datetime.date.today().strftime('%d.%m.%Y')}")

    header_tbl = doc.add_table(rows=3, cols=2)
    header_tbl.rows[0].cells[0].text = "Исполнитель"
    header_tbl.rows[0].cells[1].text = profile.executor
    header_tbl.rows[1].cells[0].text = "Заказчик"
    header_tbl.rows[1].cells[1].text = profile.customer
    header_tbl.rows[2].cells[0].text = "Договор"
    header_tbl.rows[2].cells[1].text = f"№ {profile.contract_number} от {profile.contract_date}"

    if not report.previous_available:
        doc.add_paragraph(report.previous_note).italic = True

    # Section 1
    _heading(doc, "1. Реестры текущего отчетного периода")
    doc.add_paragraph("Ниже представлены списки участников отчета и обслуживаемой техники.")
    _heading(doc, "1.1 Список сервисных центров", level=2)
    _add_table(
        doc,
        ["№пп", "Наименование АСЦ", "Код АСЦ", "Регион АСЦ"],
        [[r.seq, r.asc_name, r.asc_code, r.region] for r in report.asc_registry],
    )
    _heading(doc, "1.2 Общий список обслуживаемой техники", level=2)
    _add_table(
        doc,
        ["Категория техники", "Бренд", "Модель", "Количество случаев"],
        [[r.category, r.brand, r.model, r.count] for r in report.equipment_registry],
    )

    # Section 2
    doc.add_page_break()
    _heading(doc, "2. Управленческое резюме")
    kpi, kpi_prev = report.kpis, report.kpis_prev
    narrative = (
        f"Представленные данные отражают фактическое количество ремонтируемой техники за период из целевого файла .xlsx. "
        f"В текущем периоде в обслуживании приняли участие {kpi.asc_count} АСЦ из {kpi.region_count} регионов. "
        f"Всего выполнено {format_number(kpi.repair_count)} ремонтов на общую сумму {format_rub(kpi.total_sum)}. "
        f"Средний срок ремонта по сети составил {round(kpi.avg_duration_days) if kpi.avg_duration_days is not None else '—'} дн. "
        f"Суммарно зафиксировано {kpi.visits_count} выездов на сумму {format_rub(kpi.visits_sum)}. "
        f"Запасные части использованы в {kpi.parts_count} ремонтах на сумму {format_rub(kpi.parts_sum)}. "
        f"В ремонтном массиве представлена техника от {kpi.manufacturer_count} изготовителей, охватывающая "
        f"{kpi.brand_count} брендов и {kpi.model_count} уникальных моделей."
    )
    doc.add_paragraph(narrative)
    kpi_rows = [
        ["Количество ремонтов", format_number(kpi.repair_count), format_number(kpi_prev.repair_count) if has_prev else "н/д",
         format_dynamics(kpi.repair_count, kpi_prev.repair_count) if has_prev else "н/д"],
        ["Общая сумма", format_rub(kpi.total_sum), format_rub(kpi_prev.total_sum) if has_prev else "н/д",
         format_dynamics(kpi.total_sum, kpi_prev.total_sum, " ₽") if has_prev else "н/д"],
        ["Средний чек", format_rub(kpi.avg_check), format_rub(kpi_prev.avg_check) if has_prev else "н/д",
         format_dynamics(kpi.avg_check, kpi_prev.avg_check, " ₽") if has_prev else "н/д"],
    ]
    _add_table(doc, ["Показатель", "Текущий", "Предыдущий", "Динамика"], kpi_rows)
    doc.add_picture(charts["section2_pie"], width=Inches(4.5))

    # Section 3
    doc.add_page_break()
    _heading(doc, "3. Структура затрат по видам техники")
    _heading(doc, "Затраты по категориям", level=2)
    _add_table(doc, ["Вид техники", "Кол-во тек.", "Кол-во пред.", "Динамика", "Сумма тек."], _dynamics_rows(report.equipment_by_sum, has_prev))
    doc.add_picture(charts["section3_by_sum"], width=Inches(6))

    _heading(doc, "По категориям (в разрезе количества)", level=2)
    _add_table(doc, ["Категория", "Кол-во тек.", "Кол-во пред.", "Динамика", "Сумма тек."], _dynamics_rows(report.equipment_by_count, has_prev))
    doc.add_picture(charts["section3_by_count"], width=Inches(6))

    _heading(doc, "По уровням ремонта", level=2)
    level_labels = {r.name: repair_level_label(r.name) for r in report.repair_level.rows}
    _add_table(doc, ["Уровень ремонта", "Кол-во тек.", "Кол-во пред.", "Динамика", "Сумма тек."], _dynamics_rows(report.repair_level, has_prev, name_map=level_labels))
    doc.add_picture(charts["section3_level"], width=Inches(6))

    # Section 4
    doc.add_page_break()
    _heading(doc, "4. Разрезы данных")
    _heading(doc, "По регионам", level=2)
    regions_top = dataclasses.replace(report.regions, rows=report.regions.rows[:top_n])
    _add_table(doc, ["Регион", "Кол-во ремонтов", "Пред. кол-во", "Динамика", "Сумма тек."], _dynamics_rows(regions_top, has_prev))
    doc.add_picture(charts["section4_regions"], width=Inches(6))

    _heading(doc, "ТОП-15 АСЦ по количеству ремонтов", level=2)
    doc.add_paragraph("Максимальная и минимальная активность АСЦ").bold = True
    if report.asc_leader_follower:
        doc.add_paragraph(report.asc_leader_follower.render())
    asc_top = dataclasses.replace(report.asc, rows=report.asc.rows[:top_n])
    _add_table(doc, ["Наименование АСЦ", "Кол-во тек.", "Кол-во пред.", "Динамика", "Сумма тек."], _dynamics_rows(asc_top, has_prev))
    doc.add_picture(charts["section4_asc"], width=Inches(6))

    _heading(doc, "4.1 ТОП-15 АСЦ по затратам на выезд", level=2)
    doc.add_paragraph("Сводка по выездному обслуживанию АСЦ").bold = True
    if report.asc_visits_leader_follower:
        doc.add_paragraph(report.asc_visits_leader_follower.render())
    visits_top = report.asc_visits.top(settings["top_n_asc_visits"])
    visits_rows = [[r.name, format_number(r.visit_count), format_rub(r.visit_sum)] for r in visits_top]
    visits_rows.append(["ИТОГ", format_number(report.asc_visits.total_count), format_rub(report.asc_visits.total_sum)])
    _add_table(doc, ["Наименование АСЦ", "Кол-во выездов", "Сумма выездов"], visits_rows)
    doc.add_picture(charts["section4_1_visits"], width=Inches(6))

    _heading(doc, "По брендам", level=2)
    doc.add_paragraph("Доминирующие и редко поступающие в ремонт бренды").bold = True
    if report.brands_leader_follower:
        doc.add_paragraph(report.brands_leader_follower.render())
    brands_top = dataclasses.replace(report.brands, rows=report.brands.rows[:top_n])
    _add_table(doc, ["Бренд", "Кол-во тек.", "Кол-во пред.", "Динамика", "Сумма тек."], _dynamics_rows(brands_top, has_prev))
    doc.add_picture(charts["section4_brands"], width=Inches(6))

    # Section 5
    doc.add_page_break()
    _heading(doc, "5. Эффективность АСЦ, Качество и SLA")
    _heading(doc, "5.1 Золотой стандарт (Отличники)", level=2)
    gs_settings = settings["golden_standard"]
    doc.add_paragraph(f"АСЦ с низким чеком, идеальным заполнением кодов IRIS и долей НРП < {gs_settings['max_anr_share_pct']:.0f}%.")
    if report.golden_standard:
        _add_table(
            doc,
            ["Наименование АСЦ", "Заявок", "Средний чек", "Доля НРП"],
            [[r.asc_name, r.claims, format_rub(r.avg_check), f"{r.anr_share_pct:.0f}%"] for r in report.golden_standard],
        )
    else:
        doc.add_paragraph("Центров, полностью соответствующих «Золотому стандарту», не выявлено.")

    _heading(doc, "5.2 Зона риска (Аномально высокий НРП)", level=2)
    rz_settings = settings["risk_zone"]
    doc.add_paragraph(
        f"Доля актов о неремонтопригодности превышает среднюю по сети в {rz_settings['multiplier_over_network_avg']} раза "
        f"(Ср. по сети: {round(report.risk_zone.network_anr_share_pct)}%)."
    )
    _add_table(
        doc,
        ["Наименование АСЦ", "Заявок", "Доля_НРП"],
        [[r.asc_name, r.claims, f"{r.anr_share_pct:.0f}%"] for r in report.risk_zone.rows],
    )

    _heading(doc, "5.3 Анализ сроков ремонтов", level=2)
    d = report.duration
    if d.fastest_asc and d.slowest_asc:
        doc.add_paragraph(
            f"Самые быстрые ремонты показывает АСЦ «{d.fastest_asc.asc_name}» (ср. срок {round(d.fastest_asc.avg_days)} дн.), "
            f"самые затяжные — АСЦ «{d.slowest_asc.asc_name}» (ср. срок {round(d.slowest_asc.avg_days)} дн.)."
        )
    doc.add_paragraph("ТОП-10 самых долгих ремонтов").bold = True
    _add_table(
        doc,
        ["Наименование АСЦ", "Бренд", "Модель", "Срок_ремонта_дней"],
        [[r.asc_name, r.brand, r.model, f"{r.duration_days} дн."] for r in d.top_slow_repairs],
    )

    _heading(doc, "5.4 Брак из коробки (DOA < {} дней)".format(settings["doa"]["max_days"]), level=2)
    doc.add_paragraph("Модели техники, вышедшие из строя в течение первых 30 дней после продажи.")
    _add_table(doc, ["Бренд", "Модель", "Кол-во DOA"], [[r.brand, r.model, r.count] for r in report.doa_rows])

    # Section 6
    doc.add_page_break()
    _heading(doc, "6. Аналитика по изготовителям")
    doc.add_paragraph("Распределение брака по заводам-изготовителям").bold = True
    if report.manufacturers_leader_follower:
        doc.add_paragraph(report.manufacturers_leader_follower.render())
    _add_table(
        doc,
        ["Изготовитель", "Сумма (текущ)", "Доля %", "Сумма (пред)", "Кол-во рем.", "Кол-во моделей"],
        [
            [r.name, format_rub(r.sum_cur), f"{r.share_pct:.1f}%", format_rub(r.sum_prev) if r.sum_prev is not None else "н/д", r.count_cur, r.model_count]
            for r in report.manufacturers.rows
        ],
    )
    doc.add_picture(charts["section6_manufacturers"], width=Inches(6))

    # Section 7
    doc.add_page_break()
    _heading(doc, "7. Анализ ТВ техники")
    _heading(doc, "7.1. Распределение ТВ по диагоналям", level=2)
    _add_table(doc, ["ТВ с диагональю", "Кол-во тек.", "Кол-во пред.", "Динамика", "Сумма тек."], _dynamics_rows(report.tv_diagonal, has_prev))
    doc.add_picture(charts["section7_1_diagonal"], width=Inches(6))

    _heading(doc, "7.2. Топ моделей по брендам ТВ", level=2)
    doc.add_paragraph("Наиболее и наименее проблемные модели ТВ").bold = True
    if report.tv_model_leader_follower:
        doc.add_paragraph(report.tv_model_leader_follower.render())
    for b in report.tv_brand_models:
        doc.add_paragraph(f"Бренд: {b.brand}").bold = True
        _add_table(doc, ["Модель", "Кол-во ремонтов"], [[m.model, m.count] for m in b.models])

    # Section 8
    doc.add_page_break()
    _heading(doc, "8. Аналитика по дефектам и IRIS кодам (ТВ техника)")
    _heading(doc, "8.1. Обобщение: Самые частые цепочки IRIS кодов", level=2)
    doc.add_paragraph("Частотность возникновения дефектов (IRIS)").bold = True
    if report.iris_chains:
        doc.add_paragraph(
            f"Абсолютным лидером является {report.iris_chains[0].chain} ({report.iris_chains[0].count} шт.). "
            f"Наименьшие показатели зафиксированы у {report.iris_chains[-1].chain} ({report.iris_chains[-1].count} шт.)."
        )
    _add_table(doc, ["Секция → Дефект → Ремонт", "Кол-во"], [[r.chain, r.count] for r in report.iris_chains])

    _heading(doc, "8.2. Самые частые заявленные дефекты (Текст)", level=2)
    _add_table(doc, ["Описание дефекта", "Кол-во ремонтов"], [[r.text, r.count] for r in report.defect_texts])

    _heading(doc, "8.3. Ошибки кодирования IRIS", level=2)
    if report.iris_errors:
        _add_table(
            doc,
            ["АСЦ", "Бренд", "Модель", "Секция", "Дефект", "Ремонт", "Ошибки"],
            [[r.asc_name, r.brand, r.model, r.section, r.defect, r.repair, r.errors] for r in report.iris_errors],
        )
    else:
        doc.add_paragraph("Ошибок кодирования IRIS для телевизоров не выявлено.")

    # Section 9
    doc.add_page_break()
    _heading(doc, "9. Контроль Фрода и СБ")
    doc.add_paragraph("Подозрительные совпадения телефонов (накрутка)").bold = True
    _add_table(doc, ["Телефон", "Клиент", "Уникальных аппаратов"], [[r.phone, r.client, r.unique_devices] for r in report.fraud_rows])

    # Sections 10-11 (experimental)
    if report.experimental_sections_enabled:
        doc.add_page_break()
        _heading(doc, SECTION_10_PLACEHOLDER.title)
        doc.add_paragraph(SECTION_10_PLACEHOLDER.message)
        _add_table(doc, SECTION_10_PLACEHOLDER.column_layout, [])

        _heading(doc, SECTION_11_PLACEHOLDER.title)
        doc.add_paragraph(SECTION_11_PLACEHOLDER.message)
        _add_table(doc, SECTION_11_PLACEHOLDER.column_layout, [])

    # Section 12
    doc.add_page_break()
    _heading(doc, "12. Детализация ремонтов (Текущий период)")
    doc.add_paragraph("Реестр всех строк из выгрузки актуального периода.")
    labels = [label for _, label in DETAIL_COLUMNS]
    _add_table(doc, labels, [[row.get(lbl, "") for lbl in labels] for row in report.detail_rows])

    doc.add_paragraph("")
    doc.add_paragraph(f"{profile.signatory_title} __________________ / {profile.signatory_name} /")
    p = doc.add_paragraph("— Конец отчёта —")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.save(str(out_path))

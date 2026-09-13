"""Builds the AI-summary prompt from an already-built ReportData and calls
client.request_completion(). See docs/REVERSE_ENGINEERING.md §16.

Per explicit product decision: only AGGREGATED figures and the report's
own narrative blurbs are sent to the external AI service -- never
per-repair detail rows (section 12), customer names, serial numbers, or
phone numbers. build_digest() below is the complete list of what leaves
the network; if you're tempted to add something, check it's a summary
statistic and not a raw record first.
"""
from __future__ import annotations

from repair_report.ai.client import AIRequestError, AIResponseError, request_completion
from repair_report.ai_settings import AiSettings
from repair_report.analytics.common import format_number, format_rub
from repair_report.analytics.engine import ReportData

MAX_SUMMARY_CHARS = 11_000  # hard backstop for "не более двух страниц А4" alongside max_output_tokens below

INSTRUCTIONS = (
    "Ты — аналитик, который готовит краткое управленческое резюме для ежемесячного/квартального/годового "
    "отчёта сервисной сети по ремонту техники. На основе приведённых ниже агрегированных показателей "
    "напиши связное резюме на русском языке объёмом НЕ БОЛЕЕ ДВУХ СТРАНИЦ А4 (примерно 900-1200 слов, "
    "короче — тоже нормально). Пиши деловым, нейтральным языком, абзацами (разделяй абзацы пустой строкой), "
    "без заголовков, без списков, без markdown-разметки. Опирайся только на переданные цифры, ничего не "
    "придумывай и не оценивай то, чего нет в данных. Если данных для сравнения с предыдущим периодом нет, "
    "не делай выводов о динамике."
)


def build_digest(report: ReportData) -> str:
    kpi = report.kpis
    lines = [
        f"Отчётный период: {report.current_span}.",
    ]
    if report.previous_available and report.kpis_prev:
        prev = report.kpis_prev
        lines.append(
            f"Предыдущий сопоставимый период: {report.previous_span} "
            f"(ремонтов: {prev.repair_count}, сумма: {format_rub(prev.total_sum)})."
        )
    else:
        lines.append("Сравнение с предыдущим периодом недоступно (нет данных за предыдущий период в файле).")
    if not report.current_span_complete:
        lines.append("Внимание: выбранный период представлен в файле не полностью (охвачены не все месяцы).")

    lines += [
        "",
        "Основные показатели за период:",
        f"- Количество ремонтов: {format_number(kpi.repair_count)}",
        f"- Общая сумма: {format_rub(kpi.total_sum)}",
        f"- Средний чек: {format_rub(kpi.avg_check)}",
        f"- Средний срок ремонта: {round(kpi.avg_duration_days) if kpi.avg_duration_days is not None else 'н/д'} дн.",
        f"- АСЦ в работе: {kpi.asc_count}, регионов: {kpi.region_count}",
        f"- Изготовителей: {kpi.manufacturer_count}, брендов: {kpi.brand_count}, моделей: {kpi.model_count}",
        f"- Выездов: {kpi.visits_count} на сумму {format_rub(kpi.visits_sum)}",
        f"- Ремонтов с использованием запчастей: {kpi.parts_count} на сумму {format_rub(kpi.parts_sum)}",
    ]

    if report.monthly_dynamics:
        lines += ["", "Динамика по месяцам внутри периода:"]
        for r in report.monthly_dynamics:
            lines.append(f"- {r.period}: {r.repair_count} ремонтов, {format_rub(r.total_sum)}")

    if report.equipment_by_sum.rows:
        lines += ["", "Топ видов техники по сумме затрат:"]
        for r in report.equipment_by_sum.rows[:5]:
            lines.append(f"- {r.name}: {format_rub(r.sum_cur)}")

    if report.asc_leader_follower:
        lines += ["", "Активность АСЦ: " + report.asc_leader_follower.render()]

    if report.golden_standard:
        lines.append(f"АСЦ, соответствующих «золотому стандарту» качества: {len(report.golden_standard)}.")
    if report.risk_zone.rows:
        lines.append(
            f"АСЦ в зоне риска по доле неремонтопригодности: {len(report.risk_zone.rows)} "
            f"(средняя доля по сети: {round(report.risk_zone.network_anr_share_pct)}%)."
        )
    if report.doa_total:
        lines.append(f"Случаев брака из коробки (DOA): {report.doa_total}.")
    if report.fraud_rows:
        lines.append(f"Подозрительных совпадений телефонов (возможный фрод): {len(report.fraud_rows)}.")

    if report.parts is not None and report.parts.has_data_for_window:
        p = report.parts.summary
        parts_line = (
            f"Запасные части (раздел 10): {format_number(p.row_count)} строк, "
            f"{format_number(p.unique_orders)} заказов, {format_number(p.total_qty_ordered)} шт. заказано."
        )
        lines += ["", parts_line]
    if report.support is not None and report.support.has_data_for_window:
        s = report.support.summary
        support_line = (
            f"Техническая поддержка (раздел 11): {format_number(s.ticket_count)} обращений, "
            f"доля закрытых: {s.closed_share_pct:.1f}%."
        )
        lines += ["", support_line]

    return "\n".join(lines)


def generate_summary(report: ReportData, settings: AiSettings) -> str:
    """Raises AIRequestError/AIResponseError on failure -- the caller (see
    ui/worker.py) is responsible for catching these and turning them into
    the report's "ERROR: ..." convention rather than letting them abort
    the whole report generation."""
    if not settings.is_configured():
        raise AIRequestError("не заданы параметры ИИ (API-ключ / Folder ID) — заполните их в «Настройках ИИ»")

    digest = build_digest(report)
    text = request_completion(
        api_key=settings.api_key,
        folder_id=settings.folder_id,
        model=settings.effective_model(),
        instructions=INSTRUCTIONS,
        input_text=digest,
        max_output_tokens=1500,
    )
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…\n\n[Резюме обрезано до объёма ~2 страниц А4.]"
    return text


__all__ = ["AIRequestError", "AIResponseError", "build_digest", "generate_summary"]

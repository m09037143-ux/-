"""Reads the spare-parts logistics export (a SEPARATE, OPTIONAL file from the
main WR_Consolidated_List_*.xlsx) that section 10 needs.

This is a genuinely different data source (a parts-ordering/logistics
system), not a variant of the main repair export -- see
docs/REVERSE_ENGINEERING.md §13 for the full investigation, including how
its column names, ASC-code system, and date semantics were confirmed to
line up with the main export (same 'Код АСЦ' numbering) despite being an
independent file.

Same robustness principle as ingest/excel_reader.py: match columns by
(normalized) NAME, fail loudly and specifically if a critical column is
missing, never guess by position.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# key -> (expected raw header, is_critical)
CANONICAL_COLUMNS: dict[str, tuple[str, bool]] = {
    "order_number": ("Номер заказа", True),
    "order_created_date": ("Дата создания заказа", False),
    "tracking_number": ("№ трекинга", False),
    "model": ("Модель", False),
    "request_number": ("Номер заявки", False),
    "line_status": ("Статус линии заказа", True),
    "ordered_part_number": ("Заказанный партномер", True),
    "ordered_part_name": ("Заказанный партномер наименование", False),
    "shipped_part_number": ("Партномер к отгрузке", False),
    "shipped_part_name": ("Партномер к отгрузке наименование", False),
    "request_created_date": ("Дата создания заявки", False),
    "order_sent_date": ("Дата отправки заказа", True),
    "customer_name": ("Заказчик", True),
    "customer_code": ("Код Заказчика", False),
    "consignee": ("Грузополучатель", False),
    "delivery_address": ("Адрес доставки", True),
    "order_note": ("Примечание к заказу", False),
    "comment": ("Комментарий", False),
    "order_status": ("Статус заказа", False),
    "order_type": ("Тип заказа", False),
    "cancel_date": ("Дата отмены", False),
    "request_status_note": ("Пояснение к статусу заявки", False),
    "estimated_ship_date": ("Ориентировочная дата отгрузки", False),
    "requested_total_qty": ("Заказано по заявке всего", True),
    "line_status_qty": ("Количество линии в статусе", False),
    "shipped_qty": ("Количество отгруженное", False),
    "price": ("Цена", False),
    "currency": ("Валюта", False),
    "request_note": ("Примечание к заявке", False),
    "serial_number": ("Серийный номер", False),
    "asc_request_number": ("Номер заявки АСЦ", False),
    "invoice_number": ("Номер накладной", False),
    "torg12_number": ("№ ТОРГ12", False),
    "ttn_number": ("№ ТТН", False),
    "repair_receipt_date": ("Дата приёма в ремонт", False),
    "shipment_date": ("Дата отгрузки", False),
    "refused_no_stock": ("Отказ от заявки если ЗЧ нет на складе", False),
    "request_load_error": ("Ошибка загрузки заявки", False),
    "part_type": ("Тип ЗЧ", False),
    "repair_level": ("Уровень ремонта", False),
    "repair_status": ("Статус ремонта", False),
    "repair_type": ("Тип ремонта", False),
    "part_brand": ("Бренд запчасти", False),
}

DATE_COLUMNS = [
    "order_created_date", "request_created_date", "order_sent_date",
    "cancel_date", "repair_receipt_date", "shipment_date",
]
NUMERIC_COMMA_COLUMNS = ["requested_total_qty", "line_status_qty", "shipped_qty", "price"]


class PartsColumnResolutionError(RuntimeError):
    pass


@dataclass
class PartsColumnReport:
    matched: dict[str, str] = field(default_factory=dict)
    fuzzy_matched: dict[str, str] = field(default_factory=dict)
    missing_critical: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_critical


def _normalize_header(name: object) -> str:
    if name is None:
        return ""
    text = str(name).replace("\xa0", " ").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.rstrip(".,;: ")


def _resolve_columns(raw_headers: list[str]) -> PartsColumnReport:
    report = PartsColumnReport()
    normalized_to_raw: dict[str, str] = {}
    for raw in raw_headers:
        normalized_to_raw.setdefault(_normalize_header(raw), raw)

    used_raw: set[str] = set()
    for key, (expected_header, critical) in CANONICAL_COLUMNS.items():
        norm_expected = _normalize_header(expected_header)
        if norm_expected in normalized_to_raw:
            raw = normalized_to_raw[norm_expected]
            report.matched[key] = raw
            used_raw.add(raw)
            continue
        candidates = [n for n, r in normalized_to_raw.items() if r not in used_raw]
        close = difflib.get_close_matches(norm_expected, candidates, n=1, cutoff=0.85)
        if close:
            raw = normalized_to_raw[close[0]]
            report.fuzzy_matched[key] = raw
            used_raw.add(raw)
            continue
        (report.missing_critical if critical else report.missing_optional).append(key)
    return report


def _format_missing_error(report: PartsColumnReport, raw_headers: list[str]) -> str:
    lines = [
        "Файл не распознан как выгрузка по запасным частям: не найдены обязательные столбцы.",
        "",
        "Отсутствуют критические столбцы:",
    ]
    for key in report.missing_critical:
        expected, _ = CANONICAL_COLUMNS[key]
        close = difflib.get_close_matches(_normalize_header(expected), [_normalize_header(h) for h in raw_headers], n=3, cutoff=0.4)
        hint = f" (похожие столбцы в файле: {', '.join(close)})" if close else ""
        lines.append(f"  - «{expected}»{hint}")
    lines.append("")
    lines.append("Проверьте, что загружен правильный файл выгрузки по запасным частям.")
    return "\n".join(lines)


def load_parts_data(path: str | Path) -> tuple[pd.DataFrame, PartsColumnReport]:
    path = Path(path)
    xl = pd.ExcelFile(path)
    sheet = xl.sheet_names[0]
    raw_df = pd.read_excel(path, sheet_name=sheet)
    raw_headers = [str(c) for c in raw_df.columns]

    report = _resolve_columns(raw_headers)
    if not report.ok:
        raise PartsColumnResolutionError(_format_missing_error(report, raw_headers))

    rename_map = {raw: key for key, raw in {**report.matched, **report.fuzzy_matched}.items()}
    df = raw_df.rename(columns=rename_map)

    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d.%m.%Y", errors="coerce")

    for col in NUMERIC_COMMA_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace("\xa0", "", regex=False).str.replace(",", ".", regex=False),
                errors="coerce",
            )

    return df, report

"""Reads the source WR_Consolidated_List_*.xlsx export into a normalized DataFrame.

Design constraints (see project spec):
  * Columns are matched by (normalized) NAME, never by position -- a future
    export may add/remove/reorder columns.
  * Matching is resilient to case, surrounding whitespace, and small
    cosmetic differences; when a critical column still cannot be found,
    fail loudly with a message that names the missing column and lists the
    closest candidates actually present, rather than silently proceeding
    with wrong data.
  * This module is the ONLY place that knows the raw Russian header text.
    Everything downstream (analytics/*) works with the stable, English,
    snake_case keys defined in CANONICAL_COLUMNS.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# key -> (expected raw header, is_critical)
# "critical" means: without this column, at least one report section cannot
# be computed at all and the file must be rejected up front.
CANONICAL_COLUMNS: dict[str, tuple[str, bool]] = {
    "row_no": ("№ п/п", True),
    "asc_code": ("Код АСЦ", False),
    "asc_name": ("Наименование АСЦ", True),
    "asc_city": ("Город АСЦ", False),
    "asc_region": ("Регион АСЦ", True),
    "repair_level": ("Уровень ремонта", True),
    "brand": ("Бренд", True),
    "act_date": ("Дата акта", True),
    "act_number": ("Номер акта", False),
    "asc_receipt_number": ("№ квит АСЦ", False),
    "counterparty_order_number": ("Номер заказа контрагента", False),
    "model": ("Модель", True),
    "equipment_category": ("Категория техники", True),
    "serial_number": ("Сер. №", False),
    "manufacturer": ("Изготовитель", True),
    "client_data": ("Данные клиента", False),
    "client_phone": ("Телефон клиента", False),
    "sale_date": ("Дата продажи", False),
    "receipt_date": ("Дата поступления", True),
    "repair_date": ("Дата ремонта", True),
    "defect_description": ("Описание дефекта", False),
    "work_performed": ("Выполненные работы", False),
    "iris_position_number": ("IRIS позиционный номер", False),
    "iris_condition_code": ("IRIS код условия", False),
    "iris_symptom_code": ("IRIS код симптома", False),
    "iris_section_code": ("IRIS код секции", False),
    "iris_defect_code": ("IRIS код дефекта", False),
    "iris_repair_code": ("IRIS код ремонта", False),
    "service_cost": ("Стоимость услуг, руб.коп.", True),
    "part_name": ("Наименование артикула", False),
    "part_article": ("Артикул", False),
    "part_line_seq": ("int", False),
    "part_cost_price": ("Себестоимость", False),
    "part_price": ("Стоимость", False),
    "part_cost_third_party": ("Стоимость детали 3-х лиц", False),
    "parts_compensation": ("Сумма компенсации АСЦ за запчасти", True),
    "visit_cost": ("Выезд, руб.коп", True),
    "total_amount": ("Сумма (итого),руб.коп.", True),
}

DATE_COLUMNS = ["act_date", "receipt_date", "repair_date"]
# sale_date is handled separately: in the reference export it comes through as
# free text "DD.MM.YYYY" rather than a native Excel date.


class ColumnResolutionError(RuntimeError):
    """Raised when one or more critical columns cannot be matched in the file."""


@dataclass
class ColumnReport:
    """Diagnostics about how source headers were matched to canonical keys."""

    matched: dict[str, str] = field(default_factory=dict)  # canonical key -> raw header used
    fuzzy_matched: dict[str, str] = field(default_factory=dict)  # canonical key -> raw header (not exact)
    missing_critical: list[str] = field(default_factory=list)  # canonical keys
    missing_optional: list[str] = field(default_factory=list)  # canonical keys
    unrecognized_raw_headers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_critical


def _normalize_header(name: object) -> str:
    if name is None:
        return ""
    text = str(name).replace("\xa0", " ")
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(".,;: ")
    return text


def _resolve_columns(raw_headers: list[str]) -> ColumnReport:
    report = ColumnReport()
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
        # fuzzy fallback against remaining (not yet claimed) headers
        candidates = [n for n, r in normalized_to_raw.items() if r not in used_raw]
        close = difflib.get_close_matches(norm_expected, candidates, n=1, cutoff=0.85)
        if close:
            raw = normalized_to_raw[close[0]]
            report.fuzzy_matched[key] = raw
            used_raw.add(raw)
            continue
        if critical:
            report.missing_critical.append(key)
        else:
            report.missing_optional.append(key)

    report.unrecognized_raw_headers = [r for r in raw_headers if r not in used_raw]
    return report


def _format_missing_error(report: ColumnReport, raw_headers: list[str]) -> str:
    lines = [
        "Файл не распознан как выгрузка WR_Consolidated_List: не найдены обязательные столбцы.",
        "",
        "Отсутствуют критические столбцы:",
    ]
    for key in report.missing_critical:
        expected, _ = CANONICAL_COLUMNS[key]
        close = difflib.get_close_matches(_normalize_header(expected), [_normalize_header(h) for h in raw_headers], n=3, cutoff=0.4)
        hint = f" (похожие столбцы в файле: {', '.join(close)})" if close else ""
        lines.append(f"  - «{expected}»{hint}")
    lines.append("")
    lines.append("Проверьте, что загружен правильный файл-выгрузка (лист 'Table', 38 столбцов).")
    return "\n".join(lines)


def find_data_sheet(path: str | Path) -> str:
    """Return the sheet name to read: 'Table' if present, else the first sheet."""
    xl = pd.ExcelFile(path)
    if "Table" in xl.sheet_names:
        return "Table"
    return xl.sheet_names[0]


def load_repair_data(path: str | Path) -> tuple[pd.DataFrame, ColumnReport]:
    """Load and normalize a WR_Consolidated_List_*.xlsx export.

    Returns (df, column_report). Raises ColumnResolutionError if a critical
    column is missing. `df` columns are the canonical snake_case keys from
    CANONICAL_COLUMNS (plus any unrecognized raw columns, left untouched,
    in case future analytics need them).
    """
    path = Path(path)
    sheet = find_data_sheet(path)
    raw_df = pd.read_excel(path, sheet_name=sheet, dtype={"Артикул": str})
    raw_headers = [str(c) for c in raw_df.columns]

    report = _resolve_columns(raw_headers)
    if not report.ok:
        raise ColumnResolutionError(_format_missing_error(report, raw_headers))

    rename_map = {raw: key for key, raw in {**report.matched, **report.fuzzy_matched}.items()}
    df = raw_df.rename(columns=rename_map)

    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    if "sale_date" in df.columns:
        # Reference export stores this as free text "DD.MM.YYYY"; be lenient
        # and also accept a native datetime if a future export changes this.
        parsed = pd.to_datetime(df["sale_date"], format="%d.%m.%Y", errors="coerce")
        still_missing = parsed.isna() & df["sale_date"].notna()
        if still_missing.any():
            parsed.loc[still_missing] = pd.to_datetime(df.loc[still_missing, "sale_date"], errors="coerce")
        df["sale_date"] = parsed

    # Row identity for section 12 / diagnostics: preserve original spreadsheet
    # row order regardless of any later filtering/sorting.
    df["_source_row_index"] = range(len(df))

    return df, report

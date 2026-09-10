"""Reads the tech-support ticket export (a SEPARATE, OPTIONAL third file) that
section 11 needs.

See docs/REVERSE_ENGINEERING.md §14 for the full investigation confirming
this file's formula against the reference report. Same robustness pattern
as ingest/excel_reader.py and ingest/parts_reader.py: match columns by
(normalized) NAME, fail loudly and specifically if a critical column is
missing.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

CANONICAL_COLUMNS: dict[str, tuple[str, bool]] = {
    "ticket_number": ("Номер", True),
    "brand": ("Бренд", False),
    "model": ("Модель", False),
    "repair_number": ("Номер ремонта", False),
    "status": ("Статус", True),
    "asc_code": ("Код АСЦ", False),
    "organization": ("Организация", True),
    "created_date": ("Дата создания", True),
    "answered_date": ("Дата ответа", False),
    "answered_by": ("Кто ответил", False),
    "closed_date": ("Дата закрытия", False),
    "topic": ("Тема", True),
    "rating": ("Оценка", False),
    "serial_number": ("Серийный номер", False),
    "chassis": ("Шасси", False),
    "country": ("Страна", False),
}

DATE_COLUMNS = ["created_date", "answered_date", "closed_date"]


class SupportColumnResolutionError(RuntimeError):
    pass


@dataclass
class SupportColumnReport:
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


def _resolve_columns(raw_headers: list[str]) -> SupportColumnReport:
    report = SupportColumnReport()
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


def _format_missing_error(report: SupportColumnReport, raw_headers: list[str]) -> str:
    lines = [
        "Файл не распознан как выгрузка по технической поддержке: не найдены обязательные столбцы.",
        "",
        "Отсутствуют критические столбцы:",
    ]
    for key in report.missing_critical:
        expected, _ = CANONICAL_COLUMNS[key]
        close = difflib.get_close_matches(_normalize_header(expected), [_normalize_header(h) for h in raw_headers], n=3, cutoff=0.4)
        hint = f" (похожие столбцы в файле: {', '.join(close)})" if close else ""
        lines.append(f"  - «{expected}»{hint}")
    lines.append("")
    lines.append("Проверьте, что загружен правильный файл выгрузки по технической поддержке.")
    return "\n".join(lines)


def load_support_data(path: str | Path) -> tuple[pd.DataFrame, SupportColumnReport]:
    path = Path(path)
    xl = pd.ExcelFile(path)
    sheet = xl.sheet_names[0]
    raw_df = pd.read_excel(path, sheet_name=sheet)
    raw_headers = [str(c) for c in raw_df.columns]

    report = _resolve_columns(raw_headers)
    if not report.ok:
        raise SupportColumnResolutionError(_format_missing_error(report, raw_headers))

    rename_map = {raw: key for key, raw in {**report.matched, **report.fuzzy_matched}.items()}
    df = raw_df.rename(columns=rename_map)

    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d.%m.%Y", errors="coerce")

    return df, report

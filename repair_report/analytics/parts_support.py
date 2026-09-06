"""Sections 10 (Аналитика поставок запасных частей) and 11 (Аналитика
Технической Поддержки) -- UNCONFIRMED. Spec §2.13.

=== Investigation log (do not repeat this work; read before touching this file) ===

Claimed reference numbers (from the manually-produced July DOCX):
  - Section 10: "Общее количество обработанных строк: 746", "Уникальных
    заказов: 709", "Заказано запчастей (шт): 756". A status breakdown table
    ('Статус линии заказа' x month) and a "География поставок" table
    (city/region -> shipped/total/% fulfilment) with fixed status colors.
  - Section 11: "Всего поступило заявок (тикетов): 287", "Доля закрытых
    заявок: 44.6%". Tables for top ASC by ticket volume, "support engineers",
    ticket topic breakdown, and a data-quality audit of ticket fields.

What was checked against the ONLY input this application is allowed to read
(the WR_Consolidated_List_*.xlsx export) and also against the client's own
intermediate pivot workbook (Final_Report_2026_*_Kosovov.xlsx, dev-only test
fixture, never read at runtime -- see repair_report/config and this module's
callers):

  1. Rows with a non-blank 'Наименование артикула' (the only column that
     signals "a spare part was used on this repair") total 330 across all
     4 months in the export (107 in July, 69 in June). Section 10 claims 746
     rows for "June+July combined" -- off by more than 2x. Not reconcilable
     by any row-counting convention we found (with/without continuation
     rows, per-part-line vs per-repair, etc).
  2. The order-status vocabulary named in section 10 ("Зарезервирована на
     складе" / "Отгружена" / "Отказана менеджером" / "Рассматривается") does
     not exist as a column, nor as a recognizable enum anywhere in the
     export's 38 columns.
  3. The 'int' column (32nd, header literally "int") was fully explained
     under a DIFFERENT investigation (see analytics/common.py and §1.3 of
     the product spec): it is a sequence number for spare-part continuation
     lines within one repair record (values 1, 2, or blank), NOT a
     support-ticket flag. That hypothesis is closed, do not reopen it.
  4. Section 11's ASC names in its own reference examples ("Сервисный центр
     VPS", "АЦ ПИОНЕР СЕРВИС ООО") use a different naming convention than
     the canonical 'Наименование АСЦ' values seen anywhere in the export
     (e.g. 'ООО "АЦ "ПИОНЕР СЕРВИС""') -- consistent with these two sections
     being sourced from an entirely different system.
  5. The client's own intermediate pivot workbook (Kosovov), which WAS used
     to build the rest of the manual report (sections 1-9, 12) and which
     contains full non-truncated pivots for every other section, has NO
     sheet related to parts or support at all -- its complete sheet list is
     Сводка, Регионы, АСЦ, Изготовители, Выезды_АСЦ, ТВ_диагонали,
     ТВ_Модели, ТВ_IRIS, IRIS_ошибки. If even the preparer's own working
     file doesn't carry this data, it did not come from this export.

Conclusion: sections 10 and 11 are built from a separate source system
(a parts-ordering system and a support ticket tracker) that was not
included in what this application is allowed to read. This is not a
formula we failed to find -- it's data that isn't there. Fabricating a
plausible-looking substitute would silently hand the client wrong numbers,
which is worse than clearly saying "unknown". See app_settings.json's
`experimental_sections` flag: both sections are OFF by default and, even
when enabled, render as an explicit "needs clarification" placeholder using
the column layout named in the spec (so a real data source can be wired in
later without a layout redo), never invented figures.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExperimentalSectionPlaceholder:
    title: str
    message: str
    column_layout: list[str]


SECTION_10_PLACEHOLDER = ExperimentalSectionPlaceholder(
    title="10. Аналитика поставок запасных частей",
    message=(
        "Данные для этого раздела не удалось однозначно определить в указанной выгрузке — "
        "требуется уточнение у заказчика источника/логики (вероятно, отдельная система заказа запчастей)."
    ),
    column_layout=["Статус линии заказа", "<месяц 1>", "<месяц 2>", "ИТОГО"],
)

SECTION_11_PLACEHOLDER = ExperimentalSectionPlaceholder(
    title="11. Аналитика Технической Поддержки",
    message=(
        "Данные для этого раздела не удалось однозначно определить в указанной выгрузке — "
        "требуется уточнение у заказчика источника/логики (вероятно, отдельная система тикетов техподдержки)."
    ),
    column_layout=["Город / Направление", "Всего заявок (строк)", "Отгружено", "Выполнение (%)"],
)

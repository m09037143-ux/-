"""Section 11 (tech support) vs. the reference DOCX's narrative text and
tables (26/27/28). See docs/REVERSE_ENGINEERING.md §14 for the full
investigation -- like section 10, these formulas were reverse-engineered
from a file supplied after the original report shipped, so every number
here was cross-checked directly against the reference DOCX rather than a
pre-computed pivot fixture.

11.2 "Инженеры поддержки" is intentionally NOT implemented: the source
file's 'Кто ответил' column is 100% empty, and the reference report's own
raw XML confirms it has no table under that heading either -- just the bare
subheading. Reproducing that absence faithfully is the correct behavior,
not a gap.

Expected discrepancy: 'Статус' is a LIVE field (same as section 10's
'Статус линии заказа') -- this file is a snapshot from ~09.09.2026, a month
after the reference DOCX (~10.08.2026), so the closed-ticket share has
legitimately moved on (44.6% -> ~97.9% as tickets got resolved). The ticket
COUNT (which doesn't depend on a mutable field) matches exactly.
"""
from pathlib import Path

import pytest

from repair_report.analytics.periods import Period
from repair_report.analytics.support import compute_summary, organizations_table, period_window_df, quality_audit_table, topics_table
from repair_report.ingest.support_reader import load_support_data

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT_FILE = FIXTURES / "Техподдержка_100926.xlsx"


@pytest.fixture(scope="session")
def support_window():
    df, _ = load_support_data(str(SUPPORT_FILE))
    window_df, window_periods = period_window_df(df, Period(2026, 7), Period(2026, 6))
    return window_df, window_periods


def test_ingest_resolves_all_critical_columns():
    df, report = load_support_data(str(SUPPORT_FILE))
    assert report.ok
    assert report.missing_critical == []
    assert len(df) == 672


def test_ticket_count_matches_reference_exactly(support_window):
    window_df, _ = support_window
    s = compute_summary(window_df)
    assert s.ticket_count == 287  # "Всего поступило заявок (тикетов): 287"
    assert s.leader_org == "Сервисный центр VPS"
    assert s.leader_count == 30


def test_leader_follower_text_matches_reference(support_window):
    window_df, _ = support_window
    s = compute_summary(window_df)
    text = s.leader_follower_text()
    assert text.startswith("Абсолютным лидером является Сервисный центр VPS (30 шт.).")
    # Laggard count matches (1); the specific org name among many count=1
    # ties is a documented tie-break ambiguity (reference: "СЦ Клён").
    assert s.laggard_count == 1


def test_organizations_table_matches_reference_top15(support_window):
    window_df, _ = support_window
    rows = organizations_table(window_df)
    got_counts = [r.count for r in rows]
    assert got_counts == [30, 14, 14, 13, 13, 9, 8, 8, 8, 7, 7, 7, 7, 6, 6]
    assert rows[0].organization == "Сервисный центр VPS"


def test_topics_table_matches_reference(support_window):
    window_df, _ = support_window
    rows = topics_table(window_df)
    got = {r.topic: r.count for r in rows}
    expected = {
        "Вопрос по ремонту": 63,
        "Проблемы с заказом з/ч": 40,
        "Нет модели в заказе зап.частей": 22,
        "Требуется парномер": 19,
        "Гарантийность обращения": 18,
        "Неисправна ЖК-панель": 18,
        "Добавление з/ч": 18,
        "Обновление ПО": 17,
        "Запрос на АНРП": 17,
        "согласование запчасти": 11,
        "Прошивка": 9,
        "Закрыть ремонт": 9,
        "Запрос по Акт ТО": 8,
        "техническая поддержка": 8,
    }
    for topic, count in expected.items():
        assert got[topic] == count, topic


def test_quality_audit_matches_reference_exactly(support_window):
    window_df, _ = support_window
    rows = {r.field_label: r for r in quality_audit_table(window_df)}
    assert rows["Серийный номер"].filled == 287
    assert rows["Серийный номер"].missing == 0
    assert rows["Модель"].filled == 287
    assert rows["Бренд"].filled == 287
    assert rows["Шасси"].filled == 8
    assert rows["Шасси"].missing == 279
    assert round(rows["Шасси"].fill_pct) == 3
    assert rows["Тема обращения"].filled == 287


def test_period_window_handles_no_overlap_gracefully():
    df, _ = load_support_data(str(SUPPORT_FILE))
    window_df, window_periods = period_window_df(df, Period(2024, 1), Period(2023, 12))
    assert len(window_df) == 0
    s = compute_summary(window_df)
    assert s.ticket_count == 0
    assert s.leader_org is None
    assert "Недостаточно данных" in s.leader_follower_text()

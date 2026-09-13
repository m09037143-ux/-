"""Top-level orchestrator: loads a file, resolves periods, and runs every
analytics section for the chosen current/previous period pair.

This is the single entry point render/*.py and ui/*.py should call -- they
should never import individual analytics/*.py modules directly, so that the
column-resolution, period-selection and business-rule logic stays in one
place. See docs/REVERSE_ENGINEERING.md for the evidence behind every number
this module's output ultimately carries.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from repair_report.analytics import (
    categories,
    detail,
    fraud,
    iris_defects,
    manufacturers,
    parts,
    registries,
    regions_asc,
    sla_quality,
    summary,
    support,
    tv_analysis,
)
from repair_report.analytics.periods import Period, PeriodSpan, assign_periods, available_periods, select_span
from repair_report.analytics.regions_asc import LeaderFollowerText, leader_follower_from_dynamics, leader_follower_from_visits
from repair_report.analytics.tables import DynamicsTable
from repair_report.ingest.excel_reader import ColumnReport, load_repair_data
from repair_report.ingest.parts_reader import PartsColumnReport, load_parts_data
from repair_report.ingest.support_reader import SupportColumnReport, load_support_data


@dataclass
class PartsSection:
    """Section 10, built from the optional spare-parts file. See
    analytics/parts.py and docs/REVERSE_ENGINEERING.md §13."""

    column_report: PartsColumnReport
    window_periods: list[Period]
    summary: parts.PartsSummary
    status_by_month: parts.StatusMonthTable
    geography: list[parts.GeographyRow]
    has_data_for_window: bool


@dataclass
class SupportSection:
    """Section 11, built from the optional tech-support ticket file. See
    analytics/support.py and docs/REVERSE_ENGINEERING.md §14."""

    column_report: SupportColumnReport
    window_periods: list[Period]
    summary: support.SupportSummary
    organizations: list[support.OrgRow]
    topics: list[support.TopicRow]
    quality_audit: list[support.QualityAuditRow]
    has_data_for_window: bool


@dataclass
class ReportData:
    current_span: PeriodSpan
    previous_span: PeriodSpan | None
    previous_available: bool
    previous_note: str
    current_span_complete: bool
    all_periods: list[Period]

    column_report: ColumnReport

    asc_registry: list[registries.AscRegistryRow]
    equipment_registry: list[registries.EquipmentRegistryRow]

    kpis: summary.SummaryKpis
    kpis_prev: summary.SummaryKpis | None

    equipment_by_sum: DynamicsTable
    equipment_by_count: DynamicsTable
    repair_level: DynamicsTable

    regions: DynamicsTable
    asc: DynamicsTable
    brands: DynamicsTable
    asc_visits: regions_asc.VisitsTable

    asc_leader_follower: LeaderFollowerText | None
    asc_visits_leader_follower: LeaderFollowerText | None
    brands_leader_follower: LeaderFollowerText | None

    golden_standard: list[sla_quality.GoldenStandardRow]
    risk_zone: sla_quality.RiskZoneResult
    duration: sla_quality.DurationAnalysis
    doa_rows: list[sla_quality.DoaRow]
    doa_total: int

    manufacturers: manufacturers.ManufacturersTable
    manufacturers_leader_follower: LeaderFollowerText | None

    tv_diagonal: DynamicsTable
    tv_brand_models: list[tv_analysis.BrandModels]
    tv_model_leader_follower: tv_analysis.ModelLeaderFollower | None

    iris_chains: list[iris_defects.IrisChainRow]
    iris_chains_total: int
    defect_texts: list[iris_defects.DefectTextRow]
    iris_errors: list[iris_defects.IrisErrorRow]

    fraud_rows: list[fraud.FraudRow]

    detail_rows: list[dict] = field(repr=False, default_factory=list)

    parts: PartsSection | None = None
    """Section 10's real data, present only when a parts file was supplied
    to build_report(). None means section 10 is simply omitted from the
    report -- no checkbox, no placeholder text (see
    docs/REVERSE_ENGINEERING.md §13)."""

    support: SupportSection | None = None
    """Section 11's real data, present only when a tech-support ticket file
    was supplied to build_report(). None means section 11 is simply omitted
    from the report, same pattern as `parts` above (see
    docs/REVERSE_ENGINEERING.md §14)."""

    monthly_dynamics: list[summary.MonthlyKpiRow] | None = None
    """'2.1 Динамика по месяцам' -- populated only when current_span covers
    more than one calendar month (quarter/year) AND at least 2 of its
    months actually have data; otherwise None and the subsection is
    omitted (see docs/REVERSE_ENGINEERING.md §15)."""

    ai_summary: str | None = None
    """Optional ИИ-generated executive summary (see repair_report/ai/), set
    by the UI layer AFTER build_report() returns -- engine.py itself never
    makes network calls, staying a plain offline-testable library. None
    means no summary was requested, or generation failed (in which case a
    human-readable failure note is placed here instead by the caller, never
    silently dropped)."""


def build_report(
    path: str,
    requested_period_key: str | None = None,
    parts_path: str | None = None,
    support_path: str | None = None,
) -> ReportData:
    df, column_report = load_repair_data(path)
    periods_series = assign_periods(df)
    all_periods = available_periods(periods_series)

    selection = select_span(all_periods, requested_period_key)

    current_df = df[periods_series.isin(selection.current_present_months)]
    previous_df = df[periods_series.isin(selection.previous.months())] if selection.previous_available else None

    monthly_dynamics = None
    if selection.current.kind != "month" and len(selection.current_present_months) >= 2:
        monthly_dynamics = summary.monthly_kpi_table(df, selection.current_present_months, periods_series)

    kpis = summary.compute_summary(current_df)
    kpis_prev = summary.compute_summary(previous_df) if previous_df is not None else None

    eq_sum = categories.equipment_by_sum(current_df, previous_df)
    eq_count = categories.equipment_by_count(current_df, previous_df)
    level = categories.repair_level_table(current_df, previous_df)

    regions = regions_asc.regions_table(current_df, previous_df)
    asc = regions_asc.asc_table(current_df, previous_df)
    brands = regions_asc.brands_table(current_df, previous_df)
    visits = regions_asc.asc_visits_table(current_df)

    manuf = manufacturers.manufacturers_table(current_df, previous_df)

    diag = tv_analysis.diagonal_table(current_df, previous_df)
    brand_models = tv_analysis.top_brand_models(current_df)

    chains, chains_total = iris_defects.iris_chain_table(current_df)
    defect_texts = iris_defects.defect_text_table(current_df)
    iris_errors = iris_defects.iris_errors_table(current_df)

    # Sections 10/11's "window" of months: for a single-month report this is
    # the original current+calendar-previous-month pair (unchanged, exactly
    # as reverse-engineered against the reference report -- see §13/§14).
    # For a quarter/year report it's every present month of that SAME span
    # the rest of the report aggregates over, per the "aggregate over the
    # whole period" product decision (docs/REVERSE_ENGINEERING.md §15) --
    # not a separately re-derived 2-month rule.
    if selection.current.kind == "month":
        window_months = selection.current_present_months + (
            selection.previous.months() if selection.previous_available else []
        )
        window_months = sorted(set(window_months), key=lambda p: (p.year, p.month))
    else:
        window_months = selection.current_present_months

    parts_section = None
    if parts_path:
        parts_df, parts_column_report = load_parts_data(parts_path)
        window_df = parts.window_df_for_months(parts_df, window_months)
        parts_section = PartsSection(
            column_report=parts_column_report,
            window_periods=window_months,
            summary=parts.compute_summary(window_df),
            status_by_month=parts.status_by_month_table(window_df, window_months),
            geography=parts.geography_table(window_df),
            has_data_for_window=len(window_df) > 0,
        )

    support_section = None
    if support_path:
        support_df, support_column_report = load_support_data(support_path)
        sup_window_df = support.window_df_for_months(support_df, window_months)
        sup_window_periods = window_months
        support_section = SupportSection(
            column_report=support_column_report,
            window_periods=sup_window_periods,
            summary=support.compute_summary(sup_window_df),
            organizations=support.organizations_table(sup_window_df),
            topics=support.topics_table(sup_window_df),
            quality_audit=support.quality_audit_table(sup_window_df),
            has_data_for_window=len(sup_window_df) > 0,
        )

    return ReportData(
        current_span=selection.current,
        previous_span=selection.previous if selection.previous_available else None,
        previous_available=selection.previous_available,
        previous_note=selection.note,
        current_span_complete=selection.current_complete,
        all_periods=all_periods,
        monthly_dynamics=monthly_dynamics,
        column_report=column_report,
        asc_registry=registries.asc_registry(current_df),
        equipment_registry=registries.equipment_registry(current_df),
        kpis=kpis,
        kpis_prev=kpis_prev,
        equipment_by_sum=eq_sum,
        equipment_by_count=eq_count,
        repair_level=level,
        regions=regions,
        asc=asc,
        brands=brands,
        asc_visits=visits,
        asc_leader_follower=leader_follower_from_dynamics(asc),
        asc_visits_leader_follower=leader_follower_from_visits(visits),
        brands_leader_follower=leader_follower_from_dynamics(brands),
        golden_standard=sla_quality.golden_standard(current_df),
        risk_zone=sla_quality.risk_zone(current_df),
        duration=sla_quality.duration_analysis(current_df),
        doa_rows=(doa := sla_quality.doa_table(current_df))[0],
        doa_total=doa[1],
        manufacturers=manuf,
        manufacturers_leader_follower=leader_follower_from_dynamics(manuf, rank_by="sum_cur"),
        tv_diagonal=diag,
        tv_brand_models=brand_models,
        tv_model_leader_follower=tv_analysis.model_leader_follower(current_df),
        iris_chains=chains,
        iris_chains_total=chains_total,
        defect_texts=defect_texts,
        iris_errors=iris_errors,
        fraud_rows=fraud.fraud_table(current_df),
        detail_rows=detail.detail_rows(current_df),
        parts=parts_section,
        support=support_section,
    )

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

import pandas as pd

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
    tv_analysis,
)
from repair_report.analytics.periods import Period, PeriodSelection, assign_periods, available_periods, select_periods
from repair_report.analytics.regions_asc import LeaderFollowerText, leader_follower_from_dynamics, leader_follower_from_visits
from repair_report.analytics.tables import DynamicsTable
from repair_report.config import loader
from repair_report.ingest.excel_reader import ColumnReport, load_repair_data
from repair_report.ingest.parts_reader import PartsColumnReport, load_parts_data


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
class ReportData:
    current_period: Period
    previous_period: Period | None
    previous_available: bool
    previous_note: str
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

    experimental_sections_enabled: bool = False
    """Gates section 11 (tech support) ALWAYS, and section 10's placeholder
    when no parts file was provided. Has no effect on section 10 once a
    parts file IS provided -- that section then always shows its real data,
    per product decision (see docs/REVERSE_ENGINEERING.md §13)."""

    parts: PartsSection | None = None
    """Section 10's real data, present only when a parts file was supplied
    to build_report(). None means section 10 falls back to the old
    'requires clarification' placeholder (still gated by
    experimental_sections_enabled, unchanged from before)."""


def build_report(
    path: str,
    requested_period_key: str | None = None,
    include_experimental: bool | None = None,
    parts_path: str | None = None,
) -> ReportData:
    df, column_report = load_repair_data(path)
    periods_series = assign_periods(df)
    all_periods = available_periods(periods_series)

    requested_period = None
    if requested_period_key:
        year, month = (int(x) for x in requested_period_key.split("-"))
        requested_period = Period(year, month)

    selection: PeriodSelection = select_periods(all_periods, requested_period)

    current_df = df[periods_series == selection.current]
    previous_df = df[periods_series == selection.previous] if selection.previous_available else None

    settings = loader.app_settings()
    experimental_enabled = (
        include_experimental if include_experimental is not None else settings["experimental_sections"]["enabled"]
    )

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

    parts_section = None
    if parts_path:
        parts_df, parts_column_report = load_parts_data(parts_path)
        window_df, window_periods = parts.period_window_df(parts_df, selection.current, selection.previous)
        parts_section = PartsSection(
            column_report=parts_column_report,
            window_periods=window_periods,
            summary=parts.compute_summary(window_df),
            status_by_month=parts.status_by_month_table(window_df, window_periods),
            geography=parts.geography_table(window_df),
            has_data_for_window=len(window_df) > 0,
        )

    return ReportData(
        current_period=selection.current,
        previous_period=selection.previous,
        previous_available=selection.previous_available,
        previous_note=selection.note,
        all_periods=all_periods,
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
        experimental_sections_enabled=experimental_enabled,
        parts=parts_section,
    )

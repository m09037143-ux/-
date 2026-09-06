"""PDF export -- renders the SAME HTML (render/html_builder.build_html) via
weasyprint, so PDF and HTML never drift out of visual sync (spec §4 tech
choice: one HTML+CSS source of truth, no LibreOffice/Word dependency)."""
from __future__ import annotations

from pathlib import Path

from weasyprint import HTML

from repair_report.analytics.engine import ReportData
from repair_report.profile import ClientProfile
from repair_report.render.html_builder import build_html


def save_pdf(
    report: ReportData,
    profile: ClientProfile,
    out_path: str | Path,
    work_dir: str | Path,
    chart_paths: dict[str, str] | None = None,
) -> None:
    html = build_html(report, profile, work_dir, chart_paths)
    HTML(string=html, base_url=str(work_dir)).write_pdf(str(out_path))

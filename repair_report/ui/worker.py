"""Background worker: keeps the GUI responsive while the file is parsed,
aggregated, charted, and rendered (spec §5: staged progress indicator)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from repair_report.analytics.engine import ReportData, build_report
from repair_report.ingest.excel_reader import ColumnResolutionError
from repair_report.profile import ClientProfile


class ProbeWorker(QObject):
    """Step 1: just parse the file far enough to list available periods, so
    the UI can populate the period picker before the user commits to a
    format/output choice."""

    finished = Signal(object)  # ReportData built for the default (latest) period
    failed = Signal(str)
    stage = Signal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path

    def run(self):
        try:
            self.stage.emit("Чтение файла…")
            self.stage.emit("Определение периодов…")
            report = build_report(self.file_path)
            self.stage.emit("Готово")
            self.finished.emit(report)
        except ColumnResolutionError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 -- surface any failure to the user
            self.failed.emit(f"Не удалось обработать файл: {e}")


class ReportWorker(QObject):
    """Step 2: full aggregation for the chosen period + rendering to the
    chosen output formats."""

    progress = Signal(str)
    finished = Signal(list)  # list of output file paths written
    failed = Signal(str)

    def __init__(
        self,
        file_path: str,
        period_key: str | None,
        formats: list[str],
        out_paths: dict[str, str],
        profile: ClientProfile,
        include_experimental: bool,
        work_dir: str,
    ):
        super().__init__()
        self.file_path = file_path
        self.period_key = period_key
        self.formats = formats
        self.out_paths = out_paths
        self.profile = profile
        self.include_experimental = include_experimental
        self.work_dir = work_dir

    def run(self):
        try:
            self.progress.emit("Чтение файла…")
            self.progress.emit("Определение периодов…")
            report: ReportData = build_report(self.file_path, self.period_key, self.include_experimental)

            self.progress.emit("Агрегация показателей…")
            # (aggregation already happened inside build_report; this stage
            # message is shown for UX continuity with the spec's staged flow)

            self.progress.emit("Построение графиков…")
            from repair_report.render.chart_pipeline import generate_charts

            # Generated ONCE and shared across all selected formats -- each
            # of HTML/PDF/DOCX used to call generate_charts independently,
            # tripling matplotlib work (and the wall-clock time) for no
            # benefit when a user picks all three formats at once.
            chart_paths = generate_charts(report, Path(self.work_dir) / "charts")
            written = []

            if "html" in self.formats:
                self.progress.emit("Генерация отчёта (HTML)…")
                from repair_report.render.html_builder import save_html

                save_html(report, self.profile, self.out_paths["html"], self.work_dir, chart_paths)
                written.append(self.out_paths["html"])

            if "pdf" in self.formats:
                self.progress.emit("Генерация отчёта (PDF)…")
                from repair_report.render.pdf_builder import save_pdf

                save_pdf(report, self.profile, self.out_paths["pdf"], self.work_dir, chart_paths)
                written.append(self.out_paths["pdf"])

            if "docx" in self.formats:
                self.progress.emit("Генерация отчёта (DOCX)…")
                from repair_report.render.docx_builder import save_docx

                save_docx(report, self.profile, self.out_paths["docx"], self.work_dir, chart_paths)
                written.append(self.out_paths["docx"])

            self.progress.emit("Готово")
            self.finished.emit(written)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"Ошибка при формировании отчёта: {e}")

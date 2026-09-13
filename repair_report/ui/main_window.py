"""Main application window (spec §5): file selection/drop -> period picker
-> profile + format selection -> progress -> save-as.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from repair_report import ai_settings as ai_settings_store
from repair_report import profile as profile_store
from repair_report.analytics.periods import MONTH_NAMES_RU
from repair_report.ui.ai_settings_dialog import AiSettingsDialog
from repair_report.ui.profile_dialog import ProfileDialog
from repair_report.ui.worker import ProbeWorker, ReportWorker

logger = logging.getLogger(__name__)


class DropArea(QLabel):
    def __init__(self, on_file_dropped, placeholder_text: str = "Перетащите файл .xlsx сюда\nили нажмите «Выбрать файл»"):
        super().__init__(placeholder_text)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(90)
        self.setStyleSheet("border: 2px dashed #888; border-radius: 8px; padding: 12px; color: #555;")
        self.setAcceptDrops(True)
        self._on_file_dropped = on_file_dropped

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith((".xlsx", ".xlsm")):
                self._on_file_dropped(path)
            else:
                QMessageBox.warning(self, "Неверный файл", "Ожидается файл выгрузки в формате .xlsx.")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Автоматический отчёт о выполненных ремонтах")
        self.setMinimumSize(960, 680)

        self.file_path: str | None = None
        self.parts_file_path: str | None = None
        self.support_file_path: str | None = None
        self.report = None  # ReportData for the currently probed file/period
        self.work_dir = tempfile.mkdtemp(prefix="repair_report_")
        self._probe_thread: QThread | None = None
        self._report_thread: QThread | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- File selection: three PARALLEL, equal-weight inputs that
        # together feed one report -- not a "main file + optional extras"
        # hierarchy. Loading all three means the full report (sections 10
        # and 11 included); loading fewer just narrows which sections have
        # real data, no checkboxes involved (see
        # docs/REVERSE_ENGINEERING.md §13/§14's product-decision notes). ---
        files_group = QGroupBox("1. Файлы для отчёта")
        # A grid (not three independent QVBoxLayouts) so every column's row
        # -- title, drop area, button, status -- is forced to the same
        # height as its siblings. With independent per-column layouts, a
        # title that wraps to a different number of lines than its
        # neighbours pushes that column's drop area down/up relative to the
        # others (reported as visible misalignment); a shared grid can't
        # drift like that since each row's height is the max across the row.
        files_grid = QGridLayout(files_group)
        for col_idx in range(3):
            files_grid.setColumnStretch(col_idx, 1)

        self.drop_area, self.file_status_label = self._build_file_column(
            files_grid,
            column=0,
            title="Выгрузка ремонтов (WR_Consolidated_List_*.xlsx)",
            placeholder="Перетащите файл выгрузки ремонтов сюда\nили нажмите «Выбрать файл»",
            on_drop=self._load_file,
            on_pick=self._pick_file,
            initial_status="Файл не выбран.",
        )

        self.parts_drop_area, self.parts_file_status_label = self._build_file_column(
            files_grid,
            column=1,
            title="Выгрузка по запасным частям (раздел 10)",
            placeholder="Перетащите файл по запасным частям сюда\nили нажмите «Выбрать файл»",
            on_drop=self._load_parts_file,
            on_pick=self._pick_parts_file,
            initial_status="Файл не выбран — раздел 10 будет скрыт, пока не загружен.",
        )

        self.support_drop_area, self.support_file_status_label = self._build_file_column(
            files_grid,
            column=2,
            title="Выгрузка по технической поддержке (раздел 11)",
            placeholder="Перетащите файл по техподдержке сюда\nили нажмите «Выбрать файл»",
            on_drop=self._load_support_file,
            on_pick=self._pick_support_file,
            initial_status="Файл не выбран — раздел 11 будет скрыт, пока не загружен.",
        )

        layout.addWidget(files_group)

        # --- Period selection ---
        period_group = QGroupBox("2. Отчётный период")
        period_layout = QVBoxLayout(period_group)
        period_row = QHBoxLayout()
        self.period_kind_combo = QComboBox()
        self.period_kind_combo.addItem("Месяц", userData="month")
        self.period_kind_combo.addItem("Квартал", userData="quarter")
        self.period_kind_combo.addItem("Год", userData="year")
        self.period_kind_combo.setEnabled(False)
        self.period_kind_combo.currentIndexChanged.connect(self._on_period_kind_changed)
        period_row.addWidget(self.period_kind_combo)
        self.period_combo = QComboBox()
        self.period_combo.setEnabled(False)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        period_row.addWidget(self.period_combo, stretch=1)
        period_layout.addLayout(period_row)
        self.previous_period_label = QLabel("")
        self.previous_period_label.setWordWrap(True)
        period_layout.addWidget(self.previous_period_label)
        layout.addWidget(period_group)

        # --- Client profile ---
        profile_group = QGroupBox("3. Профиль заказчика")
        profile_layout = QHBoxLayout(profile_group)
        self.profile_combo = QComboBox()
        self._reload_profiles()
        profile_layout.addWidget(self.profile_combo, stretch=1)
        new_profile_btn = QPushButton("Новый / Редактировать…")
        new_profile_btn.clicked.connect(self._edit_profile)
        profile_layout.addWidget(new_profile_btn)
        layout.addWidget(profile_group)

        # --- Format selection ---
        format_group = QGroupBox("4. Формат выгрузки")
        format_layout = QHBoxLayout(format_group)
        self.format_html = QCheckBox("HTML")
        self.format_pdf = QCheckBox("PDF")
        self.format_docx = QCheckBox("DOCX")
        self.format_pdf.setChecked(True)
        for cb in (self.format_html, self.format_pdf, self.format_docx):
            format_layout.addWidget(cb)
        layout.addWidget(format_group)

        # --- AI summary (optional) ---
        ai_group = QGroupBox("5. ИИ-анализ (опционально)")
        ai_layout = QHBoxLayout(ai_group)
        self.ai_summary_checkbox = QCheckBox("Провести анализ с помощью ИИ (краткое резюме)")
        self._ai_settings = ai_settings_store.load_ai_settings()
        self.ai_summary_checkbox.setChecked(self._ai_settings.enabled_by_default)
        ai_layout.addWidget(self.ai_summary_checkbox, stretch=1)
        ai_settings_btn = QPushButton("Настроить ИИ…")
        ai_settings_btn.clicked.connect(self._edit_ai_settings)
        ai_layout.addWidget(ai_settings_btn)
        layout.addWidget(ai_group)

        # --- Generate ---
        self.generate_btn = QPushButton("Сформировать отчёт")
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self._generate_report)
        layout.addWidget(self.generate_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)

        layout.addStretch()

    def _build_file_column(self, grid: QGridLayout, *, column: int, title: str, placeholder: str, on_drop, on_pick, initial_status: str):
        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(title_label, 0, column)

        drop_area = DropArea(on_drop, placeholder)
        grid.addWidget(drop_area, 1, column, Qt.AlignTop)

        pick_btn = QPushButton("Выбрать файл…")
        pick_btn.clicked.connect(on_pick)
        grid.addWidget(pick_btn, 2, column)

        status_label = QLabel(initial_status)
        status_label.setWordWrap(True)
        status_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(status_label, 3, column)

        return drop_area, status_label

    # ---- profile management ----
    def _reload_profiles(self):
        self.profile_combo.clear()
        names = profile_store.list_profiles()
        self.profile_combo.addItems(names)

    def _edit_profile(self):
        current_name = self.profile_combo.currentText()
        existing = profile_store.load_profile(current_name) if current_name else None
        dlg = ProfileDialog(self, existing)
        if dlg.exec():
            new_profile = dlg.result_profile()
            if new_profile:
                profile_store.save_profile(new_profile)
                self._reload_profiles()
                idx = self.profile_combo.findText(new_profile.name)
                if idx >= 0:
                    self.profile_combo.setCurrentIndex(idx)

    def _current_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            from repair_report.profile import ClientProfile

            return ClientProfile(name="(без профиля)")
        return profile_store.load_profile(name)

    # ---- AI settings ----
    def _edit_ai_settings(self):
        dlg = AiSettingsDialog(self, self._ai_settings)
        if dlg.exec():
            new_settings = dlg.result_settings()
            if new_settings:
                ai_settings_store.save_ai_settings(new_settings)
                self._ai_settings = new_settings
                self.ai_summary_checkbox.setChecked(new_settings.enabled_by_default)

    # ---- file loading ----
    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл выгрузки", "", "Excel (*.xlsx *.xlsm)")
        if path:
            self._load_file(path)

    def _pick_parts_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл по запасным частям", "", "Excel (*.xlsx *.xlsm)")
        if path:
            self._load_parts_file(path)

    def _pick_support_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл по технической поддержке", "", "Excel (*.xlsx *.xlsm)")
        if path:
            self._load_support_file(path)

    def _load_parts_file(self, path: str):
        self.parts_file_path = path
        self.parts_file_status_label.setText(f"Загружен: {os.path.basename(path)}")
        if self.file_path:
            self._load_file(self.file_path)  # re-probe so section 10 data is included

    def _load_support_file(self, path: str):
        self.support_file_path = path
        self.support_file_status_label.setText(f"Загружен: {os.path.basename(path)}")
        if self.file_path:
            self._load_file(self.file_path)  # re-probe so section 11 data is included

    def _load_file(self, path: str):
        self.file_path = path
        self.file_status_label.setText(f"Загрузка: {os.path.basename(path)}…")
        self.generate_btn.setEnabled(False)
        self.period_combo.setEnabled(False)

        # Parented to `self` so Qt's parent/child ownership keeps the C++
        # QThread object alive even after `self._probe_thread` is reassigned
        # by the next drop (loading main+parts+support in quick succession
        # re-probes repeatedly) -- without a parent, losing the last Python
        # reference to a QThread that hasn't fully stopped yet aborts the
        # process ("QThread: Destroyed while thread '' is still running").
        probe_thread = QThread(self)
        probe_worker = ProbeWorker(path, self.parts_file_path, self.support_file_path)
        probe_worker.moveToThread(probe_thread)
        probe_thread.started.connect(probe_worker.run)
        probe_worker.finished.connect(self._on_probe_finished)
        probe_worker.failed.connect(self._on_probe_failed)
        probe_worker.finished.connect(probe_thread.quit)
        probe_worker.failed.connect(probe_thread.quit)
        probe_thread.finished.connect(probe_thread.deleteLater)
        # Keep references so they aren't GC'd before Qt is done with them.
        self._probe_thread = probe_thread
        self._probe_worker = probe_worker
        probe_thread.start()

    def _on_probe_finished(self, report):
        self.report = report
        self.file_status_label.setText(
            f"Файл загружен: {os.path.basename(self.file_path)} — найдено периодов: {len(report.all_periods)}."
        )
        # Default view: month granularity, latest month selected -- matches
        # the report ProbeWorker already built (build_report() with no
        # requested key defaults to the latest month too), so no recompute
        # is needed here, unlike _on_period_kind_changed/_on_period_changed.
        self.period_kind_combo.blockSignals(True)
        self.period_kind_combo.setCurrentIndex(0)  # "Месяц"
        self.period_kind_combo.blockSignals(False)
        self.period_kind_combo.setEnabled(True)
        self._populate_period_combo("month")
        self.period_combo.setEnabled(True)
        self._update_previous_period_label(report)
        self._update_parts_status_label(report)
        self._update_support_status_label(report)
        self.generate_btn.setEnabled(True)

    def _populate_period_combo(self, kind: str) -> None:
        """Fill self.period_combo with every span of `kind` present in
        self.report.all_periods (see analytics.periods.available_spans),
        selecting the most recent one by default. Signals are blocked while
        repopulating -- the caller is responsible for recomputing the report
        for whatever ends up selected (see _on_period_kind_changed); the one
        exception is _on_probe_finished, whose report already matches the
        default month population, so it doesn't need to."""
        from repair_report.analytics.periods import available_spans

        spans = available_spans(self.report.all_periods, kind)
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        for span in spans:
            self.period_combo.addItem(str(span), userData=span.key)
        if spans:
            self.period_combo.setCurrentIndex(len(spans) - 1)  # default: most recent
        self.period_combo.blockSignals(False)

    def _on_period_kind_changed(self, _index: int) -> None:
        if self.report is None:
            return
        kind = self.period_kind_combo.currentData()
        self._populate_period_combo(kind)
        self._recompute_for_current_period()

    def _on_probe_failed(self, message: str):
        self.file_status_label.setText("Не удалось прочитать файл.")
        QMessageBox.critical(self, "Ошибка чтения файла", message)

    def _update_parts_status_label(self, report):
        if report.parts is None:
            return  # no parts file selected -- leave the static label as-is
        if report.parts.has_data_for_window:
            s = report.parts.summary
            self.parts_file_status_label.setText(
                f"Загружен: {os.path.basename(self.parts_file_path)} — за выбранный период найдено "
                f"{s.row_count} строк / {s.unique_orders} заказов."
            )
        else:
            window = " и ".join(str(p) for p in report.parts.window_periods)
            self.parts_file_status_label.setText(
                f"Загружен: {os.path.basename(self.parts_file_path)} — ⚠ нет данных за {window}."
            )

    def _update_support_status_label(self, report):
        if report.support is None:
            return  # no support file selected -- leave the static label as-is
        if report.support.has_data_for_window:
            s = report.support.summary
            self.support_file_status_label.setText(
                f"Загружен: {os.path.basename(self.support_file_path)} — за выбранный период найдено "
                f"{s.ticket_count} обращений."
            )
        else:
            window = " и ".join(str(p) for p in report.support.window_periods)
            self.support_file_status_label.setText(
                f"Загружен: {os.path.basename(self.support_file_path)} — ⚠ нет данных за {window}."
            )

    def _update_previous_period_label(self, report):
        if report.previous_available:
            self.previous_period_label.setText(f"✓ {report.previous_note}")
            self.previous_period_label.setStyleSheet("color: #2e7d32;")
        else:
            self.previous_period_label.setText(f"⚠ {report.previous_note}")
            self.previous_period_label.setStyleSheet("color: #b8860b;")

    def _on_period_changed(self, _index: int):
        self._recompute_for_current_period()

    def _recompute_for_current_period(self):
        # Rebuild the report for whatever span is now selected (month/
        # quarter/year), so the "previous period" note and every table
        # reflect it. Runs synchronously on the UI thread, same as before
        # this method was extracted -- period switches are much cheaper
        # than the full chart+render pipeline in _generate_report.
        if not self.file_path:
            return
        period_key = self.period_combo.currentData()
        if period_key is None:
            return
        from repair_report.analytics.engine import build_report

        try:
            self.report = build_report(self.file_path, period_key, self.parts_file_path, self.support_file_path)
            self._update_previous_period_label(self.report)
            self._update_parts_status_label(self.report)
            self._update_support_status_label(self.report)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", f"Не удалось пересчитать период: {e}")

    # ---- report generation ----
    def _generate_report(self):
        formats = []
        if self.format_html.isChecked():
            formats.append("html")
        if self.format_pdf.isChecked():
            formats.append("pdf")
        if self.format_docx.isChecked():
            formats.append("docx")
        if not formats:
            QMessageBox.warning(self, "Формат не выбран", "Выберите хотя бы один формат выгрузки (PDF/DOCX/HTML).")
            return

        ai_enabled = self.ai_summary_checkbox.isChecked()
        if ai_enabled and not self._ai_settings.is_configured():
            QMessageBox.warning(
                self,
                "ИИ-анализ не настроен",
                "Чтобы провести анализ с помощью ИИ, сначала заполните API-ключ и Folder ID "
                "в «Настроить ИИ…», либо снимите галочку.",
            )
            return

        span = self.report.current_span
        if span.kind == "month":
            default_name = f"Final_Report_{span.year}_{MONTH_NAMES_RU[span.index]}"
        elif span.kind == "quarter":
            default_name = f"Final_Report_{span.year}_Q{span.index}"
        else:
            default_name = f"Final_Report_{span.year}_год"
        out_dir = QFileDialog.getExistingDirectory(self, "Куда сохранить отчёт?")
        if not out_dir:
            return

        out_paths = {fmt: str(Path(out_dir) / f"{default_name}.{fmt}") for fmt in formats}

        self.generate_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_label.setText("Запуск…")

        period_key = self.period_combo.currentData()
        # See the comment in _load_file: parent to `self` to avoid the
        # QThread being destroyed by Python GC while still running.
        report_thread = QThread(self)
        report_worker = ReportWorker(
            self.file_path,
            period_key,
            formats,
            out_paths,
            self._current_profile(),
            self.work_dir,
            self.parts_file_path,
            self.support_file_path,
            ai_settings=self._ai_settings if ai_enabled else None,
        )
        report_worker.moveToThread(report_thread)
        report_thread.started.connect(report_worker.run)
        report_worker.progress.connect(self.progress_label.setText)
        report_worker.finished.connect(self._on_report_finished)
        report_worker.failed.connect(self._on_report_failed)
        report_worker.finished.connect(report_thread.quit)
        report_worker.failed.connect(report_thread.quit)
        report_thread.finished.connect(report_thread.deleteLater)
        self._report_thread = report_thread
        self._report_worker = report_worker
        report_thread.start()

    def _on_report_finished(self, paths: list[str]):
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.progress_label.setText("Готово.")
        QMessageBox.information(self, "Отчёт сформирован", "Файлы сохранены:\n" + "\n".join(paths))

    def _on_report_failed(self, message: str):
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.progress_label.setText("Ошибка.")
        QMessageBox.critical(self, "Ошибка формирования отчёта", message)

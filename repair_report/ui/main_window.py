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

from repair_report import profile as profile_store
from repair_report.analytics.periods import MONTH_NAMES_RU
from repair_report.ui.profile_dialog import ProfileDialog
from repair_report.ui.worker import ProbeWorker, ReportWorker

logger = logging.getLogger(__name__)


class DropArea(QLabel):
    def __init__(self, on_file_dropped):
        super().__init__("Перетащите файл WR_Consolidated_List_*.xlsx сюда\nили нажмите «Выбрать файл»")
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
        self.setMinimumSize(640, 640)

        self.file_path: str | None = None
        self.report = None  # ReportData for the currently probed file/period
        self.work_dir = tempfile.mkdtemp(prefix="repair_report_")
        self._probe_thread: QThread | None = None
        self._report_thread: QThread | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- File selection ---
        file_group = QGroupBox("1. Исходный файл")
        file_layout = QVBoxLayout(file_group)
        self.drop_area = DropArea(self._load_file)
        file_layout.addWidget(self.drop_area)
        pick_btn = QPushButton("Выбрать файл…")
        pick_btn.clicked.connect(self._pick_file)
        file_layout.addWidget(pick_btn)
        self.file_status_label = QLabel("Файл не выбран.")
        file_layout.addWidget(self.file_status_label)
        layout.addWidget(file_group)

        # --- Period selection ---
        period_group = QGroupBox("2. Отчётный период")
        period_layout = QVBoxLayout(period_group)
        self.period_combo = QComboBox()
        self.period_combo.setEnabled(False)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        period_layout.addWidget(self.period_combo)
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

        # --- Options ---
        options_group = QGroupBox("4. Настройки отчёта")
        options_layout = QVBoxLayout(options_group)
        self.experimental_checkbox = QCheckBox("Включить экспериментальные разделы (запчасти/техподдержка)")
        self.experimental_checkbox.setToolTip(
            "Логика разделов 10-11 не подтверждена доступной выгрузкой (см. README) — "
            "по умолчанию отключено, чтобы не показывать недостоверные цифры."
        )
        options_layout.addWidget(self.experimental_checkbox)
        layout.addWidget(options_group)

        # --- Format selection ---
        format_group = QGroupBox("5. Формат выгрузки")
        format_layout = QHBoxLayout(format_group)
        self.format_html = QCheckBox("HTML")
        self.format_pdf = QCheckBox("PDF")
        self.format_docx = QCheckBox("DOCX")
        self.format_pdf.setChecked(True)
        for cb in (self.format_html, self.format_pdf, self.format_docx):
            format_layout.addWidget(cb)
        layout.addWidget(format_group)

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

    # ---- file loading ----
    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл выгрузки", "", "Excel (*.xlsx *.xlsm)")
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        self.file_path = path
        self.file_status_label.setText(f"Загрузка: {os.path.basename(path)}…")
        self.generate_btn.setEnabled(False)
        self.period_combo.setEnabled(False)

        self._probe_thread = QThread()
        self._probe_worker = ProbeWorker(path)
        self._probe_worker.moveToThread(self._probe_thread)
        self._probe_thread.started.connect(self._probe_worker.run)
        self._probe_worker.finished.connect(self._on_probe_finished)
        self._probe_worker.failed.connect(self._on_probe_failed)
        self._probe_worker.finished.connect(self._probe_thread.quit)
        self._probe_worker.failed.connect(self._probe_thread.quit)
        self._probe_thread.start()

    def _on_probe_finished(self, report):
        self.report = report
        self.file_status_label.setText(
            f"Файл загружен: {os.path.basename(self.file_path)} — найдено периодов: {len(report.all_periods)}."
        )
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        for p in report.all_periods:
            self.period_combo.addItem(f"{MONTH_NAMES_RU[p.month]} {p.year}", userData=p.key)
        # default: latest period selected
        self.period_combo.setCurrentIndex(self.period_combo.count() - 1)
        self.period_combo.blockSignals(False)
        self.period_combo.setEnabled(True)
        self._update_previous_period_label(report)
        self.generate_btn.setEnabled(True)

    def _on_probe_failed(self, message: str):
        self.file_status_label.setText("Не удалось прочитать файл.")
        QMessageBox.critical(self, "Ошибка чтения файла", message)

    def _update_previous_period_label(self, report):
        if report.previous_available:
            self.previous_period_label.setText(f"✓ {report.previous_note}")
            self.previous_period_label.setStyleSheet("color: #2e7d32;")
        else:
            self.previous_period_label.setText(f"⚠ {report.previous_note}")
            self.previous_period_label.setStyleSheet("color: #b8860b;")

    def _on_period_changed(self, _index: int):
        # Re-probe with the newly selected period so the "previous period"
        # note reflects the right calendar-previous month.
        if not self.file_path:
            return
        period_key = self.period_combo.currentData()
        if period_key is None:
            return
        from repair_report.analytics.engine import build_report

        try:
            self.report = build_report(self.file_path, period_key, self.experimental_checkbox.isChecked())
            self._update_previous_period_label(self.report)
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

        period = self.report.current_period
        default_name = f"Final_Report_{period.year}_{MONTH_NAMES_RU[period.month]}"
        out_dir = QFileDialog.getExistingDirectory(self, "Куда сохранить отчёт?")
        if not out_dir:
            return

        out_paths = {fmt: str(Path(out_dir) / f"{default_name}.{fmt}") for fmt in formats}

        self.generate_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_label.setText("Запуск…")

        period_key = self.period_combo.currentData()
        self._report_thread = QThread()
        self._report_worker = ReportWorker(
            self.file_path,
            period_key,
            formats,
            out_paths,
            self._current_profile(),
            self.experimental_checkbox.isChecked(),
            self.work_dir,
        )
        self._report_worker.moveToThread(self._report_thread)
        self._report_thread.started.connect(self._report_worker.run)
        self._report_worker.progress.connect(self.progress_label.setText)
        self._report_worker.finished.connect(self._on_report_finished)
        self._report_worker.failed.connect(self._on_report_failed)
        self._report_worker.finished.connect(self._report_thread.quit)
        self._report_worker.failed.connect(self._report_thread.quit)
        self._report_thread.start()

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

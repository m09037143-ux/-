"""ИИ (AI summary) settings editor: API key / Folder ID / model URI for the
Yandex Cloud endpoint used to write the optional report summary. Same
pattern as ProfileDialog. The API key field is masked (QLineEdit.Password)
since it's a real credential -- see ai_settings.py for where/how it's
stored (local app-data only, never in the repo)."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox

from repair_report.ai_settings import AiSettings


class AiSettingsDialog(QDialog):
    def __init__(self, parent=None, settings: AiSettings | None = None):
        super().__init__(parent)
        self.setWindowTitle("Настройки ИИ")
        self.setMinimumWidth(460)

        settings = settings or AiSettings()

        note = QLabel(
            "Используется для необязательного ИИ-резюме в начале отчёта (галочка «Провести анализ "
            "с помощью ИИ»). Ключ хранится только на этом компьютере и никогда не попадает в отчёт "
            "или в исходный код программы."
        )
        note.setWordWrap(True)

        self.api_key_edit = QLineEdit(settings.api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.folder_id_edit = QLineEdit(settings.folder_id)
        self.model_edit = QLineEdit(settings.model)
        self.model_edit.setPlaceholderText("gpt://<Folder ID>/aliceai-llm/latest (по умолчанию, если оставить пустым)")
        self.enabled_checkbox = QCheckBox("Включать анализ ИИ по умолчанию для новых отчётов")
        self.enabled_checkbox.setChecked(settings.enabled_by_default)

        form = QFormLayout(self)
        form.addRow(note)
        form.addRow("API-ключ*", self.api_key_edit)
        form.addRow("Folder ID*", self.folder_id_edit)
        form.addRow("Модель (необязательно)", self.model_edit)
        form.addRow(self.enabled_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._result_settings: AiSettings | None = None

    def _on_accept(self):
        api_key = self.api_key_edit.text().strip()
        folder_id = self.folder_id_edit.text().strip()
        if not api_key or not folder_id:
            QMessageBox.warning(self, "Настройки ИИ", "Укажите API-ключ и Folder ID.")
            return
        self._result_settings = AiSettings(
            api_key=api_key,
            folder_id=folder_id,
            model=self.model_edit.text().strip(),
            enabled_by_default=self.enabled_checkbox.isChecked(),
        )
        self.accept()

    def result_settings(self) -> AiSettings | None:
        return self._result_settings

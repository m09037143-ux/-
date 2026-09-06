"""Client profile editor (spec §2.3/§5): the report header's static requisites,
entered once per client and reused every month."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox

from repair_report.profile import ClientProfile


class ProfileDialog(QDialog):
    def __init__(self, parent=None, profile: ClientProfile | None = None):
        super().__init__(parent)
        self.setWindowTitle("Профиль заказчика")
        self.setMinimumWidth(420)

        self.name_edit = QLineEdit(profile.name if profile else "")
        self.executor_edit = QLineEdit(profile.executor if profile else "")
        self.customer_edit = QLineEdit(profile.customer if profile else "")
        self.contract_number_edit = QLineEdit(profile.contract_number if profile else "")
        self.contract_date_edit = QLineEdit(profile.contract_date if profile else "")
        self.signatory_title_edit = QLineEdit(profile.signatory_title if profile else "")
        self.signatory_name_edit = QLineEdit(profile.signatory_name if profile else "")

        form = QFormLayout(self)
        form.addRow("Название профиля*", self.name_edit)
        form.addRow("Исполнитель", self.executor_edit)
        form.addRow("Заказчик", self.customer_edit)
        form.addRow("Номер договора", self.contract_number_edit)
        form.addRow("Дата договора (ДД.ММ.ГГГГ)", self.contract_date_edit)
        form.addRow("Должность подписанта", self.signatory_title_edit)
        form.addRow("ФИО подписанта", self.signatory_name_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._result_profile: ClientProfile | None = None

    def _on_accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Профиль заказчика", "Укажите название профиля.")
            return
        self._result_profile = ClientProfile(
            name=self.name_edit.text().strip(),
            executor=self.executor_edit.text().strip(),
            customer=self.customer_edit.text().strip(),
            contract_number=self.contract_number_edit.text().strip(),
            contract_date=self.contract_date_edit.text().strip(),
            signatory_title=self.signatory_title_edit.text().strip(),
            signatory_name=self.signatory_name_edit.text().strip(),
        )
        self.accept()

    def result_profile(self) -> ClientProfile | None:
        return self._result_profile

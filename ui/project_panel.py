from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from core.models import ProjectMeta


class ProjectPanel(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.client_name = QLineEdit()
        self.order_number = QLineEdit()
        self.material = QLineEdit()
        self.creation_date = QLineEdit()
        self.creation_date.setReadOnly(True)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumBlockCount(2000)

        form = QFormLayout()
        form.addRow("Client name", self.client_name)
        form.addRow("Order number", self.order_number)
        form.addRow("Main material", self.material)
        form.addRow("Creation date", self.creation_date)
        form.addRow("Notes", self.notes)
        group = QGroupBox("Project metadata")
        group.setLayout(form)
        layout = QVBoxLayout(self)
        layout.addWidget(group)
        layout.addStretch(1)

        self.client_name.textChanged.connect(lambda *_: self.changed.emit())
        self.order_number.textChanged.connect(lambda *_: self.changed.emit())
        self.material.textChanged.connect(lambda *_: self.changed.emit())
        self.notes.textChanged.connect(self.changed.emit)

    def set_meta(self, meta: ProjectMeta) -> None:
        self.client_name.setText(meta.client_name)
        self.order_number.setText(meta.order_number)
        self.material.setText(meta.material)
        self.creation_date.setText(meta.creation_date)
        self.notes.setPlainText(meta.notes)

    def meta(self) -> ProjectMeta:
        return ProjectMeta(
            client_name=self.client_name.text(),
            order_number=self.order_number.text(),
            material=self.material.text(),
            notes=self.notes.toPlainText(),
            creation_date=self.creation_date.text(),
        )

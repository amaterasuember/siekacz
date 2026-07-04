from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from database import repositories


class LeftoversPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.headers = ["id", "type", "material", "thickness", "profile", "width", "height", "length", "source", "status", "created_at"]
        self.table = QTableWidget(0, len(self.headers))
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        refresh = QPushButton("Refresh")
        mark_used = QPushButton("Mark used")
        mark_discarded = QPushButton("Mark discarded")
        refresh.clicked.connect(self.refresh)
        mark_used.clicked.connect(lambda: self._mark("used"))
        mark_discarded.clicked.connect(lambda: self._mark("discarded"))
        buttons = QHBoxLayout()
        buttons.addWidget(refresh)
        buttons.addWidget(mark_used)
        buttons.addWidget(mark_discarded)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        self.table.setRowCount(0)
        for item in repositories.list_leftovers():
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, header in enumerate(self.headers):
                self.table.setItem(row, column, QTableWidgetItem(str(item.get(header, "") or "")))

    def _mark(self, status: str) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                repositories.update_leftover_status(int(item.text()), status)
        self.refresh()


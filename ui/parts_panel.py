from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget

from core.models import LinearPart, SheetPart
from .stock_panel import EditableTable, _bool, _float, _int


class PartsPanel(QWidget):
    changed = Signal()

    sheet_headers = [
        "name",
        "width",
        "height",
        "quantity",
        "material",
        "thickness",
        "allow_rotation",
        "grain_direction",
        "label",
        "priority",
        "notes",
    ]
    linear_headers = ["name", "length", "quantity", "material", "label", "priority", "notes"]

    def __init__(self) -> None:
        super().__init__()
        self.sheet_filter = QLineEdit()
        self.sheet_filter.setPlaceholderText("Filter sheet parts")
        self.linear_filter = QLineEdit()
        self.linear_filter.setPlaceholderText("Filter linear parts")
        self.sheet_table = EditableTable(self.sheet_headers, ["Panel", 300, 200, 1, "POM-C", 10, True, "none", "", 0, ""])
        self.linear_table = EditableTable(self.linear_headers, ["Rod cut", 250, 1, "POM-C", "", 0, ""])
        self.sheet_table.changed.connect(self.changed.emit)
        self.linear_table.changed.connect(self.changed.emit)
        self.sheet_filter.textChanged.connect(lambda text: self._filter(self.sheet_table, text))
        self.linear_filter.textChanged.connect(lambda text: self._filter(self.linear_table, text))

        tabs = QTabWidget()
        tabs.addTab(self._wrap(self.sheet_table, self.sheet_filter), "2D sheet parts")
        tabs.addTab(self._wrap(self.linear_table, self.linear_filter), "1D linear parts")
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

    def _wrap(self, table: EditableTable, filter_box: QLineEdit) -> QWidget:
        widget = QWidget()
        buttons = QHBoxLayout()
        for label, callback in (
            ("Add", table.add_row),
            ("Remove", table.remove_selected),
            ("Duplicate", table.duplicate_selected),
            ("Paste", table.paste),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(widget)
        layout.addWidget(filter_box)
        layout.addLayout(buttons)
        layout.addWidget(table)
        return widget

    def _filter(self, table: EditableTable, text: str) -> None:
        needle = text.strip().lower()
        for row in range(table.rowCount()):
            haystack = " ".join(table.cell_text(row, col).lower() for col in range(table.columnCount()))
            table.setRowHidden(row, bool(needle and needle not in haystack))

    def set_parts(self, sheet_parts: list[SheetPart], linear_parts: list[LinearPart]) -> None:
        self.sheet_table.load_dicts([vars(item) for item in sheet_parts])
        self.linear_table.load_dicts([vars(item) for item in linear_parts])

    def sheet_parts(self) -> list[SheetPart]:
        result = []
        for row in self.sheet_table.rows_as_dicts():
            result.append(
                SheetPart(
                    name=row["name"],
                    width=_float(row["width"]),
                    height=_float(row["height"]),
                    quantity=_int(row["quantity"]),
                    material=row["material"],
                    thickness=_float(row["thickness"]),
                    allow_rotation=_bool(row["allow_rotation"]),
                    grain_direction=row["grain_direction"] or "none",
                    label=row["label"],
                    priority=_int(row["priority"], 0),
                    notes=row["notes"],
                )
            )
        self.sheet_table.highlight_invalid({"width", "height", "thickness"}, {"quantity"})
        return result

    def linear_parts(self) -> list[LinearPart]:
        result = []
        for row in self.linear_table.rows_as_dicts():
            result.append(
                LinearPart(
                    name=row["name"],
                    length=_float(row["length"]),
                    quantity=_int(row["quantity"]),
                    material=row["material"],
                    label=row["label"],
                    priority=_int(row["priority"], 0),
                    notes=row["notes"],
                )
            )
        self.linear_table.highlight_invalid({"length"}, {"quantity"})
        return result

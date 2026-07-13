from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.models import LinearStock, SheetStock


def _item(value: Any) -> QTableWidgetItem:
    cell = QTableWidgetItem(str(value))
    cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsEditable)
    return cell


def _float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return default


def _int(value: str, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except ValueError:
        return default


def _bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "tak"}


class EditableTable(QTableWidget):
    changed = Signal()

    def __init__(self, headers: list[str], defaults: list[Any]) -> None:
        super().__init__(0, len(headers))
        self.headers = headers
        self.defaults = defaults
        self.setHorizontalHeaderLabels(headers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemChanged.connect(lambda *_: self.changed.emit())
        QShortcut(QKeySequence.StandardKey.Paste, self, self.paste)
        QShortcut(QKeySequence.StandardKey.Copy, self, self.copy)
        self.horizontalHeader().setStretchLastSection(True)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Tab:
            row = self.currentRow()
            col = self.currentColumn()
            if row == self.rowCount() - 1 and 0 <= col < len(self.headers):
                if self.headers[col] == "quantity":
                    # Add new row with defaults, but maybe keep material/thickness from current row?
                    # Let's just use defaults for now.
                    self.add_row()
                    self.setCurrentCell(self.rowCount() - 1, 0)
                    return
        super().keyPressEvent(event)

    def add_row(self, values: list[Any] | None = None) -> None:
        self.setSortingEnabled(False)
        row = self.rowCount()
        self.insertRow(row)
        for column, value in enumerate(values or self.defaults):
            self.setItem(row, column, _item(value))
        self.setSortingEnabled(True)
        self.changed.emit()

    def duplicate_selected(self) -> None:
        rows = sorted({i.row() for i in self.selectedIndexes()})
        for row in rows:
            self.add_row([self.item(row, c).text() if self.item(row, c) else "" for c in range(self.columnCount())])

    def remove_selected(self) -> None:
        for row in sorted({i.row() for i in self.selectedIndexes()}, reverse=True):
            self.removeRow(row)
        self.changed.emit()

    def paste(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        start_row = self.currentRow() if self.currentRow() >= 0 else self.rowCount()
        start_col = self.currentColumn() if self.currentColumn() >= 0 else 0
        for r, line in enumerate(text.splitlines()):
            if start_row + r >= self.rowCount():
                self.add_row()
            for c, value in enumerate(line.split("\t")):
                if start_col + c < self.columnCount():
                    self.setItem(start_row + r, start_col + c, _item(value))
        self.changed.emit()

    def copy(self) -> None:
        rows = sorted({i.row() for i in self.selectedIndexes()})
        cols = sorted({i.column() for i in self.selectedIndexes()})
        if not rows or not cols:
            return
        lines = []
        for row in rows:
            lines.append("\t".join(self.item(row, col).text() if self.item(row, col) else "" for col in cols))
        QApplication.clipboard().setText("\n".join(lines))

    def rows_as_dicts(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for row in range(self.rowCount()):
            data = {self.headers[col]: self.item(row, col).text() if self.item(row, col) else "" for col in range(self.columnCount())}
            if any(value.strip() for value in data.values()):
                rows.append(data)
        return rows

    def load_dicts(self, rows: list[dict[str, Any]]) -> None:
        self.setRowCount(0)
        for row in rows:
            self.add_row([row.get(header, "") for header in self.headers])
        self.changed.emit()

    def highlight_invalid(self, numeric_headers: set[str], integer_headers: set[str] | None = None) -> None:
        integer_headers = integer_headers or set()
        app = QApplication.instance()
        theme = app.property("theme") if app else "dark"
        invalid_color = QColor("#ffd6dc") if theme == "light" else QColor("#5b2631")
        normal_color = QColor("#ffffff") if theme == "light" else QColor("#15191f")
        for row in range(self.rowCount()):
            for col, header in enumerate(self.headers):
                cell = self.item(row, col)
                if not cell:
                    continue
                invalid = False
                if header in numeric_headers:
                    invalid = _float(cell.text(), -1) <= 0
                if header in integer_headers:
                    invalid = _int(cell.text(), -1) <= 0
                cell.setBackground(invalid_color if invalid else normal_color)

    def _menu(self, position) -> None:
        menu = QMenu(self)
        add = QAction("Add row", self)
        duplicate = QAction("Duplicate row", self)
        remove = QAction("Remove row", self)
        paste = QAction("Paste from clipboard", self)
        add.triggered.connect(lambda: self.add_row())
        duplicate.triggered.connect(self.duplicate_selected)
        remove.triggered.connect(self.remove_selected)
        paste.triggered.connect(self.paste)
        for action in (add, duplicate, remove, paste):
            menu.addAction(action)
        menu.exec(self.viewport().mapToGlobal(position))


class StockPanel(QWidget):
    changed = Signal()

    sheet_headers = [
        "material",
        "thickness",
        "width",
        "height",
        "quantity",
        "price",
        "grain_direction",
        "allow_rotation",
        "min_offcut_width",
        "min_offcut_height",
    ]
    linear_headers = ["material", "profile", "length", "quantity", "price", "kerf", "min_offcut_length"]

    def __init__(self) -> None:
        super().__init__()
        self.sheet_table = EditableTable(self.sheet_headers, ["POM-C", 10, 2000, 1000, 1, 0, "none", True, 120, 120])
        self.linear_table = EditableTable(self.linear_headers, ["POM-C", "Rod", 1000, 1, 0, 3, 80])
        self.sheet_table.changed.connect(self.changed.emit)
        self.linear_table.changed.connect(self.changed.emit)

        tabs = QTabWidget()
        tabs.addTab(self._wrap(self.sheet_table), "Sheet stock")
        tabs.addTab(self._wrap(self.linear_table), "Linear stock")
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

    def _wrap(self, table: EditableTable) -> QWidget:
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
        layout.addLayout(buttons)
        layout.addWidget(table)
        return widget

    def set_stock(self, sheet_stock: list[SheetStock], linear_stock: list[LinearStock]) -> None:
        self.sheet_table.load_dicts([vars(item) for item in sheet_stock])
        self.linear_table.load_dicts([vars(item) for item in linear_stock])

    def sheet_stock(self) -> list[SheetStock]:
        result = []
        for row in self.sheet_table.rows_as_dicts():
            result.append(
                SheetStock(
                    material=row["material"],
                    thickness=_float(row["thickness"]),
                    width=_float(row["width"]),
                    height=_float(row["height"]),
                    quantity=_int(row["quantity"]),
                    price=_float(row["price"]),
                    grain_direction=row["grain_direction"] or "none",
                    allow_rotation=_bool(row["allow_rotation"]),
                    min_offcut_width=_float(row["min_offcut_width"]),
                    min_offcut_height=_float(row["min_offcut_height"]),
                )
            )
        self.sheet_table.highlight_invalid({"thickness", "width", "height"}, {"quantity"})
        return result

    def linear_stock(self) -> list[LinearStock]:
        result = []
        for row in self.linear_table.rows_as_dicts():
            result.append(
                LinearStock(
                    material=row["material"],
                    profile=row["profile"],
                    length=_float(row["length"]),
                    quantity=_int(row["quantity"]),
                    price=_float(row["price"]),
                    kerf=_float(row["kerf"]),
                    min_offcut_length=_float(row["min_offcut_length"]),
                )
            )
        self.linear_table.highlight_invalid({"length"}, {"quantity"})
        return result

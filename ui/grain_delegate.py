from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget

GRAIN_ROLE = int(Qt.ItemDataRole.UserRole) + 26


def _star_path(rect: QRectF) -> QPainterPath:
    path = QPainterPath()
    center = rect.center()
    radius = min(rect.width(), rect.height()) * 0.38
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else radius * 0.44
        point = QPointF(center.x() + math.cos(angle) * r, center.y() + math.sin(angle) * r)
        if i == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    return path


def _star_icon() -> QIcon:
    pixmap = QPixmap(40, 40)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#d49a28"))
    painter.drawPath(_star_path(QRectF(0, 0, 20, 20)))
    painter.end()
    return QIcon(pixmap)


class GrainTableDelegate(QStyledItemDelegate):
    """Keep editable dimensions and put an independent side selector beside them."""

    def paint(self, painter, option, index) -> None:
        if index.column() not in (2, 3):
            super().paint(painter, option, index)
            return
        text_option = QStyleOptionViewItem(option)
        text_option.rect.adjust(0, 0, -22, 0)
        super().paint(painter, text_option, index)
        material = index.siblingAtColumn(0)
        table = self.parent()
        assert isinstance(table, QTableWidget)
        active = material.data(GRAIN_ROLE) == ("x" if index.column() == int(table.property("grainWidthColumn")) else "y")
        star_rect = option.rect.adjusted(option.rect.width() - 23, 0, -2, 0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#d49a28" if active else "#8190a5")
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(color if active else Qt.BrushStyle.NoBrush)
        painter.drawPath(_star_path(QRectF(star_rect)))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if (event.type() == QEvent.Type.MouseButtonRelease
                and index.column() in (2, 3)
                and event.button() == Qt.MouseButton.LeftButton
                and event.position().x() >= option.rect.right() - 24):
            self.choose_grain(index.row(), event.globalPosition().toPoint())
            return True
        return super().editorEvent(event, model, option, index)

    def choose_grain(self, row, position) -> None:
        table = self.parent()
        if not isinstance(table, QTableWidget) or row < 0:
            return
        material = table.item(row, 0)
        if material is None:
            return
        menu = QMenu(table)
        menu.setTitle("Krojenie wzdłuż słojów")
        title = menu.addAction("Krojenie wzdłuż słojów")
        title.setEnabled(False)
        menu.addSeparator()
        width_column = int(table.property("grainWidthColumn"))
        for axis, column, label in (("none", 0, "Bez przypisania"),
                                    ("x" if width_column == 2 else "y", 2, "Wysokość"),
                                    ("x" if width_column == 3 else "y", 3, "Szerokość")):
            item = table.item(row, column)
            text = label if axis == "none" else f"{label}: {item.text() if item else '—'} mm"
            action = menu.addAction(text)
            if axis != "none":
                action.setIcon(_star_icon())
            action.setCheckable(True)
            action.setChecked((material.data(GRAIN_ROLE) or "none") == axis)
            action.triggered.connect(lambda checked=False, value=axis: material.setData(GRAIN_ROLE, value))
        menu.addSeparator()
        hint = menu.addAction("Oznaczone boki będą równoległe")
        hint.setEnabled(False)
        menu.exec(position)
        table.viewport().update()


def enable_grain_menu(table: QTableWidget) -> None:
    table.setProperty("grainWidthColumn", 2 if table.columnCount() == 8 else 3)
    delegate = table.itemDelegate()
    assert isinstance(delegate, GrainTableDelegate)
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.customContextMenuRequested.connect(
        lambda pos: delegate.choose_grain(table.indexAt(pos).row(), table.viewport().mapToGlobal(pos))
    )
    table.setToolTip("Kliknij gwiazdkę przy wymiarze lub użyj prawego przycisku myszy.\n"
                     "Złote gwiazdki łączą boki płyty i formatki wzdłuż słojów.")

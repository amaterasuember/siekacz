from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


DRAWING_ROLE = 0


class TechnicalCanvas(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#07111f"))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.tool = "select"
        self.snap_enabled = True
        self.grid_size = 10.0
        self._start: QPointF | None = None
        self._preview: QGraphicsItem | None = None
        self._panning = False
        self._pan_origin = QPoint()
        self.board_width = 1000.0
        self.board_height = 2000.0
        self.board_item = QGraphicsRectItem()
        self.board_item.setZValue(-10)
        self.board_item.setPen(QPen(QColor("#5689c9"), 1.6))
        self.board_item.setBrush(QBrush(QColor("#0d1b2e")))
        self.scene().addItem(self.board_item)
        self.set_board_size(self.board_width, self.board_height)

    def set_board_size(self, width: float, height: float) -> None:
        self.board_width = max(1.0, float(width))
        self.board_height = max(1.0, float(height))
        self.board_item.setRect(0.0, 0.0, self.board_width, self.board_height)
        margin = max(80.0, min(self.board_width, self.board_height) * 0.08)
        self.scene().setSceneRect(
            -margin,
            -margin,
            self.board_width + margin * 2,
            self.board_height + margin * 2,
        )
        self.fit_board()

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.setDragMode(
            QGraphicsView.DragMode.RubberBandDrag
            if tool == "select"
            else QGraphicsView.DragMode.NoDrag
        )
        self.viewport().setCursor(
            Qt.CursorShape.ArrowCursor if tool == "select" else Qt.CursorShape.CrossCursor
        )

    def fit_board(self) -> None:
        self.fitInView(self.board_item.sceneBoundingRect().adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)

    def _snap(self, point: QPointF) -> QPointF:
        if not self.snap_enabled:
            return point
        size = max(1.0, self.grid_size)
        return QPointF(round(point.x() / size) * size, round(point.y() / size) * size)

    def _drawing_pen(self, hole: bool = False) -> QPen:
        pen = QPen(QColor("#ffb454" if hole else "#6bc5ff"), 2.0)
        if hole:
            pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        return pen

    def _prepare_item(self, item: QGraphicsItem, hole: bool = False) -> None:
        if hasattr(item, "setPen"):
            item.setPen(self._drawing_pen(hole))  # type: ignore[attr-defined]
        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsPathItem)):
            item.setBrush(QBrush(QColor(42, 118, 184, 24)))
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        item.setData(DRAWING_ROLE, "hole" if hole else "drawing")

    def _new_preview(self, start: QPointF) -> QGraphicsItem:
        if self.tool == "line":
            item: QGraphicsItem = QGraphicsLineItem(start.x(), start.y(), start.x(), start.y())
        elif self.tool in {"circle", "hole"}:
            item = QGraphicsEllipseItem(QRectF(start, start))
        else:
            item = QGraphicsRectItem(QRectF(start, start))
        self._prepare_item(item, self.tool == "hole")
        self.scene().addItem(item)
        return item

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_origin = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self.tool == "select" or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        point = self._snap(self.mapToScene(event.position().toPoint()))
        self._start = point
        self._preview = self._new_preview(point)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            position = event.position().toPoint()
            delta = position - self._pan_origin
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_origin = position
            event.accept()
            return
        if self._start is None or self._preview is None:
            super().mouseMoveEvent(event)
            return
        current = self._snap(self.mapToScene(event.position().toPoint()))
        if isinstance(self._preview, QGraphicsLineItem):
            self._preview.setLine(self._start.x(), self._start.y(), current.x(), current.y())
        else:
            rect = QRectF(self._start, current).normalized()
            if isinstance(self._preview, QGraphicsRectItem):
                self._preview.setRect(rect)
            elif isinstance(self._preview, QGraphicsEllipseItem):
                self._preview.setRect(rect)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.viewport().setCursor(
                Qt.CursorShape.ArrowCursor if self.tool == "select" else Qt.CursorShape.CrossCursor
            )
            event.accept()
            return
        if self._preview is not None and event.button() == Qt.MouseButton.LeftButton:
            bounds = self._preview.sceneBoundingRect()
            if bounds.width() < 0.01 and bounds.height() < 0.01:
                self.scene().removeItem(self._preview)
            self._preview = None
            self._start = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        event.accept()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        spacing = max(5.0, self.grid_size)
        left = math.floor(rect.left() / spacing) * spacing
        top = math.floor(rect.top() / spacing) * spacing
        lines = []
        x = left
        while x <= rect.right():
            lines.append((QPointF(x, rect.top()), QPointF(x, rect.bottom())))
            x += spacing
        y = top
        while y <= rect.bottom():
            lines.append((QPointF(rect.left(), y), QPointF(rect.right(), y)))
            y += spacing
        pen = QPen(QColor(64, 91, 124, 52), 0)
        painter.setPen(pen)
        for start, end in lines:
            painter.drawLine(start, end)

    def drawing_items(self) -> list[QGraphicsItem]:
        return [
            item
            for item in self.scene().items()
            if item is not self.board_item and item.data(DRAWING_ROLE) in {"drawing", "hole"}
        ]

    def drawing_bounds(self) -> QRectF:
        items = self.drawing_items()
        if not items:
            return QRectF()
        def geometry_bounds(item: QGraphicsItem) -> QRectF:
            if isinstance(item, QGraphicsRectItem):
                return item.mapRectToScene(item.rect())
            if isinstance(item, QGraphicsEllipseItem):
                return item.mapRectToScene(item.rect())
            if isinstance(item, QGraphicsPathItem):
                return item.mapRectToScene(item.path().boundingRect())
            if isinstance(item, QGraphicsLineItem):
                line = item.line()
                first = item.mapToScene(line.p1())
                second = item.mapToScene(line.p2())
                return QRectF(first, second).normalized()
            return item.sceneBoundingRect()

        bounds = geometry_bounds(items[0])
        for item in items[1:]:
            bounds = bounds.united(geometry_bounds(item))
        return bounds

    def delete_selected(self) -> None:
        for item in self.scene().selectedItems():
            if item is not self.board_item:
                self.scene().removeItem(item)

    def clear_drawing(self) -> None:
        for item in self.drawing_items():
            self.scene().removeItem(item)

    def rotate_selected(self) -> None:
        for item in self.scene().selectedItems():
            center = item.boundingRect().center()
            item.setTransformOriginPoint(center)
            item.setRotation(item.rotation() + 90.0)

    def convert_selected_rectangles(self, radius: float, fillet: bool) -> None:
        for item in list(self.scene().selectedItems()):
            if not isinstance(item, QGraphicsRectItem) or item is self.board_item:
                continue
            rect = item.sceneBoundingRect()
            amount = min(max(0.0, radius), rect.width() / 2, rect.height() / 2)
            path = QPainterPath()
            if fillet:
                path.addRoundedRect(rect, amount, amount)
            else:
                polygon = QPolygonF(
                    [
                        QPointF(rect.left() + amount, rect.top()),
                        QPointF(rect.right() - amount, rect.top()),
                        QPointF(rect.right(), rect.top() + amount),
                        QPointF(rect.right(), rect.bottom() - amount),
                        QPointF(rect.right() - amount, rect.bottom()),
                        QPointF(rect.left() + amount, rect.bottom()),
                        QPointF(rect.left(), rect.bottom() - amount),
                        QPointF(rect.left(), rect.top() + amount),
                    ]
                )
                path.addPolygon(polygon)
                path.closeSubpath()
            replacement = QGraphicsPathItem(path)
            self._prepare_item(replacement)
            self.scene().removeItem(item)
            self.scene().addItem(replacement)
            replacement.setSelected(True)


class TechnicalEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SIEKACZ CAD - eksperymentalny edytor techniczny")
        self.resize(1180, 820)
        self.setMinimumSize(900, 640)
        self.result_dimensions: tuple[float, float] | None = None
        self.canvas = TechnicalCanvas(self)

        self.width_input = self._dimension_input(1000.0)
        self.height_input = self._dimension_input(2000.0)
        self.radius_input = self._dimension_input(20.0)
        self.radius_input.setMaximum(10000.0)
        self.snap = QCheckBox("Przyciągaj do siatki")
        self.snap.setChecked(True)
        self.snap.toggled.connect(lambda checked: setattr(self.canvas, "snap_enabled", checked))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._toolbar())
        layout.addWidget(self.canvas, 1)
        bottom = QHBoxLayout()
        status = QLabel("Środkowy przycisk myszy: przesuwanie. Rolka: zoom.")
        status.setObjectName("summaryLabel")
        bottom.addWidget(status)
        bottom.addStretch(1)
        cancel = QPushButton("Zamknij")
        cancel.setObjectName("smallButton")
        cancel.clicked.connect(self.reject)
        add = QPushButton("Dodaj rysunek jako formatkę")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._accept_drawing)
        bottom.addWidget(cancel)
        bottom.addWidget(add)
        layout.addLayout(bottom)

        delete_action = QAction(self)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.canvas.delete_selected)
        self.addAction(delete_action)

    def _dimension_input(self, value: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setObjectName("premiumInput")
        field.setRange(0.1, 100000.0)
        field.setDecimals(2)
        field.setValue(value)
        field.setSuffix(" mm")
        field.setMinimumWidth(120)
        return field

    def _button(self, text: str, callback, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setObjectName("smallButton")
        button.setCheckable(checkable)
        button.clicked.connect(callback)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return button

    def _toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Płyta"))
        layout.addWidget(self.width_input)
        layout.addWidget(QLabel("x"))
        layout.addWidget(self.height_input)
        resize = self._button("Ustaw", self._apply_board_size)
        layout.addWidget(resize)
        layout.addSpacing(12)

        tools = [
            ("Wskaźnik", "select"),
            ("Linia", "line"),
            ("Prostokąt", "rect"),
            ("Okrąg", "circle"),
            ("Nawiercenie", "hole"),
        ]
        self.tool_buttons: list[QToolButton] = []
        for text, tool in tools:
            button = self._button(text, lambda checked=False, tool=tool: self._select_tool(tool), True)
            button.setChecked(tool == "select")
            self.tool_buttons.append(button)
            layout.addWidget(button)

        layout.addSpacing(8)
        layout.addWidget(QLabel("R/F"))
        layout.addWidget(self.radius_input)
        layout.addWidget(self._button("Promień", lambda: self.canvas.convert_selected_rectangles(self.radius_input.value(), True)))
        layout.addWidget(self._button("Faza", lambda: self.canvas.convert_selected_rectangles(self.radius_input.value(), False)))

        more = self._button("Więcej", lambda: None)
        menu = QMenu(more)
        menu.addAction("Obróć zaznaczone 90°", self.canvas.rotate_selected)
        menu.addAction("Usuń zaznaczone", self.canvas.delete_selected)
        menu.addAction("Wyczyść rysunek", self._confirm_clear)
        menu.addSeparator()
        menu.addAction("Wczytaj DXF", self._import_dxf)
        menu.addAction("Zapisz DXF", self._export_dxf)
        menu.addAction("Dopasuj widok", self.canvas.fit_board)
        more.setMenu(menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(more)
        layout.addWidget(self.snap)
        layout.addStretch(1)
        return bar

    def _select_tool(self, tool: str) -> None:
        for button in self.tool_buttons:
            button.setChecked(False)
        index = {"select": 0, "line": 1, "rect": 2, "circle": 3, "hole": 4}[tool]
        self.tool_buttons[index].setChecked(True)
        self.canvas.set_tool(tool)

    def _apply_board_size(self) -> None:
        self.canvas.set_board_size(self.width_input.value(), self.height_input.value())

    def _confirm_clear(self) -> None:
        if QMessageBox.question(self, "Wyczyść rysunek", "Usunąć wszystkie elementy rysunku?") == QMessageBox.StandardButton.Yes:
            self.canvas.clear_drawing()

    def _accept_drawing(self) -> None:
        bounds = self.canvas.drawing_bounds()
        if bounds.isEmpty():
            QMessageBox.information(self, "SIEKACZ CAD", "Najpierw narysuj lub wczytaj geometrię.")
            return
        self.result_dimensions = (round(bounds.width(), 3), round(bounds.height(), 3))
        self.accept()

    def _import_dxf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wczytaj DXF", "", "Plik DXF (*.dxf)")
        if not path:
            return
        try:
            import ezdxf
            from ezdxf import bbox

            document = ezdxf.readfile(path)
            entities = list(document.modelspace())
            extents = bbox.extents(entities, fast=False)
            if not extents.has_data:
                raise ValueError("Brak geometrii.")
            offset = QPointF(-float(extents.extmin.x), -float(extents.extmin.y))
            self.canvas.clear_drawing()
            for entity in entities:
                kind = entity.dxftype()
                if kind == "LINE":
                    start, end = entity.dxf.start, entity.dxf.end
                    item = QGraphicsLineItem(
                        start.x + offset.x(),
                        start.y + offset.y(),
                        end.x + offset.x(),
                        end.y + offset.y(),
                    )
                    self.canvas._prepare_item(item)
                    self.canvas.scene().addItem(item)
                elif kind == "CIRCLE":
                    center, radius = entity.dxf.center, float(entity.dxf.radius)
                    item = QGraphicsEllipseItem(
                        center.x - radius + offset.x(),
                        center.y - radius + offset.y(),
                        radius * 2,
                        radius * 2,
                    )
                    self.canvas._prepare_item(item, True)
                    self.canvas.scene().addItem(item)
                elif kind == "LWPOLYLINE":
                    points = [QPointF(point[0] + offset.x(), point[1] + offset.y()) for point in entity.get_points("xy")]
                    if len(points) < 2:
                        continue
                    path_item = QGraphicsPathItem()
                    path = QPainterPath(points[0])
                    for point in points[1:]:
                        path.lineTo(point)
                    if entity.closed:
                        path.closeSubpath()
                    path_item.setPath(path)
                    self.canvas._prepare_item(path_item)
                    self.canvas.scene().addItem(path_item)
            self.width_input.setValue(max(self.width_input.value(), float(extents.size.x)))
            self.height_input.setValue(max(self.height_input.value(), float(extents.size.y)))
            self._apply_board_size()
        except Exception as exc:
            QMessageBox.warning(self, "Import DXF", f"Nie udało się wczytać rysunku:\n{exc}")

    def _export_dxf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz DXF", "rysunek.dxf", "Plik DXF (*.dxf)")
        if not path:
            return
        try:
            import ezdxf

            target = Path(path).with_suffix(".dxf")
            document = ezdxf.new("R2010")
            modelspace = document.modelspace()
            for item in self.canvas.drawing_items():
                if isinstance(item, QGraphicsLineItem):
                    line = item.sceneTransform().map(item.line())
                    modelspace.add_line((line.x1(), line.y1()), (line.x2(), line.y2()))
                elif isinstance(item, QGraphicsEllipseItem):
                    rect = item.sceneBoundingRect()
                    if abs(rect.width() - rect.height()) < 0.01:
                        center = rect.center()
                        modelspace.add_circle((center.x(), center.y()), rect.width() / 2)
                    else:
                        center = rect.center()
                        modelspace.add_ellipse(
                            (center.x(), center.y()),
                            major_axis=(rect.width() / 2, 0),
                            ratio=rect.height() / rect.width(),
                        )
                else:
                    rect = item.sceneBoundingRect()
                    modelspace.add_lwpolyline(
                        [
                            (rect.left(), rect.top()),
                            (rect.right(), rect.top()),
                            (rect.right(), rect.bottom()),
                            (rect.left(), rect.bottom()),
                        ],
                        close=True,
                    )
            document.saveas(target)
        except Exception as exc:
            QMessageBox.warning(self, "Eksport DXF", f"Nie udało się zapisać rysunku:\n{exc}")

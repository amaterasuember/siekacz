from __future__ import annotations

"""Interactive read-only DXF/STEP/STL inspection and measurement window."""

import math
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cad.inspection import CadInspectionError, CadInspectionModel, load_cad_inspection
from database import repositories


def _distance_to_segment(point: QPointF, first: QPointF, second: QPointF) -> float:
    dx, dy = second.x() - first.x(), second.y() - first.y()
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(point.x() - first.x(), point.y() - first.y())
    t = max(0.0, min(1.0, ((point.x() - first.x()) * dx + (point.y() - first.y()) * dy) / length_sq))
    x, y = first.x() + t * dx, first.y() + t * dy
    return math.hypot(point.x() - x, point.y() - y)


class CadInspectionCanvas(QWidget):
    measurement_changed = Signal(str)
    view_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(500, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.model: CadInspectionModel | None = None
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.zoom = 1.0
        self.pan = QPointF()
        self._fit_scale = 1.0
        self._projected: list[QPointF] = []
        self._depths: list[float] = []
        self.selected_edge: int | None = None
        self.two_point_mode = False
        self.measurement_vertices: list[int] = []
        self._snap_position: QPointF | None = None
        self.measurement_points: list[tuple[float, float, float]] = []
        self._pressed_at: QPoint | None = None
        self._last_mouse: QPoint | None = None
        self._dragging = False
        self._pan_drag = False
        self._rotate_drag = False
        self.setToolTip(
            "Kliknij krawędź, aby ją zmierzyć. Przeciągnij LPM lub PPM, aby obracać; "
            "środkowym przyciskiem, aby przesuwać; rolką przybliżaj, a PPM + rolka obraca widok."
        )

    def set_model(self, model: CadInspectionModel) -> None:
        self.model = model
        group_counts: dict[tuple[str, int], int] = {}
        self._corner_indices: set[int] = set()
        for edge in model.edges:
            if edge.radius is None:
                self._corner_indices.update((edge.start, edge.end))
            else:
                for vertex in (edge.start, edge.end):
                    key = (edge.source_id, vertex)
                    group_counts[key] = group_counts.get(key, 0) + 1
        self._corner_indices.update(vertex for (_, vertex), count in group_counts.items() if count == 1)
        self.selected_edge = None
        self.measurement_vertices.clear()
        self.measurement_points.clear()
        self._snap_position = None
        if model.source_format == "DXF" and model.dimensions[2] < 1e-7:
            self.set_top_view()
        else:
            self.set_isometric_view()
        self.fit_model()

    def set_two_point_mode(self, enabled: bool) -> None:
        self.two_point_mode = bool(enabled)
        self.measurement_vertices.clear()
        self.measurement_points.clear()
        self._snap_position = None
        self.selected_edge = None
        self.measurement_changed.emit(
            "Kliknij pierwszy punkt geometrii." if enabled else "Kliknij krawędź, aby odczytać jej długość."
        )
        self.update()

    def clear_measurement(self) -> None:
        self.selected_edge = None
        self.measurement_vertices.clear()
        self.measurement_points.clear()
        self.measurement_changed.emit("Pomiar wyczyszczony.")
        self.update()

    def set_top_view(self) -> None:
        self.yaw = self.pitch = self.roll = 0.0
        self.fit_model()

    def set_isometric_view(self) -> None:
        self.yaw, self.pitch, self.roll = -35.0, 25.0, 0.0
        self.fit_model()

    def set_standard_view(self, view_name: str) -> None:
        """Snap the camera to a face or corner selected on the view cube."""
        orientations = {
            "top": (0.0, 0.0, 0.0),
            "front": (0.0, -90.0, 0.0),
            "right": (-90.0, 0.0, 0.0),
            "iso_ne": (-35.0, 25.0, 0.0),
            "iso_nw": (35.0, 25.0, 0.0),
            "iso_se": (-145.0, -25.0, 0.0),
            "iso_sw": (145.0, -25.0, 0.0),
        }
        orientation = orientations.get(str(view_name))
        if orientation is None:
            return
        self.yaw, self.pitch, self.roll = orientation
        self.fit_model()

    def rotate_axis(self, axis: str, degrees: float = 90.0) -> None:
        if axis == "x":
            self.pitch = (self.pitch + degrees) % 360.0
        elif axis == "y":
            self.yaw = (self.yaw + degrees) % 360.0
        else:
            self.roll = (self.roll + degrees) % 360.0
        self._update_projection()
        self.update()

    @staticmethod
    def _rotate(point: tuple[float, float, float], yaw: float, pitch: float, roll: float) -> tuple[float, float, float]:
        x, y, z = point
        px, py, pz = map(math.radians, (pitch, yaw, roll))
        # X (pitch)
        y, z = y * math.cos(px) - z * math.sin(px), y * math.sin(px) + z * math.cos(px)
        # Y (yaw)
        x, z = x * math.cos(py) + z * math.sin(py), -x * math.sin(py) + z * math.cos(py)
        # Z (roll)
        x, y = x * math.cos(pz) - y * math.sin(pz), x * math.sin(pz) + y * math.cos(pz)
        return x, y, z

    def _rotated_vertices(self) -> list[tuple[float, float, float]]:
        if self.model is None or not self.model.vertices:
            return []
        minimum, maximum = self.model.bounds
        center = tuple((minimum[axis] + maximum[axis]) / 2.0 for axis in range(3))
        return [
            self._rotate(
                (point[0] - center[0], point[1] - center[1], point[2] - center[2]),
                self.yaw,
                self.pitch,
                self.roll,
            )
            for point in self.model.vertices
        ]

    def _update_projection(self) -> None:
        rotated = self._rotated_vertices()
        if not rotated:
            self._projected, self._depths = [], []
            return
        scale = self._fit_scale * self.zoom
        center = QPointF(self.width() / 2.0, self.height() / 2.0) + self.pan
        self._projected = [QPointF(center.x() + x * scale, center.y() - y * scale) for x, y, _z in rotated]
        self._depths = [z for _x, _y, z in rotated]
        self.view_changed.emit(f"X {self.pitch:.0f}° · Y {self.yaw:.0f}° · Z {self.roll:.0f}° · zoom {self.zoom * 100:.0f}%")

    def fit_model(self) -> None:
        rotated = self._rotated_vertices()
        if not rotated:
            self._fit_scale = 1.0
            self._projected = []
            self.update()
            return
        min_x, max_x = min(point[0] for point in rotated), max(point[0] for point in rotated)
        min_y, max_y = min(point[1] for point in rotated), max(point[1] for point in rotated)
        available_w, available_h = max(80.0, self.width() - 70.0), max(80.0, self.height() - 70.0)
        self._fit_scale = min(
            available_w / max(max_x - min_x, 1e-6),
            available_h / max(max_y - min_y, 1e-6),
        )
        self.zoom = 1.0
        self.pan = QPointF()
        self._update_projection()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._update_projection()
        super().resizeEvent(event)

    def _selected_group(self) -> set[int]:
        if self.model is None or self.selected_edge is None:
            return set()
        selected = self.model.edges[self.selected_edge]
        if not selected.source_id:
            return {self.selected_edge}
        return {
            index for index, edge in enumerate(self.model.edges)
            if edge.source_id == selected.source_id
        }

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#07111f"))
        if self.model is None:
            painter.setPen(QColor("#8fa8c7"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Otwórz plik DXF, STEP lub STL")
            return
        self._update_projection()

        if self.model.faces and len(self.model.faces) <= 30000:
            ordered_faces = sorted(
                self.model.faces,
                key=lambda face: sum(self._depths[index] for index in face) / 3.0,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            for face in ordered_faces:
                polygon = QPolygonF([self._projected[index] for index in face])
                depth = sum(self._depths[index] for index in face) / 3.0
                alpha = 34 if depth < 0 else 55
                painter.setBrush(QColor(54, 137, 213, alpha))
                painter.drawPolygon(polygon)

        selected_group = self._selected_group()
        regular = QPen(QColor("#69bff4"), 1.15)
        regular.setCosmetic(True)
        selected_pen = QPen(QColor("#ffd166"), 3.0)
        selected_pen.setCosmetic(True)
        # Draw rear edges first so the selected geometry remains readable.
        model = self.model
        assert model is not None
        order = sorted(
            range(len(model.edges)),
            key=lambda index: (self._depths[model.edges[index].start] + self._depths[model.edges[index].end]) / 2.0,
        )
        for index in order:
            edge = self.model.edges[index]
            painter.setPen(selected_pen if index in selected_group else regular)
            painter.drawLine(self._projected[edge.start], self._projected[edge.end])

        marker_pen = QPen(QColor("#ff7b72"), 2.0)
        painter.setPen(marker_pen)
        painter.setBrush(QColor("#ff7b72"))
        projected = [self._project_point(point) for point in self.measurement_points]
        for point in projected:
            painter.drawEllipse(point, 5.0, 5.0)
        if len(projected) == 2:
            painter.setPen(QPen(QColor("#ff7b72"), 2.0, Qt.PenStyle.DashLine))
            painter.drawLine(*projected)
        if self.two_point_mode and self._snap_position is not None:
            painter.setPen(QPen(QColor("#ffd166"), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self._snap_position, 7, 7)

        if self.selected_edge is not None:
            edge = self.model.edges[self.selected_edge]
            first, second = self._projected[edge.start], self._projected[edge.end]
            midpoint = QPointF((first.x() + second.x()) / 2.0, (first.y() + second.y()) / 2.0)
            length = self._group_length(self.selected_edge)
            text = f"R {edge.radius:.3f} mm" if edge.radius is not None else f"{length:.3f} mm"
            box = QRectF(midpoint.x() + 8, midpoint.y() - 26, 128, 24)
            painter.setPen(QPen(QColor("#ffd166"), 1.0))
            painter.setBrush(QColor(7, 17, 31, 230))
            painter.drawRoundedRect(box, 5, 5)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _group_length(self, edge_index: int) -> float:
        assert self.model is not None
        edge = self.model.edges[edge_index]
        if not edge.source_id:
            return self.model.edge_length(edge_index)
        return sum(
            self.model.edge_length(index)
            for index, candidate in enumerate(self.model.edges)
            if candidate.source_id == edge.source_id
        )

    def _nearest_edge(self, position: QPointF, tolerance: float = 11.0) -> int | None:
        if self.model is None:
            return None
        best: tuple[float, int] | None = None
        for index, edge in enumerate(self.model.edges):
            first, second = self._projected[edge.start], self._projected[edge.end]
            if position.x() < min(first.x(), second.x()) - tolerance or position.x() > max(first.x(), second.x()) + tolerance:
                continue
            if position.y() < min(first.y(), second.y()) - tolerance or position.y() > max(first.y(), second.y()) + tolerance:
                continue
            distance = _distance_to_segment(position, first, second)
            if distance <= tolerance and (best is None or distance < best[0]):
                best = distance, index
        return best[1] if best else None

    def _nearest_vertex(self, position: QPointF, tolerance: float = 13.0) -> int | None:
        best: tuple[float, int] | None = None
        for index, point in enumerate(self._projected):
            if index not in getattr(self, "_corner_indices", range(len(self._projected))):
                continue
            distance = math.hypot(point.x() - position.x(), point.y() - position.y())
            if distance <= tolerance and (best is None or distance < best[0]):
                best = distance, index
        return best[1] if best else None

    def _project_point(self, point) -> QPointF:
        assert self.model is not None
        low, high = self.model.bounds
        x, y, _ = self._rotate(tuple(point[i] - (low[i]+high[i])/2 for i in range(3)), self.yaw, self.pitch, self.roll)
        scale = self._fit_scale * self.zoom
        return QPointF(self.width()/2 + self.pan.x() + x*scale, self.height()/2 + self.pan.y() - y*scale)

    def _select_at(self, position: QPointF) -> None:
        if self.model is None:
            return
        if self.two_point_mode:
            vertex = self._nearest_vertex(position)
            point = self.model.vertices[vertex] if vertex is not None else None
            if point is None:
                edge_index = self._nearest_edge(position)
                if edge_index is not None:
                    edge = self.model.edges[edge_index]
                    a, b = self._projected[edge.start], self._projected[edge.end]
                    dx, dy = b.x()-a.x(), b.y()-a.y()
                    t = max(0., min(1., ((position.x()-a.x())*dx + (position.y()-a.y())*dy) / max(dx*dx+dy*dy, 1e-12)))
                    first, second = self.model.vertices[edge.start], self.model.vertices[edge.end]
                    point = (first[0]+t*(second[0]-first[0]), first[1]+t*(second[1]-first[1]), first[2]+t*(second[2]-first[2]))
                    if edge.radius is not None and edge.center is not None:
                        c = edge.center
                        factor = edge.radius / max(math.dist(point, c), 1e-12)
                        point = (c[0]+(point[0]-c[0])*factor, c[1]+(point[1]-c[1])*factor, c[2]+(point[2]-c[2])*factor)
            if point is None:
                self.measurement_changed.emit("Kliknij bliżej narożnika lub krawędzi.")
                return
            if len(self.measurement_points) >= 2:
                self.measurement_points.clear()
                self.measurement_vertices.clear()
            self.measurement_points.append(point)
            if vertex is not None:
                self.measurement_vertices.append(vertex)
            if len(self.measurement_points) == 1:
                self.measurement_changed.emit("Pierwszy punkt wybrany — kliknij drugi.")
            else:
                distance = math.dist(*self.measurement_points)
                self.measurement_changed.emit(f"Odległość dwóch punktów: {distance:.3f} mm")
        else:
            self.selected_edge = self._nearest_edge(position)
            if self.selected_edge is None:
                self.measurement_changed.emit("Nie wskazano krawędzi.")
            else:
                edge = self.model.edges[self.selected_edge]
                qualifier = " (odległość końców krzywej)" if edge.approximate and self.model.source_format == "STEP" else ""
                self.measurement_changed.emit(
                    f"{edge.kind}: {self._group_length(self.selected_edge):.3f} mm{qualifier}"
                    + (f" · R {edge.radius:.3f} mm" if edge.radius is not None else "")
                )
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._pressed_at = event.position().toPoint()
        self._last_mouse = self._pressed_at
        self._dragging = False
        self._pan_drag = event.button() == Qt.MouseButton.MiddleButton
        self._rotate_drag = event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton}
        self.setFocus()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._last_mouse is None or not event.buttons():
            if self.two_point_mode and self.model is not None:
                vertex = self._nearest_vertex(event.position())
                edge_index = self._nearest_edge(event.position())
                self._snap_position = self._projected[vertex] if vertex is not None else None
                if vertex is None and edge_index is not None:
                    edge = self.model.edges[edge_index]
                    a, b = self._projected[edge.start], self._projected[edge.end]
                    dx, dy = b.x()-a.x(), b.y()-a.y()
                    t = max(0., min(1., ((event.position().x()-a.x())*dx + (event.position().y()-a.y())*dy) / max(dx*dx+dy*dy, 1e-12)))
                    self._snap_position = QPointF(a.x()+t*dx, a.y()+t*dy)
                self.update()
            return
        current = event.position().toPoint()
        delta = current - self._last_mouse
        self._last_mouse = current
        if self._pressed_at is not None and (current - self._pressed_at).manhattanLength() > 4:
            self._dragging = True
        if self._pan_drag:
            self.pan += QPointF(delta)
        elif self._rotate_drag and self._dragging:
            self.yaw += delta.x() * 0.55
            self.pitch += delta.y() * 0.55
        self._update_projection()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._dragging:
            self._select_at(event.position())
        self._pressed_at = self._last_mouse = None
        self._pan_drag = False
        self._rotate_drag = False
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.fit_model()
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.RightButton:
            steps = event.angleDelta().y() / 120.0
            self.roll = (self.roll + steps * 10.0) % 360.0
            self._update_projection()
            self.update()
            event.accept()
            return
        self.zoom = max(0.05, min(80.0, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        self._update_projection()
        self.update()
        event.accept()


class CadViewCube(QWidget):
    """Compact clickable orientation cube styled like the SIEKACZ toolbar."""

    view_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cadViewCube")
        self.setFixedSize(82, 64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Kliknij ścianę lub narożnik kostki, aby ustawić widok modelu.")
        self._hovered = ""
        self.setMouseTracking(True)

    def _geometry(self) -> tuple[dict[str, QPolygonF], dict[str, QPointF]]:
        top = QPolygonF([QPointF(17, 18), QPointF(41, 6), QPointF(65, 18), QPointF(41, 30)])
        front = QPolygonF([QPointF(17, 18), QPointF(41, 30), QPointF(41, 56), QPointF(17, 44)])
        right = QPolygonF([QPointF(41, 30), QPointF(65, 18), QPointF(65, 44), QPointF(41, 56)])
        corners = {
            "iso_nw": QPointF(17, 18),
            "iso_ne": QPointF(65, 18),
            "iso_sw": QPointF(17, 44),
            "iso_se": QPointF(65, 44),
        }
        return {"top": top, "front": front, "right": right}, corners

    def _region_at(self, position: QPointF) -> str:
        faces, corners = self._geometry()
        for name, center in corners.items():
            if math.hypot(position.x() - center.x(), position.y() - center.y()) <= 7.0:
                return name
        for name, polygon in faces.items():
            if polygon.containsPoint(position, Qt.FillRule.OddEvenFill):
                return name
        return ""

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#365273"), 1.0))
        faces, corners = self._geometry()
        colors = {"top": "#263d5d", "front": "#172c47", "right": "#1d3554"}
        labels = {"top": "G", "front": "P", "right": "B"}
        for name, polygon in faces.items():
            painter.setBrush(QColor("#2f80ff") if self._hovered == name else QColor(colors[name]))
            painter.drawPolygon(polygon)
            painter.setPen(QColor("#f4f8ff"))
            painter.drawText(polygon.boundingRect(), Qt.AlignmentFlag.AlignCenter, labels[name])
            painter.setPen(QPen(QColor("#365273"), 1.0))
        for name, center in corners.items():
            painter.setBrush(QColor("#73b3ff") if self._hovered == name else QColor("#8aa2bf"))
            painter.setPen(QPen(QColor("#0a1422"), 1.0))
            painter.drawEllipse(center, 3.5, 3.5)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        hovered = self._region_at(event.position())
        if hovered != self._hovered:
            self._hovered = hovered
            self.update()
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = ""
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            region = self._region_at(event.position())
            if region:
                self.view_selected.emit(region)
        event.accept()


class CadInspectionDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, initial_path: str | Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SIEKACZ CAD — podgląd i pomiary DXF / STEP / STL")
        self.resize(1320, 860)
        self.setMinimumSize(900, 620)
        self.canvas = CadInspectionCanvas(self)
        self.model: CadInspectionModel | None = None

        self.file_label = QLabel("Nie otwarto pliku")
        self.file_label.setWordWrap(True)
        self.format_label = QLabel("Format: —")
        self.dimension_label = QLabel("Gabaryt: —")
        self.count_label = QLabel("Geometria: —")
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #f1c66a;")
        self.measurement_label = QLabel("Kliknij krawędź, aby odczytać jej długość.")
        self.measurement_label.setWordWrap(True)
        self.measurement_label.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffd166;")
        self.view_label = QLabel("")
        self.view_label.setObjectName("summaryLabel")
        self.canvas.measurement_changed.connect(self.measurement_label.setText)
        self.canvas.view_changed.connect(self.view_label.setText)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addWidget(self._toolbar())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._information_panel())
        splitter.addWidget(self.canvas)
        splitter.setSizes([285, 1000])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        root.addLayout(self._footer())
        if initial_path:
            self.open_file(initial_path)

    @staticmethod
    def _button(text: str, callback) -> QToolButton:
        button = QToolButton()
        button.setObjectName("smallButton")
        button.setText(text)
        button.clicked.connect(callback)
        return button

    def isolate_contour(self) -> None:
        """Separate a connected detail from a technical drawing and its title block."""
        model = self.canvas.model
        selected = self.canvas.selected_edge
        if model is None or selected is None:
            self.measurement_label.setText("Najpierw kliknij bok konturu, który chcesz wyodrębnić.")
            return
        adjacency: dict[int, set[int]] = {}
        for edge in model.edges:
            adjacency.setdefault(edge.start, set()).add(edge.end)
            adjacency.setdefault(edge.end, set()).add(edge.start)
        found = {model.edges[selected].start}
        todo = list(found)
        while todo:
            for vertex in adjacency.get(todo.pop(), ()):
                if vertex not in found:
                    found.add(vertex)
                    todo.append(vertex)
        low = tuple(min(model.vertices[i][axis] for i in found) for axis in range(3))
        high = tuple(max(model.vertices[i][axis] for i in found) for axis in range(3))
        # Include holes and other internal geometry inside the chosen outline.
        edges = [edge for edge in model.edges if all(all(low[a]-1e-7 <= model.vertices[i][a] <= high[a]+1e-7 for a in range(3)) for i in (edge.start, edge.end))]
        from dataclasses import replace
        indices = sorted({i for edge in edges for i in (edge.start, edge.end)})
        mapping = {old: new for new, old in enumerate(indices)}
        isolated = CadInspectionModel(model.source_path, model.source_format,
            [model.vertices[i] for i in indices],
            [replace(edge, start=mapping[edge.start], end=mapping[edge.end]) for edge in edges],
            source_units=model.source_units, warnings=model.warnings)
        self.model = isolated
        self.canvas.set_model(isolated)
        width, height, depth = isolated.dimensions
        self.dimension_label.setText(f"Kontur: {width:.3f} × {height:.3f} × {depth:.3f} mm")
        self.count_label.setText(f"Geometria: {len(isolated.vertices)} punktów · {len(isolated.edges)} krawędzi")
        self.measurement_label.setText("Wyodrębniono kontur z geometrią wewnętrzną. Otwórz plik ponownie, aby przywrócić cały rysunek.")

    def add_contour_to_parts(self) -> None:
        model = self.canvas.model
        from app.simple_window import SimpleCutWindow
        parent = self.parent()
        if model is None or not isinstance(parent, SimpleCutWindow):
            return
        width, height, depth = model.dimensions
        if width <= 0.01 or height <= 0.01 or depth > 0.01:
            self.measurement_label.setText("Do rozkroju płyty wybierz płaski kontur DXF w płaszczyźnie XY.")
            return
        from import_export.dxf_io import inspection_contour_notes
        from app.simple_window import PART_MATERIAL_COLUMN, PART_NOTES_ROLE, PART_LABEL_ROLE
        material, thickness = parent._active_part_context()
        parent.add_part_row([thickness, width, height, 1, material])
        item = parent.parts.item(parent.parts.rowCount()-1, PART_MATERIAL_COLUMN)
        if item is not None:
            item.setData(PART_NOTES_ROLE, inspection_contour_notes(model))
            item.setData(PART_LABEL_ROLE, model.source_path.stem)
        parent._record_parts_state()
        self.measurement_label.setText(f"Dodano formatkę {width:.3f} × {height:.3f} mm z konturem.")

    def _toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        open_button = QPushButton("Otwórz DXF / STEP / STL…")
        open_button.setObjectName("primaryButton")
        open_button.clicked.connect(self.open_dialog)
        layout.addWidget(open_button)
        layout.addWidget(self._button("Dopasuj", self.canvas.fit_model))
        layout.addSpacing(8)
        view_caption = QLabel("WIDOK")
        view_caption.setObjectName("compactLabel")
        layout.addWidget(view_caption)
        self.view_cube = CadViewCube(self)
        self.view_cube.view_selected.connect(self.canvas.set_standard_view)
        layout.addWidget(self.view_cube)
        layout.addStretch(1)
        self.two_point = QCheckBox("Pomiar dwóch punktów")
        self.two_point.toggled.connect(self.canvas.set_two_point_mode)
        layout.addWidget(self.two_point)
        layout.addWidget(self._button("Wyczyść pomiar", self.canvas.clear_measurement))
        return bar

    def _information_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(250)
        panel.setMaximumWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 8, 12, 8)
        title = QLabel("MODEL")
        title.setStyleSheet("font-weight: 800; color: #8ec5ff;")
        layout.addWidget(title)
        layout.addWidget(self.file_label)
        layout.addSpacing(8)
        layout.addWidget(self.format_label)
        layout.addWidget(self.dimension_label)
        layout.addWidget(self.count_label)
        layout.addSpacing(14)
        measure_title = QLabel("POMIAR")
        measure_title.setStyleSheet("font-weight: 800; color: #8ec5ff;")
        layout.addWidget(measure_title)
        layout.addWidget(self.measurement_label)
        layout.addSpacing(12)
        layout.addWidget(self.warning_label)
        layout.addWidget(self._button("Wyodrębnij wskazany kontur", self.isolate_contour))
        layout.addWidget(self._button("Dodaj kontur jako formatkę", self.add_contour_to_parts))
        layout.addStretch(1)
        return panel

    def _footer(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        hint = QLabel(
            "Klik: zmierz · LPM/PPM przeciągnij: obrót · środkowy: przesuń · rolka: zoom · PPM + rolka: obrót osi · dwuklik: dopasuj"
        )
        hint.setObjectName("summaryLabel")
        layout.addWidget(hint, 1)
        layout.addWidget(self.view_label)
        close = QPushButton("Zamknij")
        close.setObjectName("smallButton")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        return layout

    def open_dialog(self) -> None:
        last_dir = str(repositories.get_setting("last_cad_inspection_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Otwórz model CAD",
            last_dir,
            "Modele CAD (*.dxf *.step *.stp *.stl);;DXF (*.dxf);;STEP (*.step *.stp);;STL (*.stl)",
        )
        if path:
            self.open_file(path)

    def open_file(self, path: str | Path) -> None:
        try:
            model = load_cad_inspection(path)
        except CadInspectionError as exc:
            QMessageBox.warning(self, "Podgląd CAD", str(exc))
            return
        self.model = model
        repositories.set_setting("last_cad_inspection_dir", str(model.source_path.parent))
        self.canvas.set_model(model)
        width, height, depth = model.dimensions
        self.file_label.setText(model.source_path.name)
        self.file_label.setToolTip(str(model.source_path))
        self.format_label.setText(f"Format: {model.source_format} · jednostki źródła: {model.source_units}")
        self.dimension_label.setText(f"Gabaryt: {width:.3f} × {height:.3f} × {depth:.3f} mm")
        self.count_label.setText(
            f"Geometria: {len(model.vertices)} punktów · {len(model.edges)} krawędzi · {len(model.faces)} trójkątów"
        )
        self.warning_label.setText("\n".join(f"• {warning}" for warning in model.warnings))
        self.measurement_label.setText("Kliknij konkretną krawędź albo włącz pomiar dwóch punktów.")
        self.setWindowTitle(f"SIEKACZ CAD — {model.source_path.name}")

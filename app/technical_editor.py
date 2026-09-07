from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QLineF, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cad.commands import (
    AddConstraintCommand,
    AddEntitiesCommand,
    AddEntityCommand,
    AddPatternCommand,
    AddParameterCommand,
    ApplyTopologyEditCommand,
    CommandStack,
    EditLinearPatternCommand,
    EditParameterCommand,
    RemoveConstraintsCommand,
    RemoveEntitiesCommand,
    RemovePatternCommand,
    RemoveParameterCommand,
    ReplaceConstraintCommand,
    ReplaceEntityCommand,
    RepairProfileCommand,
    SetConstraintEnabledCommand,
)
from cad.auto_constraints import (
    build_auto_constraints,
    build_bspline_auto_constraints,
    build_ellipse_auto_constraints,
    build_elliptical_arc_auto_constraints,
    build_polyline_auto_constraints,
    suggestion_label,
)
from cad.editing import EditableCurve, distance_to_curve, extend_line, split_curve, trim_curve
from cad.constraints import (
    ConstraintStatus,
    ConstraintType,
    GeometryReference,
    SketchConstraint,
    SketchSolveStatus,
    constraint_label,
)
from cad.io import CadIoError, load_document, save_document
from cad.model import (
    ArcEntity,
    BSplineEntity,
    CadLayer,
    CadDocument,
    CadValidationError,
    CircleEntity,
    EllipseEntity,
    EllipticalArcEntity,
    LineEntity,
    Point2D,
    PointEntity,
    PolylineEntity,
    RectangleEntity,
    RegularPolygonEntity,
    SketchEntity,
    SlotEntity,
    arc_from_three_points,
    ellipse_from_three_points,
    elliptical_arc_from_five_points,
    line_from_length_angle,
    rectangle_from_center,
    regular_polygon,
    slot_from_three_points,
)
from cad.patterns import create_linear_circle_pattern
from cad.parameter_data import CadParameter
from cad.parameter_data import validate_parameter_name
from cad.parameter_engine import bind_constraint_expression, bind_expression, make_parameter
from cad.profiles import (
    ProfileIssue,
    ProfileIssueType,
    ProfileReport,
    ProfileTolerance,
    analyze_profile,
    build_repair_plan,
)
from cad.selection import SelectionModel
from cad.snapping import SnapCandidate, SnapEngine, SnapKind, SnapResult
from cad.solver import fixed_constraint
from cad.units import parse_length


DRAWING_ROLE = 0
ENTITY_ID_ROLE = 1
ANNOTATION_ROLE = 2


class LayerManagerDialog(QDialog):
    def __init__(self, layers: dict[str, CadLayer], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Warstwy CAD / DXF")
        self.resize(720, 420)
        self._layers = dict(layers)
        self.table = QTableWidget(len(layers), 6)
        self.table.setHorizontalHeaderLabels(("Widoczna", "Nazwa", "Kolor ACI", "RGB", "Typ linii", "Blokada"))
        for row, layer in enumerate(layers.values()):
            visible = QTableWidgetItem()
            visible.setCheckState(Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
            locked = QTableWidgetItem()
            locked.setCheckState(Qt.CheckState.Checked if layer.locked else Qt.CheckState.Unchecked)
            name = QTableWidgetItem(layer.name)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, visible)
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QTableWidgetItem(str(layer.color)))
            self.table.setItem(row, 3, QTableWidgetItem("" if layer.true_color is None else f"#{layer.true_color:06X}"))
            self.table.setItem(row, 4, QTableWidgetItem(layer.linetype))
            self.table.setItem(row, 5, locked)
        self.table.resizeColumnsToContents()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Widoczność i styl warstw są zapisywane w projekcie .siekcad oraz zachowywane przy eksporcie DXF."))
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

    def values(self) -> dict[str, CadLayer]:
        result: dict[str, CadLayer] = {}
        for row in range(self.table.rowCount()):
            cells = [self.table.item(row, col) for col in range(6)]
            if any(cell is None for cell in cells):
                raise ValueError("Niekompletny wiersz warstwy CAD.")
            name, color, rgb, linetype = (self.table.item(row, col) for col in (1, 2, 3, 4))
            visible, locked = self.table.item(row, 0), self.table.item(row, 5)
            assert name is not None and color is not None and rgb is not None and linetype is not None
            assert visible is not None and locked is not None
            original = self._layers[name.text()]
            rgb_text = rgb.text().strip().lstrip("#")
            true_color = int(rgb_text, 16) if rgb_text else None
            layer = CadLayer(
                original.name,
                int(color.text()),
                true_color,
                linetype.text(),
                visible.checkState() == Qt.CheckState.Checked,
                locked.checkState() == Qt.CheckState.Checked,
            )
            result[layer.name] = layer
        return result


class LengthExpressionEdit(QLineEdit):
    """Length editor accepting units, expressions and document parameters."""

    def __init__(self, value: float = 0.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("premiumInput")
        self.setMinimumWidth(150)
        self.setValue(value)
        self.setToolTip("Przykłady: 25, 2.5 cm, 100/2, plateWidth - 10 mm")

    def setValue(self, value: float) -> None:
        self.setText(f"{float(value):.6f}".rstrip("0").rstrip("."))

    def value(self, parameters: dict[str, float] | None = None) -> float:
        return parse_length(self.text(), parameters)


class LineSpecificationDialog(QDialog):
    def __init__(
        self,
        start: Point2D,
        length: float,
        angle: float,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjna linia")
        self.setObjectName("cadDimensionBubble")
        self.setModal(True)

        self.x_input = LengthExpressionEdit(start.x)
        self.y_input = LengthExpressionEdit(start.y)
        self.length_input = LengthExpressionEdit(max(0.001, length))
        self.angle_input = QDoubleSpinBox()
        self.angle_input.setObjectName("premiumInput")
        self.angle_input.setRange(-360.0, 360.0)
        self.angle_input.setDecimals(4)
        self.angle_input.setSuffix("°")
        self.angle_input.setValue(angle)

        form = QFormLayout()
        form.setContentsMargins(14, 14, 14, 4)
        form.addRow("X początku", self.x_input)
        form.addRow("Y początku", self.y_input)
        form.addRow("Długość", self.length_input)
        form.addRow("Kąt", self.angle_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowy wymiar", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, float, float]:
        start = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        length = self.length_input.value(self.parameters)
        if length <= 0:
            raise CadValidationError("Długość linii musi być dodatnia.")
        return start, length, self.angle_input.value()


class RectangleSpecificationDialog(QDialog):
    def __init__(
        self,
        origin: Point2D,
        width: float,
        height: float,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjny prostokąt")
        self.setModal(True)
        self.x_input = LengthExpressionEdit(origin.x)
        self.y_input = LengthExpressionEdit(origin.y)
        self.width_input = LengthExpressionEdit(max(0.001, width))
        self.height_input = LengthExpressionEdit(max(0.001, height))
        form = QFormLayout()
        form.setContentsMargins(14, 14, 14, 4)
        form.addRow("X lewego dołu", self.x_input)
        form.addRow("Y lewego dołu", self.y_input)
        form.addRow("Szerokość", self.width_input)
        form.addRow("Wysokość", self.height_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowy wymiar", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, float, float]:
        origin = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        width = self.width_input.value(self.parameters)
        height = self.height_input.value(self.parameters)
        if width <= 0 or height <= 0:
            raise CadValidationError("Szerokość i wysokość prostokąta muszą być dodatnie.")
        return origin, width, height


class PointSpecificationDialog(QDialog):
    def __init__(self, point: Point2D, *, parameters: dict[str, float] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjny punkt")
        self.x_input = LengthExpressionEdit(point.x)
        self.y_input = LengthExpressionEdit(point.y)
        form = QFormLayout()
        form.addRow("X", self.x_input)
        form.addRow("Y", self.y_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowy punkt", str(exc))
            return
        self.accept()

    def values(self) -> Point2D:
        return Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))


class CenterRectangleSpecificationDialog(QDialog):
    def __init__(
        self,
        center: Point2D,
        width: float,
        height: float,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Prostokąt od środka")
        self.x_input = LengthExpressionEdit(center.x)
        self.y_input = LengthExpressionEdit(center.y)
        self.width_input = LengthExpressionEdit(max(0.001, width))
        self.height_input = LengthExpressionEdit(max(0.001, height))
        form = QFormLayout()
        form.addRow("X środka", self.x_input)
        form.addRow("Y środka", self.y_input)
        form.addRow("Szerokość", self.width_input)
        form.addRow("Wysokość", self.height_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowy prostokąt", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, float, float]:
        center = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        width, height = self.width_input.value(self.parameters), self.height_input.value(self.parameters)
        rectangle_from_center(center, width, height)
        return center, width, height


class RegularPolygonSpecificationDialog(QDialog):
    def __init__(
        self,
        center: Point2D,
        radius: float,
        sides: int = 6,
        rotation_deg: float = 0.0,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Wielokąt foremny")
        self.x_input = LengthExpressionEdit(center.x)
        self.y_input = LengthExpressionEdit(center.y)
        self.radius_input = LengthExpressionEdit(max(0.001, radius))
        self.sides_input = QSpinBox()
        self.sides_input.setObjectName("premiumInput")
        self.sides_input.setRange(3, 1000)
        self.sides_input.setValue(sides)
        self.rotation_input = QDoubleSpinBox()
        self.rotation_input.setObjectName("premiumInput")
        self.rotation_input.setRange(-3600.0, 3600.0)
        self.rotation_input.setDecimals(4)
        self.rotation_input.setSuffix("°")
        self.rotation_input.setValue(rotation_deg)
        form = QFormLayout()
        form.addRow("X środka", self.x_input)
        form.addRow("Y środka", self.y_input)
        form.addRow("Promień opisany", self.radius_input)
        form.addRow("Liczba boków", self.sides_input)
        form.addRow("Obrót", self.rotation_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowy wielokąt", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, float, int, float]:
        center = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        radius = self.radius_input.value(self.parameters)
        sides, rotation = self.sides_input.value(), self.rotation_input.value()
        regular_polygon(center, radius, sides, rotation)
        return center, radius, sides, rotation


class SlotSpecificationDialog(QDialog):
    def __init__(
        self,
        axis_start: Point2D,
        axis_end: Point2D,
        width: float,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjna szczelina")
        self.x1_input, self.y1_input = LengthExpressionEdit(axis_start.x), LengthExpressionEdit(axis_start.y)
        self.x2_input, self.y2_input = LengthExpressionEdit(axis_end.x), LengthExpressionEdit(axis_end.y)
        self.width_input = LengthExpressionEdit(max(0.001, width))
        form = QFormLayout()
        form.addRow("X początku osi", self.x1_input)
        form.addRow("Y początku osi", self.y1_input)
        form.addRow("X końca osi", self.x2_input)
        form.addRow("Y końca osi", self.y2_input)
        form.addRow("Szerokość", self.width_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowa szczelina", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, Point2D, float]:
        start = Point2D(self.x1_input.value(self.parameters), self.y1_input.value(self.parameters))
        end = Point2D(self.x2_input.value(self.parameters), self.y2_input.value(self.parameters))
        width = self.width_input.value(self.parameters)
        SlotEntity(start, end, width / 2.0)
        return start, end, width


class CircleSpecificationDialog(QDialog):
    def __init__(self, center: Point2D, radius: float, *, parameters: dict[str, float] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjny okrąg")
        self.x_input = LengthExpressionEdit(center.x)
        self.y_input = LengthExpressionEdit(center.y)
        self.radius_input = LengthExpressionEdit(max(0.001, radius))
        form = QFormLayout()
        form.addRow("X środka", self.x_input)
        form.addRow("Y środka", self.y_input)
        form.addRow("Promień", self.radius_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowy wymiar", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, float]:
        center = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        radius = self.radius_input.value(self.parameters)
        if radius <= 0:
            raise CadValidationError("Promień musi być dodatni.")
        return center, radius


class ArcSpecificationDialog(QDialog):
    def __init__(
        self,
        center: Point2D,
        radius: float,
        start_angle: float,
        sweep_angle: float,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjny łuk kołowy")
        self.x_input = LengthExpressionEdit(center.x)
        self.y_input = LengthExpressionEdit(center.y)
        self.radius_input = LengthExpressionEdit(max(0.001, radius))
        self.start_input = QDoubleSpinBox()
        self.start_input.setObjectName("premiumInput")
        self.start_input.setRange(-3600.0, 3600.0)
        self.start_input.setDecimals(4)
        self.start_input.setSuffix("°")
        self.start_input.setValue(start_angle)
        self.sweep_input = QDoubleSpinBox()
        self.sweep_input.setObjectName("premiumInput")
        self.sweep_input.setRange(-360.0, 360.0)
        self.sweep_input.setDecimals(4)
        self.sweep_input.setSuffix("°")
        self.sweep_input.setValue(sweep_angle)
        form = QFormLayout()
        form.addRow("X środka", self.x_input)
        form.addRow("Y środka", self.y_input)
        form.addRow("Promień", self.radius_input)
        form.addRow("Kąt początkowy", self.start_input)
        form.addRow("Kąt rozwarcia", self.sweep_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowy łuk", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, float, float, float]:
        center = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        radius = self.radius_input.value(self.parameters)
        start, sweep = self.start_input.value(), self.sweep_input.value()
        ArcEntity(center, radius, start, sweep)
        return center, radius, start, sweep


class EllipseSpecificationDialog(QDialog):
    def __init__(
        self,
        center: Point2D,
        major_radius: float,
        minor_radius: float,
        rotation_deg: float,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjna elipsa")
        self.x_input, self.y_input = LengthExpressionEdit(center.x), LengthExpressionEdit(center.y)
        self.major_input = LengthExpressionEdit(max(0.001, major_radius))
        self.minor_input = LengthExpressionEdit(max(0.001, minor_radius))
        self.rotation_input = QDoubleSpinBox()
        self.rotation_input.setObjectName("premiumInput")
        self.rotation_input.setRange(-3600.0, 3600.0)
        self.rotation_input.setDecimals(4)
        self.rotation_input.setSuffix("°")
        self.rotation_input.setValue(rotation_deg)
        form = QFormLayout()
        form.addRow("X środka", self.x_input)
        form.addRow("Y środka", self.y_input)
        form.addRow("Promień główny", self.major_input)
        form.addRow("Promień pomocniczy", self.minor_input)
        form.addRow("Obrót osi głównej", self.rotation_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowa elipsa", str(exc))
            return
        self.accept()

    def values(self) -> tuple[Point2D, float, float, float]:
        center = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        major, minor, rotation = self.major_input.value(self.parameters), self.minor_input.value(self.parameters), self.rotation_input.value()
        EllipseEntity(center, major, minor, rotation)
        return center, major, minor, rotation


class EllipticalArcSpecificationDialog(EllipseSpecificationDialog):
    def __init__(
        self,
        center: Point2D,
        major_radius: float,
        minor_radius: float,
        rotation_deg: float,
        start_parameter_deg: float,
        sweep_parameter_deg: float,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(center, major_radius, minor_radius, rotation_deg, parameters=parameters, parent=parent)
        self.setWindowTitle("Precyzyjny łuk elipsy")
        self.start_input = QDoubleSpinBox()
        self.start_input.setObjectName("premiumInput")
        self.start_input.setRange(-3600.0, 3600.0)
        self.start_input.setDecimals(4)
        self.start_input.setSuffix("°")
        self.start_input.setValue(start_parameter_deg)
        self.sweep_input = QDoubleSpinBox()
        self.sweep_input.setObjectName("premiumInput")
        self.sweep_input.setRange(-360.0, 360.0)
        self.sweep_input.setDecimals(4)
        self.sweep_input.setSuffix("°")
        self.sweep_input.setValue(sweep_parameter_deg)
        container_layout = self.layout()
        first_item = container_layout.itemAt(0) if container_layout is not None else None
        form = first_item.layout() if first_item is not None else None
        if isinstance(form, QFormLayout):
            form.addRow("Parametr początkowy", self.start_input)
            form.addRow("Rozwarcie parametryczne", self.sweep_input)

    def arc_values(self) -> tuple[Point2D, float, float, float, float, float]:
        center = Point2D(self.x_input.value(self.parameters), self.y_input.value(self.parameters))
        major, minor, rotation = self.major_input.value(self.parameters), self.minor_input.value(self.parameters), self.rotation_input.value()
        start, sweep = self.start_input.value(), self.sweep_input.value()
        EllipticalArcEntity(center, major, minor, rotation, start, sweep)
        return center, major, minor, rotation, start, sweep


class PolylineSpecificationDialog(QDialog):
    def __init__(
        self,
        points: tuple[Point2D, ...],
        closed: bool = False,
        bulges: tuple[float, ...] = (),
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjna polilinia")
        self.setMinimumSize(430, min(650, 180 + len(points) * 34))
        self.table = QTableWidget(len(points), 3)
        self.table.setHorizontalHeaderLabels(["X", "Y", "Bulge do następnego"])
        for row, point in enumerate(points):
            self.table.setCellWidget(row, 0, LengthExpressionEdit(point.x))
            self.table.setCellWidget(row, 1, LengthExpressionEdit(point.y))
            bulge_input = QDoubleSpinBox()
            bulge_input.setObjectName("premiumInput")
            bulge_input.setRange(-1_000_000.0, 1_000_000.0)
            bulge_input.setDecimals(8)
            bulge_input.setValue(bulges[row] if row < len(bulges) else 0.0)
            bulge_input.setToolTip("0 = odcinek; tan(kąta łuku / 4), znak określa CW/CCW")
            self.table.setCellWidget(row, 2, bulge_input)
        self.closed_input = QCheckBox("Zamknięta")
        self.closed_input.setChecked(closed)
        hint = QLabel(
            "Każdy wiersz jest trwałym wierzchołkiem. Bulge=0 tworzy linię, wartość niezerowa dokładny łuk kołowy; "
            "dla otwartej polilinii bulge ostatniego wiersza jest pomijany."
        )
        hint.setWordWrap(True)
        hint.setObjectName("summaryLabel")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.table)
        layout.addWidget(self.closed_input)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowa polilinia", str(exc))
            return
        self.accept()

    def values(self) -> tuple[tuple[Point2D, ...], bool, tuple[float, ...]]:
        points = []
        row_bulges: list[float] = []
        for row in range(self.table.rowCount()):
            x_input = self.table.cellWidget(row, 0)
            y_input = self.table.cellWidget(row, 1)
            bulge_input = self.table.cellWidget(row, 2)
            if not isinstance(x_input, LengthExpressionEdit) or not isinstance(y_input, LengthExpressionEdit) or not isinstance(bulge_input, QDoubleSpinBox):
                raise CadValidationError("Brakuje pola współrzędnej polilinii.")
            points.append(Point2D(x_input.value(self.parameters), y_input.value(self.parameters)))
            row_bulges.append(bulge_input.value())
        closed = self.closed_input.isChecked()
        segment_count = len(points) if closed else len(points) - 1
        entity = PolylineEntity(tuple(points), closed, tuple(row_bulges[:segment_count]))
        return entity.points, entity.closed, entity.bulges


class BSplineSpecificationDialog(QDialog):
    def __init__(
        self,
        control_points: tuple[Point2D, ...],
        degree: int | None = None,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Precyzyjna krzywa B-spline")
        self.setMinimumSize(470, min(700, 230 + len(control_points) * 34))
        self.table = QTableWidget(len(control_points), 2)
        self.table.setHorizontalHeaderLabels(["X punktu kontrolnego", "Y punktu kontrolnego"])
        for row, point in enumerate(control_points):
            self.table.setCellWidget(row, 0, LengthExpressionEdit(point.x))
            self.table.setCellWidget(row, 1, LengthExpressionEdit(point.y))
        self.degree_input = QSpinBox()
        self.degree_input.setObjectName("premiumInput")
        self.degree_input.setRange(1, min(10, len(control_points) - 1))
        self.degree_input.setValue(min(degree if degree is not None else 3, len(control_points) - 1))
        form = QFormLayout()
        form.addRow("Stopień krzywej", self.degree_input)
        hint = QLabel(
            "Punkty są trwałym wielobokiem kontrolnym. Krzywa pozostaje natywnym B-spline/NURBS; "
            "renderer nie zamienia jej na geometrię modelu z odcinków."
        )
        hint.setWordWrap(True)
        hint.setObjectName("summaryLabel")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nieprawidłowa krzywa B-spline", str(exc))
            return
        self.accept()

    def values(self) -> tuple[tuple[Point2D, ...], int]:
        points: list[Point2D] = []
        for row in range(self.table.rowCount()):
            x_input = self.table.cellWidget(row, 0)
            y_input = self.table.cellWidget(row, 1)
            if not isinstance(x_input, LengthExpressionEdit) or not isinstance(y_input, LengthExpressionEdit):
                raise CadValidationError("Brakuje pola punktu kontrolnego B-spline.")
            points.append(Point2D(x_input.value(self.parameters), y_input.value(self.parameters)))
        entity = BSplineEntity(tuple(points), self.degree_input.value())
        return entity.control_points, entity.degree


class LinearPatternDialog(QDialog):
    def __init__(
        self,
        count: int = 4,
        spacing_x: float = 40.0,
        spacing_y: float = 0.0,
        *,
        parameters: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.parameters = parameters or {}
        self.setWindowTitle("Parametryczny szyk liniowy")
        self.count_input = QSpinBox()
        self.count_input.setObjectName("premiumInput")
        self.count_input.setRange(2, 1000)
        self.count_input.setValue(count)
        self.spacing_x_input = LengthExpressionEdit(spacing_x)
        self.spacing_y_input = LengthExpressionEdit(spacing_y)
        form = QFormLayout()
        form.addRow("Liczba elementów łącznie", self.count_input)
        form.addRow("Krok X", self.spacing_x_input)
        form.addRow("Krok Y", self.spacing_y_input)
        hint = QLabel("Element źródłowy jest pierwszym elementem szyku.")
        hint.setObjectName("summaryLabel")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.values()
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nieprawidłowy szyk", str(exc))
            return
        self.accept()

    def values(self) -> tuple[int, float, float]:
        spacing_x = self.spacing_x_input.value(self.parameters)
        spacing_y = self.spacing_y_input.value(self.parameters)
        if math.hypot(spacing_x, spacing_y) <= 1e-9:
            raise CadValidationError("Wektor kroku szyku nie może być zerowy.")
        return self.count_input.value(), spacing_x, spacing_y


class ProfileDiagnosticsDialog(QDialog):
    """Review profile problems before applying any geometry repair."""

    def __init__(
        self,
        report: ProfileReport,
        on_select,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.on_select = on_select
        self.setWindowTitle("Sprawdź i napraw szkic")
        self.setMinimumSize(620, 430)

        if report.is_valid_surface:
            summary_text = f"Profil poprawny: {len(report.loops)} zamknięte pętle."
            summary_color = "#76e0a6"
        else:
            summary_text = (
                f"Profil nie może utworzyć powierzchni · błędy: {report.error_count} · "
                f"otwarte końce: {report.open_endpoint_count} · pętle: {len(report.loops)}"
            )
            summary_color = "#ff657a"
        self.summary = QLabel(summary_text)
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"font-weight: 700; color: {summary_color};")

        self.issue_list = QListWidget()
        self.issue_list.setAlternatingRowColors(True)
        for index, issue in enumerate(report.issues):
            item = QListWidgetItem(self._issue_label(issue))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(issue.message)
            color = "#ff657a" if issue.severity.value == "error" else "#ffbd69"
            item.setForeground(QBrush(QColor(color)))
            self.issue_list.addItem(item)
        if not report.issues:
            item = QListWidgetItem("Nie znaleziono problemów profilu.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.issue_list.addItem(item)
        self.issue_list.currentItemChanged.connect(self._select_issue)

        self.close_gaps = QCheckBox("Dodaj więzy zbieżności dla małych szczelin")
        self.close_gaps.setChecked(any(issue.issue_type == ProfileIssueType.SMALL_GAP for issue in report.issues))
        self.remove_duplicates = QCheckBox("Usuń dokładne duplikaty")
        self.remove_duplicates.setChecked(any(issue.issue_type == ProfileIssueType.DUPLICATE for issue in report.issues))
        self.remove_micro = QCheckBox("Usuń mikroodcinki (operacja destrukcyjna)")
        self.remove_micro.setChecked(False)
        self.remove_micro.setToolTip("Mikroodcinki są usuwane tylko po jawnym zaznaczeniu tej opcji.")

        note = QLabel(
            "Kliknij problem, aby zaznaczyć jego geometrię. Naprawa nie zmienia niczego bez zatwierdzenia "
            "i zostanie zapisana jako jeden krok historii undo."
        )
        note.setObjectName("summaryLabel")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close)
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setText("Napraw zaznaczone")
        apply_button.setEnabled(any(issue.repairable for issue in report.issues))
        buttons.clicked.connect(
            lambda button: self.accept()
            if button == apply_button
            else self.reject()
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.issue_list, 1)
        layout.addWidget(self.close_gaps)
        layout.addWidget(self.remove_duplicates)
        layout.addWidget(self.remove_micro)
        layout.addWidget(note)
        layout.addWidget(buttons)

    @staticmethod
    def _issue_label(issue: ProfileIssue) -> str:
        labels = {
            ProfileIssueType.OPEN_ENDPOINT: "Otwarty koniec",
            ProfileIssueType.SMALL_GAP: "Mała szczelina",
            ProfileIssueType.DUPLICATE: "Duplikat",
            ProfileIssueType.MICRO_SEGMENT: "Mikroodcinek",
            ProfileIssueType.SELF_INTERSECTION: "Samoprzecięcie",
            ProfileIssueType.OVERLAP: "Nakładanie",
            ProfileIssueType.BRANCH_POINT: "Punkt rozgałęzienia",
        }
        suffix = " · można naprawić" if issue.repairable else ""
        return f"{labels[issue.issue_type]}: {issue.message}{suffix}"

    def _select_issue(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        index = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(index, int) and 0 <= index < len(self.report.issues):
            self.on_select(self.report.issues[index])

    def repair_options(self) -> tuple[bool, bool, bool]:
        return self.remove_duplicates.isChecked(), self.remove_micro.isChecked(), self.close_gaps.isChecked()


class ParameterEditorDialog(QDialog):
    def __init__(
        self,
        document: CadDocument,
        parameter: CadParameter | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.parameter = parameter
        self.setWindowTitle("Edytuj parametr" if parameter else "Nowy parametr")
        self.name_input = QLineEdit(parameter.name if parameter else "")
        self.name_input.setObjectName("premiumInput")
        self.expression_input = QLineEdit(parameter.expression if parameter else "10 mm")
        self.expression_input.setObjectName("premiumInput")
        self.expression_input.setPlaceholderText("np. holeDiameter * 2")
        names = [item.name for item in document.ordered_parameters()]
        completer = QCompleter(names, self.expression_input)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.expression_input.setCompleter(completer)
        self.scope_input = QComboBox()
        self.scope_input.addItem("Globalny — cały dokument", "")
        for sketch in document.sketches.values():
            self.scope_input.addItem(f"Lokalny — {sketch.label}", sketch.id)
        scope = parameter.scope_object_id if parameter else ""
        scope_index = self.scope_input.findData(scope)
        self.scope_input.setCurrentIndex(max(0, scope_index))
        self.scope_input.setEnabled(parameter is None)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setObjectName("summaryLabel")
        self.expression_input.textChanged.connect(self._update_preview)
        self.scope_input.currentIndexChanged.connect(self._update_preview)

        form = QFormLayout()
        form.addRow("Nazwa", self.name_input)
        form.addRow("Zakres", self.scope_input)
        form.addRow("Wyrażenie", self.expression_input)
        form.addRow("Podgląd", self.preview)
        hint = QLabel("Obsługiwane są mm, cm, m, cale, działania arytmetyczne i wcześniej zdefiniowane parametry.")
        hint.setWordWrap(True)
        hint.setObjectName("summaryLabel")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self._update_preview()

    def _update_preview(self, *_args) -> None:
        try:
            _bindings, value = bind_expression(
                self.document,
                self.expression_input.text(),
                str(self.scope_input.currentData() or ""),
                exclude_id=self.parameter.id if self.parameter else "",
            )
            self.preview.setText(f"Wynik: {value:g} mm")
            self.preview.setStyleSheet("color: #76e0a6;")
        except (CadValidationError, ValueError) as exc:
            self.preview.setText(str(exc))
            self.preview.setStyleSheet("color: #ff657a;")

    def _validate_and_accept(self) -> None:
        try:
            if self.parameter is None:
                make_parameter(self.document, *self.values())
            else:
                name = validate_parameter_name(self.name_input.text())
                if any(
                    item.id != self.parameter.id
                    and item.name == name
                    and item.scope_object_id == self.parameter.scope_object_id
                    for item in self.document.parameters.values()
                ):
                    raise CadValidationError(f"Parametr {name} już istnieje w tym zakresie.")
                bind_expression(
                    self.document,
                    self.expression_input.text(),
                    self.parameter.scope_object_id,
                    exclude_id=self.parameter.id,
                )
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nieprawidłowy parametr", str(exc))
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return self.name_input.text().strip(), self.expression_input.text().strip(), str(self.scope_input.currentData() or "")


class ParameterManagerDialog(QDialog):
    def __init__(self, document: CadDocument, command_stack: CommandStack, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.command_stack = command_stack
        self.setWindowTitle("Parametry dokumentu")
        self.setMinimumSize(760, 430)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Zakres", "Nazwa", "Wyrażenie", "Wartość"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _index: self._edit_selected())
        add = QPushButton("Dodaj")
        edit = QPushButton("Edytuj")
        remove = QPushButton("Usuń")
        close = QPushButton("Zamknij")
        add.clicked.connect(self._add_parameter)
        edit.clicked.connect(self._edit_selected)
        remove.clicked.connect(self._remove_selected)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(edit)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        self._refresh()

    def _refresh(self, selected_id: str = "") -> None:
        self.table.setRowCount(0)
        for parameter in self.document.ordered_parameters():
            row = self.table.rowCount()
            self.table.insertRow(row)
            scope = "Globalny" if not parameter.scope_object_id else self.document.sketches[parameter.scope_object_id].label
            values = (scope, parameter.name, parameter.expression, f"{parameter.value:g} mm")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, parameter.id)
                self.table.setItem(row, column, item)
            if parameter.id == selected_id:
                self.table.selectRow(row)
        self.table.resizeColumnsToContents()

    def selected_parameter_id(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _add_parameter(self) -> None:
        editor = ParameterEditorDialog(self.document, parent=self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            parameter = make_parameter(self.document, *editor.values())
            self.command_stack.execute(AddParameterCommand(parameter))
            self._refresh(parameter.id)
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nie można dodać parametru", str(exc))

    def _edit_selected(self) -> None:
        parameter_id = self.selected_parameter_id()
        parameter = self.document.parameters.get(parameter_id)
        if parameter is None:
            QMessageBox.information(self, "Parametry", "Wybierz parametr do edycji.")
            return
        editor = ParameterEditorDialog(self.document, parameter, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        name, expression, _scope = editor.values()
        try:
            self.command_stack.execute(EditParameterCommand(parameter.id, name, expression))
            self._refresh(parameter.id)
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nie można zmienić parametru", str(exc))

    def _remove_selected(self) -> None:
        parameter_id = self.selected_parameter_id()
        if not parameter_id:
            QMessageBox.information(self, "Parametry", "Wybierz parametr do usunięcia.")
            return
        try:
            self.command_stack.execute(RemoveParameterCommand(parameter_id))
            self._refresh()
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nie można usunąć parametru", str(exc))


class TechnicalCanvas(QGraphicsView):
    creation_requested = Signal(str, object, object, object, object)
    multi_creation_requested = Signal(str, object, object, bool)
    entity_edit_requested = Signal(str)
    topology_requested = Signal(str)
    status_changed = Signal(str)

    def __init__(
        self,
        document: CadDocument,
        command_stack: CommandStack,
        selection: SelectionModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.command_stack = command_stack
        self.selection_model = selection
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#07111f"))
        self.setMouseTracking(True)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        self.tool = "select"
        self.grid_size = 10.0
        self.snap_engine = SnapEngine(radius_pixels=14.0, grid_size=self.grid_size)
        self._start: Point2D | None = None
        self._preview: QGraphicsItem | None = None
        self._start_snap_candidate: SnapCandidate | None = None
        self._last_snap_result = SnapResult(Point2D(0, 0), None, ())
        self._last_cursor_model: Point2D | None = None
        self._snap_candidate_index = 0
        self._multi_points: list[Point2D] = []
        self._multi_candidates: list[SnapCandidate | None] = []
        self.auto_constraints_enabled = True
        self._panning = False
        self._pan_origin = QPoint()
        self._entity_items: dict[str, QGraphicsItem] = {}
        self._preselected_id: str | None = None
        self._selection_guard = False
        self._unsubscribe_commands = self.command_stack.subscribe(lambda _ids: self.sync_from_model())
        self._unsubscribe_selection = self.selection_model.subscribe(lambda _ids: self._sync_scene_selection())

        self.board_width = float(self.document.view_state.get("board_width", 1000.0))
        self.board_height = float(self.document.view_state.get("board_height", 2000.0))
        self.board_item = QGraphicsRectItem()
        self.board_item.setZValue(-10)
        self.board_item.setPen(QPen(QColor("#5689c9"), 1.6))
        self.board_item.setBrush(QBrush(QColor("#0d1b2e")))
        self.scene().addItem(self.board_item)
        self.snap_marker = QGraphicsEllipseItem(-5, -5, 10, 10)
        self.snap_marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.snap_marker.setPen(QPen(QColor("#ffcc66"), 1.6))
        self.snap_marker.setBrush(QBrush(QColor(255, 204, 102, 70)))
        self.snap_marker.setZValue(100)
        self.snap_marker.hide()
        self.scene().addItem(self.snap_marker)
        self.snap_candidate_markers: list[QGraphicsEllipseItem] = []
        for _index in range(12):
            marker = QGraphicsEllipseItem(-3.5, -3.5, 7, 7)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            marker.setPen(QPen(QColor("#8290a8"), 1.0))
            marker.setBrush(QBrush(QColor(130, 144, 168, 35)))
            marker.setZValue(98)
            marker.hide()
            self.scene().addItem(marker)
            self.snap_candidate_markers.append(marker)
        self.snap_glyph = QGraphicsSimpleTextItem()
        self.snap_glyph.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.snap_glyph.setBrush(QBrush(QColor("#ffcc66")))
        self.snap_glyph.setZValue(101)
        self.snap_glyph.hide()
        self.scene().addItem(self.snap_glyph)
        self.scene().selectionChanged.connect(self._scene_selection_changed)
        self.set_board_size(self.board_width, self.board_height)
        self.sync_from_model()

    @staticmethod
    def model_to_scene(point: Point2D) -> QPointF:
        return QPointF(point.x, -point.y)

    @staticmethod
    def scene_to_model(point: QPointF) -> Point2D:
        return Point2D(point.x(), -point.y())

    def bind(self, document: CadDocument, command_stack: CommandStack, selection: SelectionModel) -> None:
        self._unsubscribe_commands()
        self._unsubscribe_selection()
        self.document = document
        self.command_stack = command_stack
        self.selection_model = selection
        self._unsubscribe_commands = self.command_stack.subscribe(lambda _ids: self.sync_from_model())
        self._unsubscribe_selection = self.selection_model.subscribe(lambda _ids: self._sync_scene_selection())
        self.board_width = float(document.view_state.get("board_width", 1000.0))
        self.board_height = float(document.view_state.get("board_height", 2000.0))
        self.set_board_size(self.board_width, self.board_height)
        self.sync_from_model()

    def set_board_size(self, width: float, height: float) -> None:
        self.board_width = max(1.0, float(width))
        self.board_height = max(1.0, float(height))
        self.document.view_state["board_width"] = self.board_width
        self.document.view_state["board_height"] = self.board_height
        self.board_item.setRect(0.0, -self.board_height, self.board_width, self.board_height)
        margin = max(80.0, min(self.board_width, self.board_height) * 0.08)
        self.scene().setSceneRect(-margin, -self.board_height - margin, self.board_width + margin * 2, self.board_height + margin * 2)
        self.fit_board()

    def set_tool(self, tool: str) -> None:
        self.cancel_preview()
        self.tool = tool
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if tool == "select" else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor if tool == "select" else Qt.CursorShape.CrossCursor)

    def fit_board(self) -> None:
        self.fitInView(self.board_item.sceneBoundingRect().adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)

    def _pixels_per_unit(self) -> float:
        return max(abs(self.transform().m11()), 1e-9)

    def _snap_result(self, point: Point2D, *, preserve_candidate: bool = False) -> SnapResult:
        if not preserve_candidate and (
            self._last_cursor_model is None
            or self._last_cursor_model.distance_to(point) * self._pixels_per_unit() > 2.0
        ):
            self._snap_candidate_index = 0
        result = self.snap_engine.query(
            self.document.active_sketch,
            point,
            pixels_per_unit=self._pixels_per_unit(),
            candidate_index=self._snap_candidate_index,
            start=self._start,
            tool=self.tool,
        )
        self._last_cursor_model = point
        self._last_snap_result = result
        for marker, candidate in zip(self.snap_candidate_markers, result.candidates):
            marker.setPos(self.model_to_scene(candidate.point))
            marker.show()
        for marker in self.snap_candidate_markers[len(result.candidates):]:
            marker.hide()
        if result.active is None:
            self.snap_marker.hide()
            self.snap_glyph.hide()
            self.status_changed.emit(f"X {point.x:.3f} mm   Y {point.y:.3f} mm")
        else:
            self.snap_marker.setPos(self.model_to_scene(result.point))
            self.snap_marker.show()
            names = {
                SnapKind.ENDPOINT: "koniec",
                SnapKind.MIDPOINT: "środek odcinka",
                SnapKind.CENTER: "centrum",
                SnapKind.ORIGIN: "początek układu",
                SnapKind.GRID: "siatka",
                SnapKind.INTERSECTION: "przecięcie",
                SnapKind.NEAREST: "najbliższy punkt",
                SnapKind.AXIS_X: "oś X",
                SnapKind.AXIS_Y: "oś Y",
                SnapKind.EXTENSION: "przedłużenie",
                SnapKind.HORIZONTAL: "poziomo",
                SnapKind.VERTICAL: "pionowo",
                SnapKind.PARALLEL: "równolegle",
                SnapKind.PERPENDICULAR: "prostopadle",
                SnapKind.TANGENT: "stycznie",
                SnapKind.INCREMENTAL_ANGLE: f"kąt {result.active.sub_element}°",
            }
            glyphs = {
                SnapKind.ENDPOINT: "□",
                SnapKind.MIDPOINT: "△",
                SnapKind.CENTER: "○",
                SnapKind.ORIGIN: "⊕",
                SnapKind.INTERSECTION: "×",
                SnapKind.NEAREST: "◇",
                SnapKind.AXIS_X: "X",
                SnapKind.AXIS_Y: "Y",
                SnapKind.EXTENSION: "⋯",
                SnapKind.HORIZONTAL: "H",
                SnapKind.VERTICAL: "V",
                SnapKind.PARALLEL: "∥",
                SnapKind.PERPENDICULAR: "⊥",
                SnapKind.TANGENT: "T",
                SnapKind.INCREMENTAL_ANGLE: "∠",
                SnapKind.GRID: "+",
            }
            self.snap_glyph.setText(glyphs[result.active.kind])
            glyph_position = self.model_to_scene(result.point)
            self.snap_glyph.setPos(glyph_position.x() + 8, glyph_position.y() - 18)
            self.snap_glyph.show()
            index = result.candidates.index(result.active) + 1
            cycle = f" · kandydat {index}/{len(result.candidates)} · Tab przełącza" if len(result.candidates) > 1 else ""
            inferred = suggestion_label(result.active) if self.auto_constraints_enabled else ""
            auto = f" · AUTO-WIĘZ: {inferred}" if inferred else ""
            self.status_changed.emit(
                f"SNAP: {names[result.active.kind]}{cycle}{auto}   X {result.point.x:.3f} mm   Y {result.point.y:.3f} mm"
            )
        return result

    def _snap(self, point: QPointF) -> QPointF:
        return self.model_to_scene(self._snap_result(self.scene_to_model(point)).point)

    def _entity_color(self, entity: SketchEntity) -> str:
        style = entity.style
        layer = self.document.layers.get(entity.layer)
        true_color = style.true_color if style.true_color is not None else layer.true_color if layer else None
        if true_color is not None:
            return f"#{true_color:06x}"
        color_index = style.color if 1 <= style.color <= 255 else layer.color if layer else 7
        try:
            from ezdxf.colors import aci2rgb
            rgb = aci2rgb(color_index)
            return f"#{rgb.r:02x}{rgb.g:02x}{rgb.b:02x}"
        except Exception:
            return "#6bc5ff"

    def _drawing_pen(self, selected: bool = False, preselected: bool = False, construction: bool = False, conflicted: bool = False, base_color: str = "#6bc5ff", linetype: str = "BYLAYER") -> QPen:
        color = "#ffcf66" if selected else "#ffffff" if preselected else "#ff657a" if conflicted else base_color
        pen = QPen(QColor(color), 2.6 if selected or preselected else 1.8)
        pen.setCosmetic(True)
        if construction or linetype.upper() in {"DASHED", "HIDDEN", "CENTER", "DASHDOT"}:
            pen.setStyle(Qt.PenStyle.DashLine)
        return pen

    def _entity_pen(self, entity: SketchEntity, selected: bool = False, preselected: bool = False) -> QPen:
        layer = self.document.layers.get(entity.layer)
        linetype = entity.style.linetype
        if linetype.upper() == "BYLAYER" and layer is not None:
            linetype = layer.linetype
        return self._drawing_pen(
            selected, preselected, entity.construction, self._entity_has_conflict(entity.id),
            self._entity_color(entity), linetype,
        )

    def _entity_has_conflict(self, entity_id: str) -> bool:
        return any(
            constraint.status in {ConstraintStatus.CONFLICTING, ConstraintStatus.BROKEN_REFERENCE}
            and any(reference.entity_id == entity_id for reference in constraint.references)
            for constraint in self.document.active_sketch.ordered_constraints()
        )

    @staticmethod
    def _arc_path(entity: ArcEntity) -> QPainterPath:
        center = TechnicalCanvas.model_to_scene(entity.center)
        rect = QRectF(center.x() - entity.radius, center.y() - entity.radius, entity.radius * 2, entity.radius * 2)
        path = QPainterPath()
        path.arcMoveTo(rect, entity.start_angle_deg)
        path.arcTo(rect, entity.start_angle_deg, entity.sweep_angle_deg)
        return path

    @staticmethod
    def _slot_path(entity: SlotEntity) -> QPainterPath:
        first_line, second_line = entity.boundary_lines
        end_arc, start_arc = entity.boundary_arcs
        path = QPainterPath(TechnicalCanvas.model_to_scene(first_line.start))
        path.lineTo(TechnicalCanvas.model_to_scene(first_line.end))
        for index in range(1, 25):
            path.lineTo(TechnicalCanvas.model_to_scene(end_arc.point_at(index / 24.0)))
        path.lineTo(TechnicalCanvas.model_to_scene(second_line.end))
        for index in range(1, 25):
            path.lineTo(TechnicalCanvas.model_to_scene(start_arc.point_at(index / 24.0)))
        path.closeSubpath()
        return path

    @staticmethod
    def _ellipse_path(entity: EllipseEntity | EllipticalArcEntity) -> QPainterPath:
        if isinstance(entity, EllipseEntity):
            count = 128
            points = [entity.point_at_parameter(index * 360.0 / count) for index in range(count)]
            path = QPainterPath(TechnicalCanvas.model_to_scene(points[0]))
            for point in points[1:]:
                path.lineTo(TechnicalCanvas.model_to_scene(point))
            path.closeSubpath()
            return path
        count = max(16, int(math.ceil(abs(entity.sweep_parameter_deg) / 3.0)))
        path = QPainterPath(TechnicalCanvas.model_to_scene(entity.start))
        for index in range(1, count + 1):
            path.lineTo(TechnicalCanvas.model_to_scene(entity.point_at(index / count)))
        return path

    @staticmethod
    def _bspline_path(entity: BSplineEntity) -> QPainterPath:
        points = entity.diagnostic_points(max(64, len(entity.control_points) * 32))
        path = QPainterPath(TechnicalCanvas.model_to_scene(points[0]))
        for point in points[1:]:
            path.lineTo(TechnicalCanvas.model_to_scene(point))
        return path

    def _render_entity(self, entity: SketchEntity) -> QGraphicsItem:
        if isinstance(entity, PointEntity):
            item = QGraphicsEllipseItem(-4, -4, 8, 8)
            item.setPos(self.model_to_scene(entity.point))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            label_text = f"Punkt · X {entity.point.x:.2f} · Y {entity.point.y:.2f} mm"
            label_position = self.model_to_scene(entity.point) + QPointF(0, 8)
        elif isinstance(entity, LineEntity):
            start, end = self.model_to_scene(entity.start), self.model_to_scene(entity.end)
            item: QGraphicsItem = QGraphicsLineItem(QLineF(start, end))
            label_text = f"{entity.length:.2f} mm  |  {entity.angle_deg:.1f}°"
            label_position = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2 - 20)
        elif isinstance(entity, RectangleEntity):
            rect = QRectF(entity.origin.x, -(entity.origin.y + entity.height), entity.width, entity.height)
            item = QGraphicsRectItem(rect)
            label_text = f"{entity.width:.2f} × {entity.height:.2f} mm"
            label_position = QPointF(rect.center().x(), rect.bottom() + 8)
        elif isinstance(entity, CircleEntity):
            center = self.model_to_scene(entity.center)
            rect = QRectF(center.x() - entity.radius, center.y() - entity.radius, entity.radius * 2, entity.radius * 2)
            item = QGraphicsEllipseItem(rect)
            label_text = f"Ø {entity.radius * 2:.2f} mm"
            label_position = QPointF(rect.center().x(), rect.bottom() + 8)
        elif isinstance(entity, ArcEntity):
            item = QGraphicsPathItem(self._arc_path(entity))
            midpoint = self.model_to_scene(entity.midpoint)
            label_text = f"R {entity.radius:.2f} mm · {entity.sweep_angle_deg:.1f}°"
            label_position = QPointF(midpoint.x(), midpoint.y() - 20)
        elif isinstance(entity, EllipseEntity):
            item = QGraphicsPathItem(self._ellipse_path(entity))
            label_text = f"Elipsa · {entity.major_radius:.2f} × {entity.minor_radius:.2f} mm"
            label_position = self.model_to_scene(entity.center) + QPointF(0, 8)
        elif isinstance(entity, EllipticalArcEntity):
            item = QGraphicsPathItem(self._ellipse_path(entity))
            midpoint = self.model_to_scene(entity.midpoint)
            label_text = f"Łuk elipsy · {entity.sweep_parameter_deg:.1f}° · {entity.length:.2f} mm"
            label_position = QPointF(midpoint.x(), midpoint.y() - 20)
        elif isinstance(entity, BSplineEntity):
            item = QGraphicsPathItem(self._bspline_path(entity))
            left, bottom, right, top = entity.bounds()
            rational = " · NURBS" if entity.rational else ""
            label_text = f"B-spline · stopień {entity.degree} · {len(entity.control_points)} pkt{rational}"
            label_position = self.model_to_scene(Point2D((left + right) / 2, bottom)) + QPointF(0, 8)
        elif isinstance(entity, PolylineEntity):
            path = QPainterPath(self.model_to_scene(entity.points[0]))
            for segment in entity.segment_entities:
                if isinstance(segment, ArcEntity):
                    count = max(8, int(math.ceil(abs(segment.sweep_angle_deg) / 3.0)))
                    for index in range(1, count + 1):
                        path.lineTo(self.model_to_scene(segment.point_at(index / count)))
                else:
                    path.lineTo(self.model_to_scene(segment.end))
            item = QGraphicsPathItem(path)
            left, bottom, right, top = entity.bounds()
            arc_count = sum(isinstance(segment, ArcEntity) for segment in entity.segment_entities)
            label_text = f"Polilinia · {len(entity.points)} pkt · {arc_count} łuków · {entity.length:.2f} mm"
            label_position = self.model_to_scene(Point2D((left + right) / 2, bottom)) + QPointF(0, 8)
        elif isinstance(entity, RegularPolygonEntity):
            path = QPainterPath(self.model_to_scene(entity.points[0]))
            for point in entity.points[1:]:
                path.lineTo(self.model_to_scene(point))
            path.closeSubpath()
            item = QGraphicsPathItem(path)
            label_text = f"{entity.sides}-kąt foremny · R {entity.radius:.2f} mm"
            label_position = self.model_to_scene(entity.center) + QPointF(0, 8)
        elif isinstance(entity, SlotEntity):
            item = QGraphicsPathItem(self._slot_path(entity))
            label_text = f"Szczelina · {entity.axis_length:.2f} × {entity.width:.2f} mm"
            label_position = self.model_to_scene(entity.center) + QPointF(0, 8)
        else:  # pragma: no cover - closed domain union
            raise TypeError(type(entity))
        layer = self.document.layers.get(entity.layer)
        item.setPen(self._entity_pen(entity))  # type: ignore[attr-defined]
        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsPathItem)) and not isinstance(entity, (ArcEntity, EllipseEntity, EllipticalArcEntity, BSplineEntity, PolylineEntity, RegularPolygonEntity)):
            item.setBrush(QBrush(QColor(42, 118, 184, 24)))
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.setData(DRAWING_ROLE, "drawing")
        item.setData(ENTITY_ID_ROLE, entity.id)
        item.setVisible(entity.visible and (layer.visible if layer is not None else True))
        self.scene().addItem(item)
        label = QGraphicsSimpleTextItem(label_text, item)
        label.setData(DRAWING_ROLE, "annotation")
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        label.setBrush(QBrush(QColor("#d7ecff")))
        bounds = label.boundingRect()
        label.setPos(label_position.x() - bounds.width() / 2, label_position.y())
        label.setZValue(20)
        item.setData(ANNOTATION_ROLE, label)
        badges = []
        for constraint in self.document.active_sketch.ordered_constraints():
            if not any(reference.entity_id == entity.id for reference in constraint.references):
                continue
            short = {
                ConstraintType.HORIZONTAL: "H",
                ConstraintType.VERTICAL: "V",
                ConstraintType.COINCIDENT: "●",
                ConstraintType.FIX: "FIX",
                ConstraintType.LENGTH: "L",
                ConstraintType.ARC_LENGTH: "L⌒",
                ConstraintType.RADIUS: "R",
                ConstraintType.DIAMETER: "Ø",
                ConstraintType.POINT_ON_OBJECT: "P",
                ConstraintType.PARALLEL: "∥",
                ConstraintType.PERPENDICULAR: "⊥",
                ConstraintType.TANGENT: "T",
                ConstraintType.ANGLE: "∠",
            }.get(constraint.constraint_type)
            if short:
                badges.append(short)
        if badges:
            badge = QGraphicsSimpleTextItem(" ".join(badges), item)
            badge.setData(DRAWING_ROLE, "constraint-annotation")
            badge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            badge.setBrush(QBrush(QColor("#ff657a" if self._entity_has_conflict(entity.id) else "#76e0a6")))
            badge.setPos(label_position.x(), label_position.y() - 18)
            badge.setZValue(25)
        return item

    def sync_from_model(self) -> None:
        self._selection_guard = True
        try:
            for item in self._entity_items.values():
                self.scene().removeItem(item)
            self._entity_items.clear()
            for entity in self.document.active_sketch.ordered_entities():
                self._entity_items[entity.id] = self._render_entity(entity)
            self.selection_model.remove_missing(self._entity_items)
            self._sync_scene_selection()
        finally:
            self._selection_guard = False
        self.viewport().update()

    def _sync_scene_selection(self) -> None:
        self._selection_guard = True
        try:
            selected = set(self.selection_model.selected_ids)
            for entity_id, item in self._entity_items.items():
                item.setSelected(entity_id in selected)
                entity = self.document.active_sketch.entities[entity_id]
                item.setPen(self._entity_pen(entity, entity_id in selected, entity_id == self._preselected_id))  # type: ignore[attr-defined]
        finally:
            self._selection_guard = False

    def _scene_selection_changed(self) -> None:
        if self._selection_guard:
            return
        self.selection_model.set(
            str(item.data(ENTITY_ID_ROLE))
            for item in self.scene().selectedItems()
            if item.data(ENTITY_ID_ROLE)
        )

    def _set_preselection(self, entity_id: str | None) -> None:
        if entity_id == self._preselected_id:
            return
        previous = self._preselected_id
        self._preselected_id = entity_id
        self.selection_model.preselected_id = entity_id
        for current_id in (previous, entity_id):
            if current_id and current_id in self._entity_items:
                entity = self.document.active_sketch.entities[current_id]
                self._entity_items[current_id].setPen(  # type: ignore[attr-defined]
                    self._entity_pen(entity, current_id in self.selection_model.selected_ids, current_id == entity_id)
                )

    def _entity_at(self, viewport_position: QPoint) -> str | None:
        item = self.itemAt(viewport_position)
        while item is not None:
            entity_id = item.data(ENTITY_ID_ROLE)
            if entity_id:
                return str(entity_id)
            item = item.parentItem()
        return None

    def _new_preview(self, start: Point2D) -> QGraphicsItem:
        scene_start = self.model_to_scene(start)
        if self.tool == "line":
            item: QGraphicsItem = QGraphicsLineItem(QLineF(scene_start, scene_start))
        elif self.tool == "circle":
            item = QGraphicsEllipseItem(QRectF(scene_start, scene_start))
        elif self.tool in {"arc", "polygon"}:
            item = QGraphicsPathItem(QPainterPath(scene_start))
        else:
            item = QGraphicsRectItem(QRectF(scene_start, scene_start))
        item.setPen(QPen(QColor("#9edbff"), 1.5, Qt.PenStyle.DashLine))  # type: ignore[attr-defined]
        item.setZValue(50)
        self.scene().addItem(item)
        return item

    def cancel_preview(self) -> None:
        if self._preview is not None:
            self.scene().removeItem(self._preview)
        self._preview = None
        self._start = None
        self._start_snap_candidate = None
        self._last_snap_result = SnapResult(Point2D(0, 0), None, ())
        self._last_cursor_model = None
        self._snap_candidate_index = 0
        self._multi_points.clear()
        self._multi_candidates.clear()
        self.snap_marker.hide()
        self.snap_glyph.hide()
        for marker in self.snap_candidate_markers:
            marker.hide()

    def _update_multi_preview(self, current: Point2D) -> None:
        if not isinstance(self._preview, QGraphicsPathItem) or not self._multi_points:
            return
        path = QPainterPath(self.model_to_scene(self._multi_points[0]))
        preview_points = [*self._multi_points, current]
        if self.tool == "bspline" and len(preview_points) >= 2:
            try:
                path = self._bspline_path(BSplineEntity(tuple(preview_points), min(3, len(preview_points) - 1)))
            except CadValidationError:
                for point in preview_points[1:]:
                    path.lineTo(self.model_to_scene(point))
        elif self.tool in {"ellipse", "ellipse_arc"} and len(preview_points) >= 3:
            try:
                ellipse = ellipse_from_three_points(preview_points[0], preview_points[1], preview_points[2])
                if self.tool == "ellipse_arc" and len(preview_points) >= 5:
                    path = self._ellipse_path(elliptical_arc_from_five_points(*preview_points[:5]))
                else:
                    path = self._ellipse_path(ellipse)
            except CadValidationError:
                for point in preview_points[1:]:
                    path.lineTo(self.model_to_scene(point))
        elif self.tool == "slot" and len(preview_points) >= 3:
            try:
                path = self._slot_path(slot_from_three_points(preview_points[0], preview_points[1], preview_points[2]))
            except CadValidationError:
                for point in preview_points[1:]:
                    path.lineTo(self.model_to_scene(point))
        elif self.tool == "arc3" and len(preview_points) >= 3:
            try:
                path = self._arc_path(arc_from_three_points(preview_points[0], preview_points[1], preview_points[2]))
            except CadValidationError:
                for point in preview_points[1:]:
                    path.lineTo(self.model_to_scene(point))
        else:
            for point in preview_points[1:]:
                path.lineTo(self.model_to_scene(point))
        self._preview.setPath(path)

    def _finish_multi(self, *, closed: bool = False) -> None:
        points = tuple(self._multi_points)
        candidates = tuple(self._multi_candidates)
        tool = self.tool
        self.cancel_preview()
        self.multi_creation_requested.emit(tool, points, candidates, closed)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_origin = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if self.tool in {"polyline", "bspline"} and len(self._multi_points) >= 2:
                self._finish_multi()
                event.accept()
                return
            if self._preview is not None:
                self.cancel_preview()
            else:
                self.set_tool("select")
            event.accept()
            return
        if self.tool == "select" or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self.tool == "point":
            point = self.scene_to_model(self.mapToScene(event.position().toPoint()))
            result = self._snap_result(point)
            self.creation_requested.emit("point", result.point, result.point, result.active, result.active)
            event.accept()
            return
        if self.tool in {"polyline", "bspline", "arc3", "slot", "ellipse", "ellipse_arc"}:
            point = self.scene_to_model(self.mapToScene(event.position().toPoint()))
            result = self._snap_result(point)
            if self.tool == "polyline" and len(self._multi_points) >= 3 and result.point.distance_to(self._multi_points[0]) <= 1e-7:
                self._finish_multi(closed=True)
                event.accept()
                return
            if not self._multi_points or result.point.distance_to(self._multi_points[-1]) > 1e-7:
                self._multi_points.append(result.point)
                self._multi_candidates.append(result.active)
                self._start = result.point
            if self._preview is None:
                self._preview = QGraphicsPathItem(QPainterPath(self.model_to_scene(result.point)))
                self._preview.setPen(QPen(QColor("#9edbff"), 1.5, Qt.PenStyle.DashLine))
                self._preview.setZValue(50)
                self.scene().addItem(self._preview)
            if self.tool == "arc3" and len(self._multi_points) == 3:
                self._finish_multi()
            elif self.tool == "slot" and len(self._multi_points) == 3:
                self._finish_multi()
            elif self.tool == "ellipse" and len(self._multi_points) == 3:
                self._finish_multi()
            elif self.tool == "ellipse_arc" and len(self._multi_points) == 5:
                self._finish_multi()
            event.accept()
            return
        point = self.scene_to_model(self.mapToScene(event.position().toPoint()))
        start_result = self._snap_result(point)
        self._start = start_result.point
        self._start_snap_candidate = start_result.active
        self._preview = self._new_preview(self._start)
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
        if self.tool in {"polyline", "bspline", "arc3", "slot", "ellipse", "ellipse_arc"} and self._multi_points:
            current = self._snap_result(self.scene_to_model(self.mapToScene(event.position().toPoint()))).point
            self._update_multi_preview(current)
            event.accept()
            return
        if self._start is None or self._preview is None:
            self._set_preselection(self._entity_at(event.position().toPoint()))
            point = self.scene_to_model(self.mapToScene(event.position().toPoint()))
            self._snap_result(point)
            super().mouseMoveEvent(event)
            return
        current = self._snap_result(self.scene_to_model(self.mapToScene(event.position().toPoint()))).point
        scene_start, scene_current = self.model_to_scene(self._start), self.model_to_scene(current)
        if isinstance(self._preview, QGraphicsLineItem):
            self._preview.setLine(QLineF(scene_start, scene_current))
        elif isinstance(self._preview, QGraphicsPathItem) and self.tool == "arc":
            radius = self._start.distance_to(current)
            sweep = math.degrees(math.atan2(current.y - self._start.y, current.x - self._start.x))
            if abs(sweep) <= 1e-6:
                sweep = 90.0
            self._preview.setPath(self._arc_path(ArcEntity(self._start, max(radius, 1e-7), 0.0, sweep)))
        elif isinstance(self._preview, QGraphicsPathItem) and self.tool == "polygon":
            radius = self._start.distance_to(current)
            rotation = math.degrees(math.atan2(current.y - self._start.y, current.x - self._start.x))
            polygon = regular_polygon(self._start, max(radius, 1e-7), 6, rotation)
            path = QPainterPath(self.model_to_scene(polygon.points[0]))
            for point in polygon.points[1:]:
                path.lineTo(self.model_to_scene(point))
            path.closeSubpath()
            self._preview.setPath(path)
        else:
            if self.tool == "rect_center":
                delta = scene_current - scene_start
                rect = QRectF(scene_start - delta, scene_start + delta).normalized()
            else:
                rect = QRectF(scene_start, scene_current).normalized()
            self._preview.setRect(rect)  # type: ignore[attr-defined]
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor if self.tool == "select" else Qt.CursorShape.CrossCursor)
            event.accept()
            return
        if self.tool in {"polyline", "bspline", "arc3", "slot", "ellipse", "ellipse_arc"} and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        if self._preview is not None and self._start is not None and event.button() == Qt.MouseButton.LeftButton:
            end_result = self._snap_result(self.scene_to_model(self.mapToScene(event.position().toPoint())))
            current = end_result.point
            start = self._start
            start_candidate = self._start_snap_candidate
            end_candidate = end_result.active
            self.cancel_preview()
            if start.distance_to(current) > 1e-7:
                self.creation_requested.emit(self.tool, start, current, start_candidate, end_candidate)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        entity_id = self._entity_at(event.position().toPoint())
        if entity_id:
            self.entity_edit_requested.emit(entity_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        selected = [entity_id for entity_id in self.selection_model.selected_ids if entity_id in self.document.active_sketch.entities]
        if len(selected) == 2:
            for label, operation in (
                ("Podziel na przecięciu", "split"),
                ("Przytnij wskazany fragment", "trim"),
                ("Przedłuż wskazany koniec", "extend"),
            ):
                action = menu.addAction(label)
                action.setData(operation)
        entity_id = self._entity_at(event.pos())
        if entity_id:
            if not menu.isEmpty():
                menu.addSeparator()
            edit_action = menu.addAction("Edytuj wymiary")
            edit_action.setData(f"edit:{entity_id}")
        if menu.isEmpty():
            event.ignore()
            return
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        value = str(chosen.data() or "")
        if value.startswith("edit:"):
            self.entity_edit_requested.emit(value.split(":", 1)[1])
        elif value:
            self.topology_requested.emit(value)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Tab and self._last_cursor_model is not None and len(self._last_snap_result.candidates) > 1:
            self._snap_candidate_index = (self._snap_candidate_index + 1) % len(self._last_snap_result.candidates)
            self._snap_result(self._last_cursor_model, preserve_candidate=True)
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and self.tool in {"polyline", "bspline"} and len(self._multi_points) >= 2:
            self._finish_multi()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._preview is not None:
                self.cancel_preview()
            else:
                self.set_tool("select")
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        event.accept()

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        super().drawBackground(painter, rect)
        spacing = max(1.0, self.grid_size)
        pixel_spacing = abs(self.transform().m11()) * spacing
        if pixel_spacing < 12.0:
            spacing *= max(1, math.ceil(12.0 / max(pixel_spacing, 0.001)))
        left = math.floor(rect.left() / spacing) * spacing
        top = math.floor(rect.top() / spacing) * spacing
        lines: list[QLineF] = []
        x = left
        while x <= rect.right():
            lines.append(QLineF(x, rect.top(), x, rect.bottom()))
            x += spacing
        y = top
        while y <= rect.bottom():
            lines.append(QLineF(rect.left(), y, rect.right(), y))
            y += spacing
        painter.setPen(QPen(QColor(64, 91, 124, 52), 0))
        if lines:
            painter.drawLines(lines)

    def drawing_items(self) -> list[QGraphicsItem]:
        return list(self._entity_items.values())

    def drawing_bounds(self) -> QRectF:
        bounds = self.document.active_sketch.bounds()
        if bounds is None:
            return QRectF()
        left, bottom, right, top = bounds
        return QRectF(left, bottom, right - left, top - bottom)

    def delete_selected(self) -> None:
        entity_ids = tuple(self.selection_model.selected_ids)
        if not entity_ids:
            return
        self.command_stack.execute(RemoveEntitiesCommand(self.document.active_sketch_id, entity_ids))
        self.selection_model.clear()

    def clear_drawing(self) -> None:
        ids = tuple(self.document.active_sketch.order)
        if ids:
            self.command_stack.execute(RemoveEntitiesCommand(self.document.active_sketch_id, ids, "Wyczyść szkic"))
        self.selection_model.clear()


class TechnicalEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SIEKACZ CAD — szkic 2D")
        self.resize(1280, 840)
        self.setMinimumSize(940, 640)
        self.result_dimensions: tuple[float, float] | None = None
        self.project_path: Path | None = None
        self.selected_constraint_id: str | None = None
        self.selected_pattern_id: str | None = None
        self.selected_parameter_id: str | None = None
        self.document = CadDocument.create()
        self.command_stack = CommandStack(self.document)
        self.selection_model = SelectionModel()
        self.canvas = TechnicalCanvas(self.document, self.command_stack, self.selection_model, self)
        self.canvas.creation_requested.connect(self._create_geometry)
        self.canvas.multi_creation_requested.connect(self._create_multi_geometry)
        self.canvas.entity_edit_requested.connect(self._edit_entity)
        self.canvas.topology_requested.connect(self._apply_topology_edit)
        self.canvas.scene().selectionChanged.connect(self._clear_constraint_selection_from_canvas)

        self.width_input = self._dimension_input(self.canvas.board_width)
        self.height_input = self._dimension_input(self.canvas.board_height)
        self.snap = QCheckBox("Snap")
        self.snap.setChecked(True)
        self.snap.toggled.connect(self._set_snapping)
        self.auto_constraints = QCheckBox("Auto-więzy")
        self.auto_constraints.setChecked(True)
        self.auto_constraints.setToolTip("Pokazuje sugerowany więz przed kliknięciem i dodaje go razem z geometrią.")
        self.auto_constraints.toggled.connect(self._set_auto_constraints)
        self.status_label = QLabel("Gotowy — wybierz narzędzie")
        self.status_label.setObjectName("summaryLabel")
        self.canvas.status_changed.connect(self.status_label.setText)

        self.model_tree = QTreeWidget()
        self.model_tree.setHeaderLabel("MODEL CAD")
        self.model_tree.setMinimumWidth(210)
        self.model_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.model_tree.itemDoubleClicked.connect(self._tree_item_double_clicked)
        self.selection_model.subscribe(lambda _ids: self._refresh_side_panels())
        self.command_stack.subscribe(lambda _ids: self._refresh_side_panels())

        self.property_title = QLabel("Brak zaznaczenia")
        self.property_title.setStyleSheet("font-weight: 700; color: #8ec5ff;")
        self.property_details = QLabel("Zaznacz geometrię w widoku lub drzewie.")
        self.property_details.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._toolbar())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._side_panel())
        splitter.addWidget(self.canvas)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 950])
        layout.addWidget(splitter, 1)
        layout.addLayout(self._status_bar())

        self._install_actions()
        self._refresh_side_panels()

    def _dimension_input(self, value: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setObjectName("premiumInput")
        field.setRange(0.1, 100000.0)
        field.setDecimals(2)
        field.setValue(value)
        field.setSuffix(" mm")
        field.setMinimumWidth(110)
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
        root_layout = QVBoxLayout(bar)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(5)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        file_button = self._button("Plik", lambda: None)
        file_menu = QMenu(file_button)
        file_menu.addAction("Nowy", self._new_document, QKeySequence.StandardKey.New)
        file_menu.addAction("Otwórz projekt CAD…", self._open_document, QKeySequence.StandardKey.Open)
        file_menu.addAction("Zapisz", self._save_document, QKeySequence.StandardKey.Save)
        file_menu.addAction("Zapisz jako…", self._save_document_as, QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        file_menu.addAction("Importuj DXF…", self._import_dxf)
        file_menu.addAction("Podgląd i pomiary DXF / STEP / STL…", self._open_inspection_viewer)
        file_menu.addAction("Eksportuj DXF…", self._export_dxf)
        file_menu.addAction("Ostatni raport importu DXF…", self._show_dxf_import_report)
        file_menu.addAction("Warstwy CAD / DXF…", self._manage_layers)
        file_button.setMenu(file_menu)
        file_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(file_button)
        layout.addWidget(self._button("Cofnij", self._undo))
        layout.addWidget(self._button("Ponów", self._redo))

        tools = [
            ("Wskaźnik", "select"), ("Punkt", "point"), ("Linia", "line"),
            ("Polilinia", "polyline"), ("Prostokąt", "rect"), ("Okrąg", "circle"),
        ]
        self.tool_buttons: dict[str, QToolButton] = {}
        for text, tool in tools:
            button = self._button(text, lambda checked=False, selected=tool: self._select_tool(selected), True)
            button.setChecked(tool == "select")
            self.tool_buttons[tool] = button
            layout.addWidget(button)
        geometry_button = self._button("Więcej geometrii", lambda: None)
        geometry_menu = QMenu(geometry_button)
        for label, tool in (
            ("Prostokąt od środka", "rect_center"),
            ("Wielokąt foremny", "polygon"),
            ("Szczelina", "slot"),
            ("Krzywa B-spline", "bspline"),
            ("Elipsa", "ellipse"),
            ("Łuk elipsy", "ellipse_arc"),
            ("Łuk od środka", "arc"),
            ("Łuk przez trzy punkty", "arc3"),
        ):
            geometry_menu.addAction(label, lambda checked=False, selected=tool: self._select_tool(selected))
        geometry_button.setMenu(geometry_menu)
        geometry_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(geometry_button)
        edit_button = self._button("Edycja", lambda: None)
        edit_menu = QMenu(edit_button)
        edit_menu.addAction("Podziel na przecięciu", self._split_selected_curve)
        edit_menu.addAction("Przytnij wskazany fragment", self._trim_selected_curve)
        edit_menu.addAction("Przedłuż wskazany koniec", self._extend_selected_line)
        edit_button.setMenu(edit_menu)
        edit_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        edit_button.setToolTip("Zaznacz dwie krzywe. Ostatnia pozycja kursora w widoku wskazuje fragment lub koniec.")
        layout.addWidget(edit_button)
        constraint_button = self._button("Więzy", lambda: None)
        constraint_menu = QMenu(constraint_button)
        constraint_menu.addAction("Poziomość", self._add_horizontal_constraint)
        constraint_menu.addAction("Pionowość", self._add_vertical_constraint)
        constraint_menu.addAction("Zbieżność najbliższych końców", self._add_coincident_constraint)
        constraint_menu.addAction("Punkt na obiekcie", self._add_point_on_object_constraint)
        constraint_menu.addAction("Równoległość", lambda: self._add_direction_pair_constraint(ConstraintType.PARALLEL, "Równoległość"))
        constraint_menu.addAction("Prostopadłość", lambda: self._add_direction_pair_constraint(ConstraintType.PERPENDICULAR, "Prostopadłość"))
        constraint_menu.addAction("Styczność", self._add_tangent_constraint)
        constraint_menu.addAction("Kąt linii…", self._add_angle_constraint)
        constraint_menu.addAction("Długość linii…", self._add_length_constraint)
        constraint_menu.addAction("Długość łuku…", self._add_arc_length_constraint)
        constraint_menu.addAction("Średnica okręgu…", self._add_diameter_constraint)
        constraint_menu.addAction("Promień główny elipsy…", lambda: self._add_ellipse_radius_constraint("entity", "główny"))
        constraint_menu.addAction("Promień pomocniczy elipsy…", lambda: self._add_ellipse_radius_constraint("minor-radius", "pomocniczy"))
        constraint_menu.addAction("Zwiąż wymiary prostokąta", self._constrain_rectangle_dimensions)
        constraint_menu.addAction("Zwiąż bieżące położenie środka", self._constrain_current_center)
        constraint_menu.addAction("Wyśrodkuj w początku (0, 0)", self._center_at_origin)
        constraint_menu.addAction("Szyk liniowy okręgu…", self._add_linear_pattern)
        constraint_menu.addAction("Zablokuj geometrię", self._add_fix_constraint)
        constraint_menu.addSeparator()
        constraint_menu.addAction("Włącz / wyłącz wybrany więz", self._toggle_selected_constraint)
        constraint_menu.addAction("Usuń wybrany więz", self._delete_selected_constraint)
        constraint_menu.addAction("Usuń wybrany szyk", self._delete_selected_pattern)
        constraint_button.setMenu(constraint_menu)
        constraint_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(constraint_button)
        layout.addWidget(self._button("Parametry", self._open_parameter_manager))
        layout.addWidget(self._button("Sprawdź profil", self._check_and_repair_profile))
        layout.addWidget(self.snap)
        layout.addWidget(self.auto_constraints)
        layout.addStretch(1)
        board_layout = QHBoxLayout()
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(6)
        board_layout.addWidget(QLabel("Obszar roboczy"))
        board_layout.addWidget(self.width_input)
        board_layout.addWidget(QLabel("×"))
        board_layout.addWidget(self.height_input)
        board_layout.addWidget(self._button("Ustaw", self._apply_board_size))
        board_layout.addWidget(self._button("Dopasuj", self.canvas.fit_board))
        board_layout.addSpacing(12)
        hint = QLabel("Dwuklik: edycja wymiaru · Delete: usuń · Esc: zakończ narzędzie")
        hint.setObjectName("summaryLabel")
        board_layout.addWidget(hint)
        board_layout.addStretch(1)
        root_layout.addLayout(layout)
        root_layout.addLayout(board_layout)
        return bar

    def _side_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.addWidget(self.model_tree, 2)
        layout.addWidget(QLabel("WŁAŚCIWOŚCI"))
        layout.addWidget(self.property_title)
        layout.addWidget(self.property_details)
        layout.addStretch(1)
        return panel

    def _status_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.addWidget(self.status_label, 1)
        self.selection_status = QLabel("Zaznaczenie: 0")
        layout.addWidget(self.selection_status)
        self.solver_status = QLabel("DoF: 0")
        self.solver_status.setObjectName("summaryLabel")
        layout.addWidget(self.solver_status)
        cancel = QPushButton("Zamknij")
        cancel.setObjectName("smallButton")
        cancel.clicked.connect(self.reject)
        add = QPushButton("Dodaj szkic jako formatkę")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._accept_drawing)
        layout.addWidget(cancel)
        layout.addWidget(add)
        return layout

    def _install_actions(self) -> None:
        for shortcut, callback in (
            (QKeySequence.StandardKey.Undo, self._undo),
            (QKeySequence.StandardKey.Redo, self._redo),
            (QKeySequence.StandardKey.Save, self._save_document),
            (QKeySequence.StandardKey.Open, self._open_document),
            (QKeySequence(Qt.Key.Key_Delete), self._delete_current_selection),
            (QKeySequence("N"), lambda: self._activate_tool_shortcut("point")),
            (QKeySequence("L"), lambda: self._activate_tool_shortcut("line")),
            (QKeySequence("R"), lambda: self._activate_tool_shortcut("rect")),
            (QKeySequence("Shift+R"), lambda: self._activate_tool_shortcut("rect_center")),
            (QKeySequence("C"), lambda: self._activate_tool_shortcut("circle")),
            (QKeySequence("A"), lambda: self._activate_tool_shortcut("arc")),
            (QKeySequence("P"), lambda: self._activate_tool_shortcut("polyline")),
            (QKeySequence("Shift+P"), lambda: self._activate_tool_shortcut("polygon")),
            (QKeySequence("S"), lambda: self._activate_tool_shortcut("slot")),
            (QKeySequence("B"), lambda: self._activate_tool_shortcut("bspline")),
            (QKeySequence("E"), lambda: self._activate_tool_shortcut("ellipse")),
            (QKeySequence("Shift+E"), lambda: self._activate_tool_shortcut("ellipse_arc")),
            (QKeySequence("Shift+A"), lambda: self._activate_tool_shortcut("arc3")),
            (QKeySequence("F"), self.canvas.fit_board),
        ):
            action = QAction(self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(callback)
            self.addAction(action)

    def _activate_tool_shortcut(self, tool: str) -> None:
        # Letter shortcuts must never steal keystrokes from a numeric expression
        # or any other text field.
        if isinstance(QApplication.focusWidget(), (QLineEdit, QDoubleSpinBox)):
            return
        self._select_tool(tool)

    def _select_tool(self, tool: str) -> None:
        for name, button in self.tool_buttons.items():
            button.setChecked(name == tool)
        self.canvas.set_tool(tool)
        names = {
            "select": "Wskaźnik",
            "point": "Punkt: wskaż położenie",
            "line": "Linia: wskaż początek i koniec",
            "polyline": "Polilinia: klikaj wierzchołki · Enter/prawy przycisk kończy · klik pierwszego punktu zamyka",
            "rect": "Prostokąt: wskaż dwa narożniki",
            "rect_center": "Prostokąt od środka: wskaż środek i narożnik",
            "polygon": "Wielokąt foremny: wskaż środek i pierwszy wierzchołek",
            "slot": "Szczelina: wskaż początek i koniec osi, następnie połowę szerokości",
            "bspline": "B-spline: klikaj punkty kontrolne · Enter/prawy przycisk kończy",
            "ellipse": "Elipsa: wskaż środek, koniec osi głównej i promień pomocniczy",
            "ellipse_arc": "Łuk elipsy: środek, oś główna, promień pomocniczy, początek i koniec łuku",
            "circle": "Okrąg: wskaż środek i promień",
            "arc": "Łuk: wskaż środek i punkt końcowy",
            "arc3": "Łuk 3P: wskaż początek, punkt na łuku i koniec",
        }
        self.status_label.setText(names[tool])

    def _set_snapping(self, enabled: bool) -> None:
        self.canvas.snap_engine.endpoint_enabled = enabled
        self.canvas.snap_engine.midpoint_enabled = enabled
        self.canvas.snap_engine.center_enabled = enabled
        self.canvas.snap_engine.origin_enabled = enabled
        self.canvas.snap_engine.grid_enabled = enabled
        self.canvas.snap_engine.intersection_enabled = enabled
        self.canvas.snap_engine.nearest_enabled = enabled
        self.canvas.snap_engine.axis_enabled = enabled
        self.canvas.snap_engine.extension_enabled = enabled
        self.canvas.snap_engine.direction_enabled = enabled
        self.canvas.snap_engine.tangent_enabled = enabled
        self.canvas.snap_engine.incremental_angle_enabled = enabled

    def _set_auto_constraints(self, enabled: bool) -> None:
        self.canvas.auto_constraints_enabled = enabled

    def _create_multi_geometry(self, tool: str, raw_points: object, raw_candidates: object, closed: bool = False) -> None:
        if not isinstance(raw_points, tuple) or not all(isinstance(point, Point2D) for point in raw_points):
            return
        candidates = raw_candidates if isinstance(raw_candidates, tuple) else ()
        try:
            parameters = self.document.parameter_values(self.document.active_sketch_id)
            if tool == "polyline":
                dialog = PolylineSpecificationDialog(raw_points, closed, parameters=parameters, parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                points, exact_closed, bulges = dialog.values()
                entity: SketchEntity = PolylineEntity(points, exact_closed, bulges)
                proposal = build_polyline_auto_constraints(
                    self.document.active_sketch,
                    entity,
                    tuple(candidate if isinstance(candidate, SnapCandidate) else None for candidate in candidates),
                ) if self.canvas.auto_constraints_enabled else None
            elif tool == "bspline" and len(raw_points) >= 2:
                dialog = BSplineSpecificationDialog(
                    raw_points,
                    min(3, len(raw_points) - 1),
                    parameters=parameters,
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                control_points, degree = dialog.values()
                entity = BSplineEntity(control_points, degree)
                proposal = build_bspline_auto_constraints(
                    entity,
                    tuple(candidate if isinstance(candidate, SnapCandidate) else None for candidate in candidates),
                ) if self.canvas.auto_constraints_enabled else None
            elif tool == "arc3" and len(raw_points) == 3:
                preview = arc_from_three_points(raw_points[0], raw_points[1], raw_points[2])
                dialog = ArcSpecificationDialog(
                    preview.center,
                    preview.radius,
                    preview.start_angle_deg,
                    preview.sweep_angle_deg,
                    parameters=parameters,
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, radius, start_angle, sweep_angle = dialog.values()
                entity = ArcEntity(center, radius, start_angle, sweep_angle)
                proposal = build_auto_constraints(
                    self.document.active_sketch,
                    entity,
                    start_point=raw_points[0],
                    end_point=raw_points[-1],
                    start_candidate=candidates[0] if candidates and isinstance(candidates[0], SnapCandidate) else None,
                    end_candidate=candidates[-1] if candidates and isinstance(candidates[-1], SnapCandidate) else None,
                ) if self.canvas.auto_constraints_enabled else None
            elif tool == "slot" and len(raw_points) == 3:
                preview = slot_from_three_points(raw_points[0], raw_points[1], raw_points[2])
                dialog = SlotSpecificationDialog(
                    preview.axis_start,
                    preview.axis_end,
                    preview.width,
                    parameters=parameters,
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                axis_start, axis_end, width = dialog.values()
                entity = SlotEntity(axis_start, axis_end, width / 2.0)
                proposal = build_auto_constraints(
                    self.document.active_sketch,
                    entity,
                    start_point=raw_points[0],
                    end_point=raw_points[1],
                    start_candidate=candidates[0] if candidates and isinstance(candidates[0], SnapCandidate) else None,
                    end_candidate=candidates[1] if len(candidates) > 1 and isinstance(candidates[1], SnapCandidate) else None,
                ) if self.canvas.auto_constraints_enabled else None
            elif tool == "ellipse" and len(raw_points) == 3:
                preview = ellipse_from_three_points(raw_points[0], raw_points[1], raw_points[2])
                dialog = EllipseSpecificationDialog(
                    preview.center,
                    preview.major_radius,
                    preview.minor_radius,
                    preview.rotation_deg,
                    parameters=parameters,
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, major, minor, rotation = dialog.values()
                entity = EllipseEntity(center, major, minor, rotation)
                normalized = [candidate if isinstance(candidate, SnapCandidate) else None for candidate in candidates[:3]]
                normalized.extend([None] * (3 - len(normalized)))
                normalized_candidates = (normalized[0], normalized[1], normalized[2])
                proposal = build_ellipse_auto_constraints(entity, normalized_candidates) if self.canvas.auto_constraints_enabled else None
            elif tool == "ellipse_arc" and len(raw_points) == 5:
                preview = elliptical_arc_from_five_points(*raw_points)
                dialog = EllipticalArcSpecificationDialog(
                    preview.center,
                    preview.major_radius,
                    preview.minor_radius,
                    preview.rotation_deg,
                    preview.start_parameter_deg,
                    preview.sweep_parameter_deg,
                    parameters=parameters,
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, major, minor, rotation, start_parameter, sweep = dialog.arc_values()
                entity = EllipticalArcEntity(center, major, minor, rotation, start_parameter, sweep)
                normalized_candidates = tuple(
                    candidate if isinstance(candidate, SnapCandidate) else None for candidate in candidates
                )
                proposal = build_elliptical_arc_auto_constraints(entity, normalized_candidates) if self.canvas.auto_constraints_enabled else None
            else:
                return
            with self.command_stack.transaction(f"Utwórz {tool} z auto-więzami"):
                self.command_stack.execute(AddEntityCommand(self.document.active_sketch_id, entity))
                if proposal is not None:
                    for constraint in proposal.constraints:
                        self.command_stack.execute(AddConstraintCommand(self.document.active_sketch_id, constraint))
            self.selection_model.set((entity.id,))
            labels = proposal.labels if proposal is not None else ()
            self.status_label.setText(
                (
                    "Utworzono polilinię"
                    if tool == "polyline"
                    else "Utworzono natywną krzywą B-spline"
                    if tool == "bspline"
                    else "Utworzono łuk przez trzy punkty"
                    if tool == "arc3"
                    else "Utworzono analityczną szczelinę"
                    if tool == "slot"
                    else "Utworzono analityczną elipsę"
                    if tool == "ellipse"
                    else "Utworzono analityczny łuk elipsy"
                )
                + (" · auto-więzy: " + ", ".join(labels) if labels else "")
            )
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nie można utworzyć geometrii", str(exc))

    def _create_geometry(
        self,
        tool: str,
        first: object,
        second: object,
        start_candidate: object = None,
        end_candidate: object = None,
    ) -> None:
        if not isinstance(first, Point2D) or not isinstance(second, Point2D):
            return
        try:
            parameters = self.document.parameter_values(self.document.active_sketch_id)
            if tool == "point":
                dialog = PointSpecificationDialog(first, parameters=parameters, parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                entity: SketchEntity = PointEntity(dialog.values())
            elif tool == "line":
                length = first.distance_to(second)
                angle = math.degrees(math.atan2(second.y - first.y, second.x - first.x))
                dialog = LineSpecificationDialog(first, length, angle, parameters=parameters, parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                start, exact_length, exact_angle = dialog.values()
                entity = line_from_length_angle(start, exact_length, exact_angle)
            elif tool == "rect":
                origin = Point2D(min(first.x, second.x), min(first.y, second.y))
                dialog = RectangleSpecificationDialog(origin, abs(second.x - first.x), abs(second.y - first.y), parameters=self.document.parameter_values(self.document.active_sketch_id), parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                exact_origin, width, height = dialog.values()
                entity = RectangleEntity(exact_origin, width, height)
            elif tool == "rect_center":
                dialog = CenterRectangleSpecificationDialog(
                    first,
                    abs(second.x - first.x) * 2.0,
                    abs(second.y - first.y) * 2.0,
                    parameters=parameters,
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, width, height = dialog.values()
                entity = rectangle_from_center(center, width, height)
            elif tool == "polygon":
                radius = first.distance_to(second)
                rotation = math.degrees(math.atan2(second.y - first.y, second.x - first.x))
                dialog = RegularPolygonSpecificationDialog(first, radius, 6, rotation, parameters=parameters, parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, radius, sides, rotation = dialog.values()
                entity = regular_polygon(center, radius, sides, rotation)
            elif tool == "circle":
                dialog = CircleSpecificationDialog(first, first.distance_to(second), parameters=self.document.parameter_values(self.document.active_sketch_id), parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, radius = dialog.values()
                entity = CircleEntity(center, radius)
            elif tool == "arc":
                radius = first.distance_to(second)
                sweep = math.degrees(math.atan2(second.y - first.y, second.x - first.x))
                if abs(sweep) <= 1e-6:
                    sweep = 90.0
                dialog = ArcSpecificationDialog(
                    first,
                    radius,
                    0.0,
                    sweep,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, radius, start_angle, sweep_angle = dialog.values()
                entity = ArcEntity(center, radius, start_angle, sweep_angle)
            else:
                return
            proposal = build_auto_constraints(
                self.document.active_sketch,
                entity,
                start_point=first,
                end_point=second,
                start_candidate=start_candidate if isinstance(start_candidate, SnapCandidate) else None,
                end_candidate=end_candidate if isinstance(end_candidate, SnapCandidate) else None,
            ) if self.canvas.auto_constraints_enabled else None
            with self.command_stack.transaction(f"Utwórz {tool} z auto-więzami"):
                self.command_stack.execute(AddEntityCommand(self.document.active_sketch_id, entity))
                if proposal is not None:
                    for constraint in proposal.constraints:
                        self.command_stack.execute(AddConstraintCommand(self.document.active_sketch_id, constraint))
            self.selection_model.set((entity.id,))
            if proposal is not None and proposal.labels:
                self.status_label.setText("Dodano auto-więzy: " + ", ".join(proposal.labels))
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nie można utworzyć geometrii", str(exc))

    def _edit_entity(self, entity_id: str) -> None:
        entity = self.document.active_sketch.entities.get(entity_id)
        if entity is None:
            return
        try:
            if isinstance(entity, PointEntity):
                dialog = PointSpecificationDialog(entity.point, parameters=self.document.parameter_values(self.document.active_sketch_id), parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                replacement: SketchEntity = PointEntity(dialog.values(), id=entity.id)
            elif isinstance(entity, LineEntity):
                dialog = LineSpecificationDialog(entity.start, entity.length, entity.angle_deg, parameters=self.document.parameter_values(self.document.active_sketch_id), parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                start, length, angle = dialog.values()
                replacement = line_from_length_angle(start, length, angle, entity_id=entity.id)
            elif isinstance(entity, RectangleEntity):
                dialog = RectangleSpecificationDialog(entity.origin, entity.width, entity.height, parameters=self.document.parameter_values(self.document.active_sketch_id), parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                origin, width, height = dialog.values()
                replacement = RectangleEntity(origin, width, height, id=entity.id)
            elif isinstance(entity, CircleEntity):
                dialog = CircleSpecificationDialog(entity.center, entity.radius, parameters=self.document.parameter_values(self.document.active_sketch_id), parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, radius = dialog.values()
                replacement = CircleEntity(center, radius, id=entity.id)
            elif isinstance(entity, ArcEntity):
                dialog = ArcSpecificationDialog(
                    entity.center,
                    entity.radius,
                    entity.start_angle_deg,
                    entity.sweep_angle_deg,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, radius, start_angle, sweep_angle = dialog.values()
                replacement = ArcEntity(center, radius, start_angle, sweep_angle, id=entity.id)
            elif isinstance(entity, EllipseEntity):
                dialog = EllipseSpecificationDialog(
                    entity.center,
                    entity.major_radius,
                    entity.minor_radius,
                    entity.rotation_deg,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, major, minor, rotation = dialog.values()
                replacement = EllipseEntity(center, major, minor, rotation, id=entity.id)
            elif isinstance(entity, EllipticalArcEntity):
                dialog = EllipticalArcSpecificationDialog(
                    entity.center,
                    entity.major_radius,
                    entity.minor_radius,
                    entity.rotation_deg,
                    entity.start_parameter_deg,
                    entity.sweep_parameter_deg,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, major, minor, rotation, start_parameter, sweep = dialog.arc_values()
                replacement = EllipticalArcEntity(center, major, minor, rotation, start_parameter, sweep, id=entity.id)
            elif isinstance(entity, PolylineEntity):
                dialog = PolylineSpecificationDialog(
                    entity.points,
                    entity.closed,
                    entity.bulges,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                points, closed, bulges = dialog.values()
                replacement = replace(entity, points=points, closed=closed, bulges=bulges)
            elif isinstance(entity, BSplineEntity):
                dialog = BSplineSpecificationDialog(
                    entity.control_points,
                    entity.degree,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                points, degree = dialog.values()
                preserve_definition = degree == entity.degree and len(points) == len(entity.control_points)
                replacement = replace(
                    entity,
                    control_points=points,
                    degree=degree,
                    knots=entity.knots if preserve_definition else (),
                    weights=entity.weights if preserve_definition else (),
                    closed=entity.closed if preserve_definition else False,
                    periodic=entity.periodic if preserve_definition else False,
                )
            elif isinstance(entity, RegularPolygonEntity):
                dialog = RegularPolygonSpecificationDialog(
                    entity.center,
                    entity.radius,
                    entity.sides,
                    entity.rotation_deg,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                center, radius, sides, rotation = dialog.values()
                replacement = RegularPolygonEntity(center, radius, sides, rotation, id=entity.id)
            elif isinstance(entity, SlotEntity):
                dialog = SlotSpecificationDialog(
                    entity.axis_start,
                    entity.axis_end,
                    entity.width,
                    parameters=self.document.parameter_values(self.document.active_sketch_id),
                    parent=self,
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                axis_start, axis_end, width = dialog.values()
                replacement = SlotEntity(axis_start, axis_end, width / 2.0, id=entity.id)
            else:
                return
            self.command_stack.execute(ReplaceEntityCommand(self.document.active_sketch_id, replacement))
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nie można zmienić geometrii", str(exc))

    def _selected_entities(self) -> list[SketchEntity]:
        sketch = self.document.active_sketch
        return [sketch.entities[entity_id] for entity_id in self.selection_model.selected_ids if entity_id in sketch.entities]

    @staticmethod
    def _entity_center_for_pick(entity: EditableCurve) -> Point2D:
        left, bottom, right, top = entity.bounds()
        return Point2D((left + right) / 2, (bottom + top) / 2)

    def _selected_curve_pair(self, title: str, *, require_line: bool = False) -> tuple[EditableCurve, EditableCurve, Point2D] | None:
        curves = [entity for entity in self._selected_entities() if isinstance(entity, (LineEntity, ArcEntity, CircleEntity))]
        if len(curves) != 2:
            QMessageBox.information(
                self,
                title,
                "Zaznacz dokładnie dwie krzywe: geometrię edytowaną i granicę. "
                "Przed uruchomieniem wskaż kursorem właściwy fragment lub koniec.",
            )
            return None
        pick = self.canvas._last_cursor_model or self._entity_center_for_pick(curves[0])
        preselected = self.selection_model.preselected_id
        if preselected in {curve.id for curve in curves}:
            target = next(curve for curve in curves if curve.id == preselected)
        else:
            target = min(curves, key=lambda curve: distance_to_curve(curve, pick))
        boundary = curves[1] if curves[0].id == target.id else curves[0]
        if require_line and not isinstance(target, LineEntity):
            if isinstance(boundary, LineEntity):
                target, boundary = boundary, target
            else:
                QMessageBox.information(self, title, "Przedłużana geometria musi być linią.")
                return None
        return target, boundary, pick

    def _apply_topology_edit(self, operation: str) -> None:
        titles = {"split": "Podziel geometrię", "trim": "Przytnij geometrię", "extend": "Przedłuż linię"}
        selected = self._selected_curve_pair(titles[operation], require_line=operation == "extend")
        if selected is None:
            return
        target, boundary, pick = selected
        try:
            if operation == "split":
                edit = split_curve(target, boundary, pick)
            elif operation == "trim":
                edit = trim_curve(target, boundary, pick)
            else:
                assert isinstance(target, LineEntity)
                edit = extend_line(target, boundary, pick)
            command = ApplyTopologyEditCommand(self.document.active_sketch_id, edit, titles[operation])
            self.command_stack.execute(command)
            self.selection_model.set(piece.id for piece in edit.pieces)
            removed = (
                f" Usunięto {len(command.removed_constraint_ids)} nieaktualnych więzów; undo przywróci je razem z geometrią."
                if command.removed_constraint_ids else ""
            )
            self.status_label.setText(f"{titles[operation]}: wynik zawiera {len(edit.pieces)} krzywe.{removed}")
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, titles[operation], str(exc))

    def _split_selected_curve(self) -> None:
        self._apply_topology_edit("split")

    def _trim_selected_curve(self) -> None:
        self._apply_topology_edit("trim")

    def _extend_selected_line(self) -> None:
        self._apply_topology_edit("extend")

    def _execute_constraint(self, constraint: SketchConstraint) -> None:
        try:
            self.command_stack.execute(AddConstraintCommand(self.document.active_sketch_id, constraint))
            self.selected_constraint_id = constraint.id
            self.selected_parameter_id = None
            self._refresh_side_panels()
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nie można dodać więzu", str(exc))
            return
        sketch = self.document.active_sketch
        if sketch.solve_status in {SketchSolveStatus.CONFLICTING, SketchSolveStatus.UNSOLVABLE}:
            QMessageBox.warning(
                self,
                "Konflikt więzów",
                f"{sketch.error}\n\nKonfliktowe więzy są oznaczone na czerwono w drzewie. "
                "Możesz wyłączyć lub usunąć wybrany więz.",
            )

    def _add_horizontal_constraint(self) -> None:
        lines = [entity for entity in self._selected_entities() if isinstance(entity, LineEntity)]
        if not lines:
            QMessageBox.information(self, "Poziomość", "Zaznacz co najmniej jedną linię.")
            return
        with self.command_stack.transaction("Dodaj więzy poziomości"):
            for line in lines:
                self.command_stack.execute(AddConstraintCommand(
                    self.document.active_sketch_id,
                    SketchConstraint(ConstraintType.HORIZONTAL, (GeometryReference(line.id),)),
                ))

    def _add_vertical_constraint(self) -> None:
        lines = [entity for entity in self._selected_entities() if isinstance(entity, LineEntity)]
        if not lines:
            QMessageBox.information(self, "Pionowość", "Zaznacz co najmniej jedną linię.")
            return
        with self.command_stack.transaction("Dodaj więzy pionowości"):
            for line in lines:
                self.command_stack.execute(AddConstraintCommand(
                    self.document.active_sketch_id,
                    SketchConstraint(ConstraintType.VERTICAL, (GeometryReference(line.id),)),
                ))

    def _add_coincident_constraint(self) -> None:
        curves = [entity for entity in self._selected_entities() if isinstance(entity, (LineEntity, ArcEntity, EllipticalArcEntity, BSplineEntity))]
        if len(curves) != 2:
            QMessageBox.information(self, "Zbieżność", "Zaznacz dokładnie dwie linie lub łuki z końcami.")
            return
        endpoints = (("start", curves[0].start), ("end", curves[0].end))
        other_endpoints = (("start", curves[1].start), ("end", curves[1].end))
        first_name, _first_point, second_name, _second_point = min(
            (
                (first_name, first_point, second_name, second_point)
                for first_name, first_point in endpoints
                for second_name, second_point in other_endpoints
            ),
            key=lambda item: item[1].distance_to(item[3]),
        )
        self._execute_constraint(SketchConstraint(
            ConstraintType.COINCIDENT,
            (GeometryReference(curves[0].id, first_name), GeometryReference(curves[1].id, second_name)),
        ))

    def _add_point_on_object_constraint(self) -> None:
        entities = self._selected_entities()
        if len(entities) != 2:
            QMessageBox.information(self, "Punkt na obiekcie", "Zaznacz punkt/krzywą z końcem oraz docelową krzywą.")
            return
        source, target = entities
        source_types = (PointEntity, LineEntity, ArcEntity, EllipticalArcEntity, BSplineEntity)
        target_types = (LineEntity, CircleEntity, ArcEntity, EllipseEntity, EllipticalArcEntity, BSplineEntity)
        if not isinstance(source, source_types) or not isinstance(target, target_types):
            if isinstance(target, source_types) and isinstance(source, target_types):
                source, target = target, source
            else:
                QMessageBox.information(self, "Punkt na obiekcie", "Jedna geometria musi dostarczać punkt lub koniec, a druga być obsługiwaną krzywą docelową.")
                return
        assert isinstance(source, source_types)

        def distance(point: Point2D) -> float:
            if isinstance(target, (ArcEntity, EllipseEntity, EllipticalArcEntity, BSplineEntity)):
                return point.distance_to(target.nearest_point(point))
            if isinstance(target, CircleEntity):
                return abs(point.distance_to(target.center) - target.radius)
            dx, dy = target.end.x - target.start.x, target.end.y - target.start.y
            return abs(dx * (target.start.y - point.y) - (target.start.x - point.x) * dy) / target.length

        if isinstance(source, PointEntity):
            element = "point"
        else:
            element = min(("start", "end"), key=lambda name: distance(source.start if name == "start" else source.end))
        self._execute_constraint(SketchConstraint(
            ConstraintType.POINT_ON_OBJECT,
            (GeometryReference(source.id, element), GeometryReference(target.id)),
        ))

    def _add_direction_pair_constraint(self, kind: ConstraintType, title: str) -> None:
        lines = [entity for entity in self._selected_entities() if isinstance(entity, LineEntity)]
        if len(lines) != 2:
            QMessageBox.information(self, title, "Zaznacz dokładnie dwie linie.")
            return
        self._execute_constraint(SketchConstraint(
            kind,
            (GeometryReference(lines[0].id), GeometryReference(lines[1].id)),
        ))

    def _add_tangent_constraint(self) -> None:
        entities = self._selected_entities()
        circular = (CircleEntity, ArcEntity)
        valid = len(entities) == 2 and (
            (isinstance(entities[0], LineEntity) and isinstance(entities[1], circular))
            or (isinstance(entities[1], LineEntity) and isinstance(entities[0], circular))
            or (isinstance(entities[0], circular) and isinstance(entities[1], circular))
        )
        if not valid:
            QMessageBox.information(self, "Styczność", "Zaznacz linię i okrąg albo dwa okręgi.")
            return
        self._execute_constraint(SketchConstraint(
            ConstraintType.TANGENT,
            tuple(GeometryReference(entity.id) for entity in entities),
        ))

    def _add_angle_constraint(self) -> None:
        directional = [entity for entity in self._selected_entities() if isinstance(entity, (LineEntity, EllipseEntity, EllipticalArcEntity))]
        if len(directional) != 1:
            QMessageBox.information(self, "Kąt", "Zaznacz dokładnie jedną linię, elipsę albo łuk elipsy.")
            return
        entity = directional[0]
        initial = entity.angle_deg if isinstance(entity, LineEntity) else entity.rotation_deg
        value, accepted = QInputDialog.getDouble(
            self,
            "Kąt linii",
            "Kąt względem osi X [°]",
            initial,
            -360.0,
            360.0,
            4,
        )
        if accepted:
            self._execute_constraint(SketchConstraint(
                ConstraintType.ANGLE,
                (GeometryReference(entity.id, "entity" if isinstance(entity, LineEntity) else "major-axis"),),
                value=value,
            ))

    def _prompt_length(self, title: str, label: str, initial: float, expression: str = "") -> tuple[float, str] | None:
        text, accepted = QInputDialog.getText(
            self,
            title,
            label + "\nMożesz użyć nazwanego parametru lub wyrażenia:",
            QLineEdit.EchoMode.Normal,
            expression or f"{initial:g} mm",
        )
        if not accepted:
            return None
        try:
            value = parse_length(text, self.document.parameter_values(self.document.active_sketch_id))
        except CadValidationError as exc:
            QMessageBox.warning(self, title, str(exc))
            return None
        if value <= 0:
            QMessageBox.warning(self, title, "Wartość musi być dodatnia.")
            return None
        return value, text.strip()

    def _add_length_constraint(self) -> None:
        lines = [entity for entity in self._selected_entities() if isinstance(entity, LineEntity)]
        if len(lines) != 1:
            QMessageBox.information(self, "Długość", "Zaznacz dokładnie jedną linię.")
            return
        result = self._prompt_length("Długość linii", "Długość sterująca", lines[0].length)
        if result is not None:
            value, expression = result
            constraint = SketchConstraint(ConstraintType.LENGTH, (GeometryReference(lines[0].id),), value=value)
            self._execute_constraint(bind_constraint_expression(self.document, self.document.active_sketch_id, constraint, expression))

    def _add_diameter_constraint(self) -> None:
        circles = [entity for entity in self._selected_entities() if isinstance(entity, (CircleEntity, ArcEntity))]
        if len(circles) != 1:
            QMessageBox.information(self, "Średnica", "Zaznacz dokładnie jeden okrąg lub łuk.")
            return
        result = self._prompt_length("Średnica okręgu", "Średnica sterująca", circles[0].radius * 2)
        if result is not None:
            value, expression = result
            constraint = SketchConstraint(ConstraintType.DIAMETER, (GeometryReference(circles[0].id),), value=value)
            self._execute_constraint(bind_constraint_expression(self.document, self.document.active_sketch_id, constraint, expression))

    def _add_ellipse_radius_constraint(self, element: str, label: str) -> None:
        ellipses = [entity for entity in self._selected_entities() if isinstance(entity, (EllipseEntity, EllipticalArcEntity))]
        if len(ellipses) != 1:
            QMessageBox.information(self, "Promień elipsy", "Zaznacz dokładnie jedną elipsę albo łuk elipsy.")
            return
        entity = ellipses[0]
        initial = entity.minor_radius if element == "minor-radius" else entity.major_radius
        result = self._prompt_length(f"Promień {label} elipsy", "Promień sterujący", initial)
        if result is not None:
            value, expression = result
            constraint = SketchConstraint(ConstraintType.RADIUS, (GeometryReference(entity.id, element),), value=value)
            self._execute_constraint(bind_constraint_expression(self.document, self.document.active_sketch_id, constraint, expression))

    def _add_arc_length_constraint(self) -> None:
        arcs = [entity for entity in self._selected_entities() if isinstance(entity, (ArcEntity, EllipticalArcEntity))]
        if len(arcs) != 1:
            QMessageBox.information(self, "Długość łuku", "Zaznacz dokładnie jeden łuk kołowy lub eliptyczny.")
            return
        result = self._prompt_length("Długość łuku", "Długość sterująca", arcs[0].length)
        if result is not None:
            value, expression = result
            constraint = SketchConstraint(ConstraintType.ARC_LENGTH, (GeometryReference(arcs[0].id),), value=value)
            self._execute_constraint(bind_constraint_expression(self.document, self.document.active_sketch_id, constraint, expression))

    def _constrain_rectangle_dimensions(self) -> None:
        rectangles = [entity for entity in self._selected_entities() if isinstance(entity, RectangleEntity)]
        if not rectangles:
            QMessageBox.information(self, "Wymiary prostokąta", "Zaznacz co najmniej jeden prostokąt.")
            return
        with self.command_stack.transaction("Zwiąż wymiary prostokąta"):
            for rectangle in rectangles:
                constraints = (
                    SketchConstraint(
                        ConstraintType.DISTANCE_X,
                        (GeometryReference(rectangle.id, "corner-0"), GeometryReference(rectangle.id, "corner-1")),
                        value=rectangle.width,
                        name=f"Szerokość {rectangle.id[:8]}",
                    ),
                    SketchConstraint(
                        ConstraintType.DISTANCE_Y,
                        (GeometryReference(rectangle.id, "corner-0"), GeometryReference(rectangle.id, "corner-3")),
                        value=rectangle.height,
                        name=f"Wysokość {rectangle.id[:8]}",
                    ),
                )
                for constraint in constraints:
                    self.command_stack.execute(AddConstraintCommand(self.document.active_sketch_id, constraint))

    @staticmethod
    def _center_reference(entity: SketchEntity) -> GeometryReference | None:
        if isinstance(entity, (RectangleEntity, CircleEntity, ArcEntity, EllipseEntity, EllipticalArcEntity, RegularPolygonEntity, SlotEntity)):
            return GeometryReference(entity.id, "center")
        if isinstance(entity, PointEntity):
            return GeometryReference(entity.id, "point")
        if isinstance(entity, LineEntity):
            return GeometryReference(entity.id, "midpoint")
        return None

    def _constrain_current_center(self) -> None:
        entities = self._selected_entities()
        if not entities:
            QMessageBox.information(self, "Położenie środka", "Zaznacz geometrię.")
            return
        with self.command_stack.transaction("Zwiąż położenie środka"):
            for entity in entities:
                reference = self._center_reference(entity)
                if reference is None:
                    continue
                if isinstance(entity, (RectangleEntity, CircleEntity, ArcEntity, EllipseEntity, EllipticalArcEntity, RegularPolygonEntity, SlotEntity)):
                    center = entity.center
                elif isinstance(entity, PointEntity):
                    center = entity.point
                else:
                    center = Point2D((entity.start.x + entity.end.x) / 2, (entity.start.y + entity.end.y) / 2)
                self.command_stack.execute(AddConstraintCommand(
                    self.document.active_sketch_id,
                    SketchConstraint(ConstraintType.X_COORDINATE, (reference,), value=center.x, name=f"Położenie X {entity.id[:8]}"),
                ))
                self.command_stack.execute(AddConstraintCommand(
                    self.document.active_sketch_id,
                    SketchConstraint(ConstraintType.Y_COORDINATE, (reference,), value=center.y, name=f"Położenie Y {entity.id[:8]}"),
                ))

    def _center_at_origin(self) -> None:
        entities = self._selected_entities()
        if not entities:
            QMessageBox.information(self, "Wyśrodkuj", "Zaznacz geometrię.")
            return
        with self.command_stack.transaction("Wyśrodkuj w początku"):
            for entity in entities:
                reference = self._center_reference(entity)
                if reference is None:
                    continue
                self.command_stack.execute(AddConstraintCommand(
                    self.document.active_sketch_id,
                    SketchConstraint(ConstraintType.X_COORDINATE, (reference,), value=0.0, name=f"Środek X {entity.id[:8]}"),
                ))
                self.command_stack.execute(AddConstraintCommand(
                    self.document.active_sketch_id,
                    SketchConstraint(ConstraintType.Y_COORDINATE, (reference,), value=0.0, name=f"Środek Y {entity.id[:8]}"),
                ))

    def _add_linear_pattern(self) -> None:
        circles = [entity for entity in self._selected_entities() if isinstance(entity, CircleEntity)]
        if len(circles) != 1:
            QMessageBox.information(self, "Szyk liniowy", "Zaznacz dokładnie jeden okrąg źródłowy.")
            return
        dialog = LinearPatternDialog(parameters=self.document.parameter_values(self.document.active_sketch_id), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            count, spacing_x, spacing_y = dialog.values()
            build = create_linear_circle_pattern(
                self.document.active_sketch,
                circles[0].id,
                count,
                spacing_x,
                spacing_y,
            )
            self.command_stack.execute(AddPatternCommand(self.document.active_sketch_id, build))
            self.selected_pattern_id = build.pattern.id
            self.selected_constraint_id = None
            self.selection_model.set((circles[0].id, *build.pattern.generated_entity_ids))
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nie można utworzyć szyku", str(exc))

    def _edit_pattern(self, pattern_id: str) -> None:
        pattern = self.document.active_sketch.patterns.get(pattern_id)
        if pattern is None:
            return
        dialog = LinearPatternDialog(
            pattern.count,
            pattern.spacing_x,
            pattern.spacing_y,
            parameters=self.document.parameter_values(self.document.active_sketch_id),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            count, spacing_x, spacing_y = dialog.values()
            self.command_stack.execute(EditLinearPatternCommand(
                self.document.active_sketch_id,
                pattern.id,
                count,
                spacing_x,
                spacing_y,
                merge_key=f"pattern:{pattern.id}",
            ))
            updated = self.document.active_sketch.patterns[pattern.id]
            self.selection_model.set((updated.source_entity_id, *updated.generated_entity_ids))
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nie można zmienić szyku", str(exc))

    def _delete_selected_pattern(self) -> None:
        pattern_id = self.selected_pattern_id
        if not pattern_id or pattern_id not in self.document.active_sketch.patterns:
            QMessageBox.information(self, "Szyki", "Wybierz szyk w drzewie modelu.")
            return
        self.command_stack.execute(RemovePatternCommand(self.document.active_sketch_id, pattern_id))
        self.selected_pattern_id = None
        self.selection_model.clear()

    def _add_fix_constraint(self) -> None:
        entities = self._selected_entities()
        if not entities:
            QMessageBox.information(self, "Blokada", "Zaznacz geometrię do zablokowania.")
            return
        with self.command_stack.transaction("Zablokuj geometrię"):
            for entity in entities:
                self.command_stack.execute(AddConstraintCommand(
                    self.document.active_sketch_id,
                    fixed_constraint(self.document.active_sketch, entity.id),
                ))

    def _toggle_selected_constraint(self) -> None:
        constraint = self.document.active_sketch.constraints.get(self.selected_constraint_id or "")
        if constraint is None:
            QMessageBox.information(self, "Więzy", "Wybierz więz w drzewie modelu.")
            return
        self.command_stack.execute(SetConstraintEnabledCommand.create(
            self.document.active_sketch_id,
            constraint,
            not constraint.enabled,
        ))

    def _delete_selected_constraint(self) -> None:
        constraint_id = self.selected_constraint_id
        if not constraint_id or constraint_id not in self.document.active_sketch.constraints:
            QMessageBox.information(self, "Więzy", "Wybierz więz w drzewie modelu.")
            return
        self.command_stack.execute(RemoveConstraintsCommand(self.document.active_sketch_id, (constraint_id,)))
        self.selected_constraint_id = None

    def _edit_constraint(self, constraint_id: str) -> None:
        constraint = self.document.active_sketch.constraints.get(constraint_id)
        if constraint is None or constraint.value is None:
            return
        result = self._prompt_length("Edytuj więz", constraint_label(constraint), constraint.value, constraint.expression)
        if result is None:
            return
        value, expression = result
        replacement = bind_constraint_expression(
            self.document,
            self.document.active_sketch_id,
            replace(constraint, value=value),
            expression,
        )
        self.command_stack.execute(ReplaceConstraintCommand(
            self.document.active_sketch_id,
            replacement,
            merge_key=f"constraint:{constraint.id}",
        ))

    def _open_parameter_manager(self) -> None:
        ParameterManagerDialog(self.document, self.command_stack, self).exec()
        self._refresh_side_panels()

    def _edit_selected_parameter(self) -> None:
        parameter = self.document.parameters.get(self.selected_parameter_id or "")
        if parameter is None:
            return
        editor = ParameterEditorDialog(self.document, parameter, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        name, expression, _scope = editor.values()
        try:
            self.command_stack.execute(EditParameterCommand(parameter.id, name, expression))
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Nie można zmienić parametru", str(exc))

    def _delete_selected_parameter(self) -> None:
        parameter_id = self.selected_parameter_id or ""
        if not parameter_id:
            return
        try:
            self.command_stack.execute(RemoveParameterCommand(parameter_id))
            self.selected_parameter_id = None
        except CadValidationError as exc:
            QMessageBox.warning(self, "Nie można usunąć parametru", str(exc))

    def _delete_current_selection(self) -> None:
        if self.selected_parameter_id:
            self._delete_selected_parameter()
        elif self.selected_pattern_id:
            self._delete_selected_pattern()
        elif self.selected_constraint_id:
            self._delete_selected_constraint()
        else:
            self.canvas.delete_selected()

    def _select_profile_issue(self, issue: ProfileIssue) -> None:
        self.selected_constraint_id = None
        self.selected_pattern_id = None
        self.selection_model.set(entity_id for entity_id in issue.entity_ids if entity_id in self.document.active_sketch.entities)
        if issue.entity_ids:
            self.status_label.setText(issue.message)

    def _apply_profile_repair(
        self,
        report: ProfileReport,
        *,
        remove_duplicates: bool,
        remove_micro_segments: bool,
        close_small_gaps: bool,
    ) -> ProfileReport:
        plan = build_repair_plan(
            self.document.active_sketch,
            report,
            remove_duplicates=remove_duplicates,
            remove_micro_segments=remove_micro_segments,
            close_small_gaps=close_small_gaps,
        )
        if plan.has_repairs:
            self.command_stack.execute(RepairProfileCommand(self.document.active_sketch_id, plan))
            self.selection_model.clear()
        return analyze_profile(self.document.active_sketch, ProfileTolerance())

    def _check_and_repair_profile(self) -> None:
        report = analyze_profile(self.document.active_sketch, ProfileTolerance())
        dialog = ProfileDiagnosticsDialog(report, self._select_profile_issue, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        remove_duplicates, remove_micro_segments, close_small_gaps = dialog.repair_options()
        repaired = self._apply_profile_repair(
            report,
            remove_duplicates=remove_duplicates,
            remove_micro_segments=remove_micro_segments,
            close_small_gaps=close_small_gaps,
        )
        if repaired.is_valid_surface:
            QMessageBox.information(
                self,
                "Sprawdź i napraw szkic",
                f"Profil jest poprawny. Zamknięte pętle: {len(repaired.loops)}.",
            )
        else:
            QMessageBox.warning(
                self,
                "Profil nadal wymaga naprawy",
                f"Pozostałe błędy: {repaired.error_count}. Otwarte końce: {repaired.open_endpoint_count}. "
                "Kliknij ponownie „Sprawdź profil”, aby zobaczyć szczegóły.",
            )

    def _tree_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        value = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if value.startswith("constraint:"):
            self._edit_constraint(value.split(":", 1)[1])
        elif value.startswith("pattern:"):
            self._edit_pattern(value.split(":", 1)[1])
        elif value.startswith("parameter:"):
            self.selected_parameter_id = value.split(":", 1)[1]
            self._edit_selected_parameter()
        elif value:
            self._edit_entity(value)

    def _refresh_side_panels(self) -> None:
        top_item = self.model_tree.topLevelItem(0)
        expanded = top_item.isExpanded() if top_item is not None else True
        sketch = self.document.active_sketch
        self.model_tree.blockSignals(True)
        self.model_tree.clear()
        root = QTreeWidgetItem([self.document.label])
        root.setData(0, Qt.ItemDataRole.UserRole, "")
        sketch_item = QTreeWidgetItem([sketch.label])
        sketch_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root.addChild(sketch_item)
        parameters_item = QTreeWidgetItem([f"Parametry ({len(self.document.parameters)})"])
        parameters_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root.addChild(parameters_item)
        for parameter in self.document.ordered_parameters():
            scope_suffix = "" if not parameter.scope_object_id else f" · {self.document.sketches[parameter.scope_object_id].label}"
            item = QTreeWidgetItem([f"{parameter.name} = {parameter.value:g} mm{scope_suffix}"])
            item.setData(0, Qt.ItemDataRole.UserRole, f"parameter:{parameter.id}")
            item.setForeground(0, QBrush(QColor("#8ec5ff")))
            item.setSelected(parameter.id == self.selected_parameter_id)
            parameters_item.addChild(item)
        selected = set(self.selection_model.selected_ids)
        geometry_item = QTreeWidgetItem([f"Geometria ({len(sketch.entities)})"])
        geometry_item.setData(0, Qt.ItemDataRole.UserRole, "")
        sketch_item.addChild(geometry_item)
        for entity in sketch.ordered_entities():
            type_name = (
                "Punkt" if isinstance(entity, PointEntity)
                else "Linia" if isinstance(entity, LineEntity)
                else "Prostokąt" if isinstance(entity, RectangleEntity)
                else "Okrąg" if isinstance(entity, CircleEntity)
                else "Łuk" if isinstance(entity, ArcEntity)
                else "Elipsa" if isinstance(entity, EllipseEntity)
                else "Łuk elipsy" if isinstance(entity, EllipticalArcEntity)
                else "B-spline" if isinstance(entity, BSplineEntity)
                else "Polilinia" if isinstance(entity, PolylineEntity)
                else "Wielokąt foremny" if isinstance(entity, RegularPolygonEntity)
                else "Szczelina"
            )
            item = QTreeWidgetItem([f"{type_name}  {entity.id[:8]}"])
            item.setData(0, Qt.ItemDataRole.UserRole, entity.id)
            geometry_item.addChild(item)
            item.setSelected(entity.id in selected)
        patterns_item = QTreeWidgetItem([f"Szyki ({len(sketch.patterns)})"])
        patterns_item.setData(0, Qt.ItemDataRole.UserRole, "")
        sketch_item.addChild(patterns_item)
        for pattern in sketch.ordered_patterns():
            item = QTreeWidgetItem([f"{pattern.label}: {pattern.count} × ({pattern.spacing_x:g}, {pattern.spacing_y:g}) mm"])
            item.setData(0, Qt.ItemDataRole.UserRole, f"pattern:{pattern.id}")
            item.setForeground(0, QBrush(QColor("#ff657a" if pattern.error else "#9b8cff")))
            patterns_item.addChild(item)
            item.setSelected(pattern.id == self.selected_pattern_id)
        constraints_item = QTreeWidgetItem([f"Więzy ({len(sketch.constraints)})"])
        constraints_item.setData(0, Qt.ItemDataRole.UserRole, "")
        sketch_item.addChild(constraints_item)
        for constraint in sketch.ordered_constraints():
            prefix = "○ " if not constraint.enabled else ""
            item = QTreeWidgetItem([prefix + constraint_label(constraint)])
            item.setData(0, Qt.ItemDataRole.UserRole, f"constraint:{constraint.id}")
            if constraint.status in {ConstraintStatus.CONFLICTING, ConstraintStatus.BROKEN_REFERENCE}:
                item.setForeground(0, QBrush(QColor("#ff657a")))
            elif constraint.status == ConstraintStatus.REDUNDANT:
                item.setForeground(0, QBrush(QColor("#ffbd69")))
            elif constraint.status in {ConstraintStatus.DISABLED, ConstraintStatus.REFERENCE}:
                item.setForeground(0, QBrush(QColor("#8290a8")))
            else:
                item.setForeground(0, QBrush(QColor("#76e0a6")))
            constraints_item.addChild(item)
            item.setSelected(constraint.id == self.selected_constraint_id)
        self.model_tree.addTopLevelItem(root)
        root.setExpanded(expanded)
        parameters_item.setExpanded(True)
        sketch_item.setExpanded(True)
        geometry_item.setExpanded(True)
        patterns_item.setExpanded(True)
        constraints_item.setExpanded(True)
        self.model_tree.blockSignals(False)
        self.selection_status.setText(f"Zaznaczenie: {len(selected)}")
        status_names = {
            SketchSolveStatus.UNDER_CONSTRAINED: "niedowiązany",
            SketchSolveStatus.FULLY_CONSTRAINED: "w pełni związany",
            SketchSolveStatus.REDUNDANT: "redundantne więzy",
            SketchSolveStatus.CONFLICTING: "sprzeczne więzy",
            SketchSolveStatus.UNSOLVABLE: "nierozwiązywalny",
        }
        self.solver_status.setText(f"DoF: {sketch.degrees_of_freedom} · {status_names[sketch.solve_status]}")
        selected_constraint = sketch.constraints.get(self.selected_constraint_id or "")
        selected_pattern = sketch.patterns.get(self.selected_pattern_id or "")
        selected_parameter = self.document.parameters.get(self.selected_parameter_id or "")
        if selected_parameter is not None:
            scope = "globalny" if not selected_parameter.scope_object_id else self.document.sketches[selected_parameter.scope_object_id].label
            dependencies = ", ".join(binding.alias for binding in selected_parameter.bindings) or "brak"
            self.property_title.setText(selected_parameter.name)
            self.property_details.setText(
                f"Wyrażenie: {selected_parameter.expression}\n"
                f"Wartość: {selected_parameter.value:g} mm\n"
                f"Zakres: {scope}\n"
                f"Zależności: {dependencies}\n"
                f"UUID: {selected_parameter.id}"
            )
        elif selected_pattern is not None:
            self.property_title.setText(selected_pattern.label)
            self.property_details.setText(
                f"Liczba elementów: {selected_pattern.count}\n"
                f"Krok X: {selected_pattern.spacing_x:g} mm\n"
                f"Krok Y: {selected_pattern.spacing_y:g} mm\n"
                f"UUID: {selected_pattern.id}"
                + (f"\nBłąd: {selected_pattern.error}" if selected_pattern.error else "")
            )
        elif selected_constraint is not None:
            self.property_title.setText(constraint_label(selected_constraint))
            details = [
                f"Status: {selected_constraint.status.value}",
                f"Sterujący: {'tak' if selected_constraint.driving else 'referencyjny'}",
                f"Włączony: {'tak' if selected_constraint.enabled else 'nie'}",
                f"UUID: {selected_constraint.id}",
            ]
            if selected_constraint.expression:
                details.insert(0, f"Wyrażenie: {selected_constraint.expression} = {selected_constraint.value:g} mm")
            if selected_constraint.diagnostic:
                details.insert(1, selected_constraint.diagnostic)
            self.property_details.setText("\n".join(details))
        elif len(selected) == 1:
            entity = sketch.entities[next(iter(selected))]
            if isinstance(entity, PointEntity):
                self.property_title.setText("Punkt")
                self.property_details.setText(f"X: {entity.point.x:.3f} mm\nY: {entity.point.y:.3f} mm\nUUID: {entity.id}")
            elif isinstance(entity, LineEntity):
                self.property_title.setText("Linia")
                self.property_details.setText(f"Długość: {entity.length:.3f} mm\nKąt: {entity.angle_deg:.3f}°\nUUID: {entity.id}")
            elif isinstance(entity, RectangleEntity):
                self.property_title.setText("Prostokąt")
                self.property_details.setText(f"Szerokość: {entity.width:.3f} mm\nWysokość: {entity.height:.3f} mm\nUUID: {entity.id}")
            elif isinstance(entity, CircleEntity):
                self.property_title.setText("Okrąg")
                self.property_details.setText(f"Promień: {entity.radius:.3f} mm\nŚrednica: {entity.radius * 2:.3f} mm\nUUID: {entity.id}")
            elif isinstance(entity, ArcEntity):
                self.property_title.setText("Łuk kołowy")
                self.property_details.setText(
                    f"Promień: {entity.radius:.3f} mm\n"
                    f"Kąt początkowy: {entity.start_angle_deg:.3f}°\n"
                    f"Rozwarcie: {entity.sweep_angle_deg:.3f}°\n"
                    f"Długość: {entity.length:.3f} mm\nUUID: {entity.id}"
                )
            elif isinstance(entity, EllipseEntity):
                self.property_title.setText("Elipsa")
                self.property_details.setText(
                    f"Promień główny: {entity.major_radius:.3f} mm\n"
                    f"Promień pomocniczy: {entity.minor_radius:.3f} mm\n"
                    f"Obrót: {entity.rotation_deg:.3f}°\n"
                    f"Pole: {entity.area:.3f} mm²\n"
                    f"Obwód: {entity.circumference:.3f} mm\nUUID: {entity.id}"
                )
            elif isinstance(entity, EllipticalArcEntity):
                self.property_title.setText("Łuk elipsy")
                self.property_details.setText(
                    f"Promień główny: {entity.major_radius:.3f} mm\n"
                    f"Promień pomocniczy: {entity.minor_radius:.3f} mm\n"
                    f"Obrót: {entity.rotation_deg:.3f}°\n"
                    f"Początek: {entity.start_parameter_deg:.3f}°\n"
                    f"Rozwarcie: {entity.sweep_parameter_deg:.3f}°\n"
                    f"Długość: {entity.length:.3f} mm\nUUID: {entity.id}"
                )
            elif isinstance(entity, PolylineEntity):
                arc_count = sum(isinstance(segment, ArcEntity) for segment in entity.segment_entities)
                self.property_title.setText("Polilinia")
                self.property_details.setText(
                    f"Wierzchołki: {len(entity.points)}\n"
                    f"Segmenty: {len(entity.segments)}\n"
                    f"Segmenty łukowe: {arc_count}\n"
                    f"Zamknięta: {'tak' if entity.closed else 'nie'}\n"
                    f"Długość: {entity.length:.3f} mm\nUUID: {entity.id}"
                )
            elif isinstance(entity, BSplineEntity):
                self.property_title.setText("Krzywa B-spline")
                self.property_details.setText(
                    f"Stopień: {entity.degree}\n"
                    f"Punkty kontrolne: {len(entity.control_points)}\n"
                    f"Węzły: {len(entity.knots)}\n"
                    f"Racjonalna: {'tak' if entity.rational else 'nie'}\n"
                    f"Zamknięta: {'tak' if entity.closed else 'nie'}\n"
                    f"Długość: {entity.length:.3f} mm\nUUID: {entity.id}"
                )
            elif isinstance(entity, RegularPolygonEntity):
                self.property_title.setText("Wielokąt foremny")
                self.property_details.setText(
                    f"Boki: {entity.sides}\n"
                    f"Promień opisany: {entity.radius:.3f} mm\n"
                    f"Długość boku: {entity.side_length:.3f} mm\n"
                    f"Obrót: {entity.rotation_deg:.3f}°\n"
                    f"Pole: {entity.area:.3f} mm²\nUUID: {entity.id}"
                )
            elif isinstance(entity, SlotEntity):
                self.property_title.setText("Szczelina")
                self.property_details.setText(
                    f"Długość osi: {entity.axis_length:.3f} mm\n"
                    f"Szerokość: {entity.width:.3f} mm\n"
                    f"Kąt: {entity.angle_deg:.3f}°\n"
                    f"Pole: {entity.area:.3f} mm²\nUUID: {entity.id}"
                )
        elif selected:
            self.property_title.setText(f"{len(selected)} elementy")
            self.property_details.setText("Delete usuwa zaznaczenie jako jedną operację undo.")
        else:
            self.property_title.setText("Brak zaznaczenia")
            self.property_details.setText("Zaznacz geometrię w widoku lub drzewie.")

    def _tree_selection_changed(self) -> None:
        values = [str(item.data(0, Qt.ItemDataRole.UserRole) or "") for item in self.model_tree.selectedItems()]
        constraint_values = [value for value in values if value.startswith("constraint:")]
        pattern_values = [value for value in values if value.startswith("pattern:")]
        parameter_values = [value for value in values if value.startswith("parameter:")]
        if parameter_values:
            self.selected_parameter_id = parameter_values[0].split(":", 1)[1]
            self.selected_pattern_id = None
            self.selected_constraint_id = None
            self.selection_model.clear()
            self._refresh_side_panels()
            return
        if pattern_values:
            self.selected_pattern_id = pattern_values[0].split(":", 1)[1]
            self.selected_constraint_id = None
            self.selected_parameter_id = None
            pattern = self.document.active_sketch.patterns.get(self.selected_pattern_id)
            self.selection_model.set((pattern.source_entity_id, *pattern.generated_entity_ids)) if pattern else self.selection_model.clear()
            self._refresh_side_panels()
            return
        if constraint_values:
            self.selected_constraint_id = constraint_values[0].split(":", 1)[1]
            self.selected_pattern_id = None
            self.selected_parameter_id = None
            constraint = self.document.active_sketch.constraints.get(self.selected_constraint_id)
            self.selection_model.set(reference.entity_id for reference in constraint.references) if constraint else self.selection_model.clear()
            self._refresh_side_panels()
            return
        self.selected_constraint_id = None
        self.selected_pattern_id = None
        self.selected_parameter_id = None
        self.selection_model.set(value for value in values if value)

    def _clear_constraint_selection_from_canvas(self) -> None:
        if self.canvas._selection_guard:
            return
        if self.selected_constraint_id is not None or self.selected_pattern_id is not None or self.selected_parameter_id is not None:
            self.selected_constraint_id = None
            self.selected_pattern_id = None
            self.selected_parameter_id = None
            self._refresh_side_panels()

    def _apply_board_size(self) -> None:
        self.canvas.set_board_size(self.width_input.value(), self.height_input.value())

    def _undo(self) -> None:
        self.command_stack.undo()

    def _redo(self) -> None:
        self.command_stack.redo()

    def _set_document(self, document: CadDocument, path: Path | None = None) -> None:
        self.document = document
        self.command_stack = CommandStack(document)
        self.selection_model = SelectionModel()
        self.selected_constraint_id = None
        self.selected_pattern_id = None
        self.selected_parameter_id = None
        self.canvas.bind(document, self.command_stack, self.selection_model)
        self.canvas.creation_requested.disconnect(self._create_geometry)
        self.canvas.entity_edit_requested.disconnect(self._edit_entity)
        self.canvas.creation_requested.connect(self._create_geometry)
        self.canvas.entity_edit_requested.connect(self._edit_entity)
        self.selection_model.subscribe(lambda _ids: self._refresh_side_panels())
        self.command_stack.subscribe(lambda _ids: self._refresh_side_panels())
        self.project_path = path
        self.width_input.setValue(self.canvas.board_width)
        self.height_input.setValue(self.canvas.board_height)
        self._refresh_side_panels()
        self._update_title()

    def _new_document(self) -> None:
        self._set_document(CadDocument.create())

    def _open_document(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Otwórz projekt CAD", "", "Projekt SIEKACZ CAD (*.siekcad)")
        if not path:
            return
        try:
            self._set_document(load_document(path), Path(path))
            self.status_label.setText(f"Otwarto {Path(path).name}")
        except CadIoError as exc:
            QMessageBox.warning(self, "Nie można otworzyć projektu CAD", str(exc))

    def _open_inspection_viewer(self) -> None:
        from app.cad_viewer import CadInspectionDialog

        CadInspectionDialog(self).exec()

    def _save_document(self) -> None:
        if self.project_path is None:
            self._save_document_as()
            return
        try:
            self.project_path = save_document(self.document, self.project_path)
            self.status_label.setText(f"Zapisano {self.project_path.name}")
            self._update_title()
        except CadIoError as exc:
            QMessageBox.warning(self, "Nie można zapisać projektu CAD", str(exc))

    def _save_document_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz projekt CAD", "projekt.siekcad", "Projekt SIEKACZ CAD (*.siekcad)")
        if not path:
            return
        self.project_path = Path(path)
        self._save_document()

    def _update_title(self) -> None:
        suffix = f" — {self.project_path.name}" if self.project_path else ""
        self.setWindowTitle(f"SIEKACZ CAD — szkic 2D{suffix}")

    def _accept_drawing(self) -> None:
        bounds = self.document.active_sketch.bounds()
        if bounds is None:
            QMessageBox.information(self, "SIEKACZ CAD", "Najpierw narysuj lub wczytaj geometrię.")
            return
        left, bottom, right, top = bounds
        self.result_dimensions = (round(right - left, 3), round(top - bottom, 3))
        self.accept()

    def _manage_layers(self) -> None:
        dialog = LayerManagerDialog(self.document.layers, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.document.layers = dialog.values()
            self.document.layers.setdefault("0", CadLayer("0"))
            self.canvas.sync_from_model()
            self.status_label.setText(f"Zaktualizowano {len(self.document.layers)} warstw")
        except (CadValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Warstwy CAD / DXF", f"Nie można zastosować ustawień warstw.\n{exc}")

    def _show_dxf_import_report(self) -> None:
        report = getattr(self, "last_dxf_import_report", None)
        if report is None:
            QMessageBox.information(self, "Raport importu DXF", "W tej sesji nie wykonano jeszcze importu DXF.")
            return
        details = [
            report.summary(),
            f"Otwarte kontury: {report.open_contours}",
            f"Kandydaci na duplikaty: {report.duplicate_candidates}",
            f"Mikrogeometria: {report.micro_geometry}",
        ]
        if report.unsupported:
            details.append("Nieobsługiwane: " + ", ".join(
                f"{kind}: {count}" for kind, count in sorted(report.unsupported.items())
            ))
        details.extend(report.warnings)
        QMessageBox.information(self, "Raport importu DXF", "\n".join(details))

    def _import_dxf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importuj DXF", "", "Plik DXF (*.dxf)")
        if not path:
            return
        try:
            from cad.dxf_io import import_dxf

            result = import_dxf(path)
            if not result.entities:
                raise CadValidationError("Importowany plik nie zawiera obsługiwanej geometrii.")
            existing = tuple(self.document.active_sketch.order)
            with self.command_stack.transaction(f"Importuj DXF {Path(path).name}"):
                if existing:
                    self.command_stack.execute(RemoveEntitiesCommand(self.document.active_sketch_id, existing))
                self.command_stack.execute(AddEntitiesCommand(self.document.active_sketch_id, result.entities))
            self.document.layers = dict(result.layers)
            self.last_dxf_import_report = result.report
            self.status_label.setText(result.report.summary())
            self.canvas.fit_board()
            return
        except Exception as exc:
            QMessageBox.warning(self, "Import DXF", f"Nie udało się wczytać geometrii.\n{exc}")
            return
        try:
            import ezdxf

            dxf = ezdxf.readfile(path)
            entities: list[SketchEntity] = []
            unsupported: dict[str, int] = {}
            for source in dxf.modelspace():
                kind = source.dxftype()
                layer = str(source.dxf.layer)
                if kind == "LINE":
                    start, end = source.dxf.start, source.dxf.end
                    entities.append(LineEntity(Point2D(start.x, start.y), Point2D(end.x, end.y), layer=layer))
                elif kind == "POINT":
                    location = source.dxf.location
                    entities.append(PointEntity(Point2D(location.x, location.y), layer=layer))
                elif kind == "CIRCLE":
                    center = source.dxf.center
                    entities.append(CircleEntity(Point2D(center.x, center.y), float(source.dxf.radius), layer=layer))
                elif kind == "ARC":
                    center = source.dxf.center
                    start_angle = float(source.dxf.start_angle)
                    end_angle = float(source.dxf.end_angle)
                    sweep = (end_angle - start_angle) % 360.0
                    entities.append(ArcEntity(
                        Point2D(center.x, center.y),
                        float(source.dxf.radius),
                        start_angle,
                        sweep or 360.0,
                        layer=layer,
                    ))
                elif kind == "ELLIPSE":
                    center, major_axis = source.dxf.center, source.dxf.major_axis
                    major_radius = math.hypot(float(major_axis.x), float(major_axis.y))
                    minor_radius = major_radius * abs(float(source.dxf.ratio))
                    rotation = math.degrees(math.atan2(float(major_axis.y), float(major_axis.x)))
                    start_parameter = math.degrees(float(source.dxf.start_param))
                    raw_sweep = math.degrees(float(source.dxf.end_param) - float(source.dxf.start_param))
                    if abs(abs(raw_sweep) - 360.0) <= 1e-7 or abs(raw_sweep) <= 1e-7:
                        entities.append(EllipseEntity(Point2D(center.x, center.y), major_radius, minor_radius, rotation, layer=layer))
                    else:
                        sweep = raw_sweep % 360.0
                        entities.append(EllipticalArcEntity(
                            Point2D(center.x, center.y),
                            major_radius,
                            minor_radius,
                            rotation,
                            start_parameter,
                            sweep,
                            layer=layer,
                        ))
                elif kind == "SPLINE":
                    control_points = list(source.control_points)
                    knots = tuple(float(value) for value in source.knots)
                    weights = tuple(float(value) for value in source.weights)
                    degree = int(source.dxf.degree)
                    if not control_points:
                        fit_points = list(source.fit_points)
                        if len(fit_points) < 2:
                            unsupported["SPLINE bez definicji krzywej"] = unsupported.get("SPLINE bez definicji krzywej", 0) + 1
                            continue
                        from ezdxf.math import global_bspline_interpolation

                        construction = global_bspline_interpolation(fit_points, degree=min(degree, len(fit_points) - 1))
                        control_points = list(construction.control_points)
                        knots = tuple(float(value) for value in construction.knots())
                        weights = tuple(float(value) for value in construction.weights())
                        degree = int(construction.degree)
                    flags = int(source.dxf.flags)
                    entities.append(BSplineEntity(
                        tuple(Point2D(float(point[0]), float(point[1])) for point in control_points),
                        degree,
                        knots,
                        weights,
                        bool(flags & 1),
                        bool(flags & 2),
                        layer=layer,
                    ))
                elif kind == "LWPOLYLINE":
                    raw = list(source.get_points("xyb"))
                    points = tuple(Point2D(float(point[0]), float(point[1])) for point in raw)
                    closed = bool(source.closed)
                    segment_count = len(points) if closed else max(0, len(points) - 1)
                    bulges = tuple(float(raw[index][2]) for index in range(segment_count))
                    entities.append(PolylineEntity(points, closed, bulges, layer=layer))
                elif kind == "POLYLINE" and source.is_2d_polyline:
                    vertices = list(source.vertices)
                    points = tuple(Point2D(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in vertices)
                    closed = bool(source.is_closed)
                    segment_count = len(points) if closed else max(0, len(points) - 1)
                    bulges = tuple(float(vertices[index].dxf.get("bulge", 0.0)) for index in range(segment_count))
                    entities.append(PolylineEntity(points, closed, bulges, layer=layer))
                else:
                    unsupported[kind] = unsupported.get(kind, 0) + 1
            if not entities:
                raise CadValidationError("Importowany plik nie zawiera obsługiwanej geometrii.")
            existing = tuple(self.document.active_sketch.order)
            with self.command_stack.transaction(f"Importuj DXF {Path(path).name}"):
                if existing:
                    self.command_stack.execute(RemoveEntitiesCommand(self.document.active_sketch_id, existing))
                self.command_stack.execute(AddEntitiesCommand(self.document.active_sketch_id, tuple(entities)))
            details = ", ".join(f"{kind}: {count}" for kind, count in sorted(unsupported.items()))
            self.status_label.setText(f"Zaimportowano {len(entities)} encji" + (f"; pominięto {details}" if details else ""))
            self.canvas.fit_board()
        except Exception as exc:
            QMessageBox.warning(self, "Import DXF", f"Nie udało się wczytać geometrii.\n{exc}")

    def _export_dxf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Eksportuj DXF", "rysunek.dxf", "Plik DXF (*.dxf)")
        if not path:
            return
        try:
            from cad.dxf_io import export_dxf

            target = export_dxf(
                path,
                self.document.active_sketch.ordered_entities(),
                self.document.layers.values(),
            )
            self.status_label.setText(f"Wyeksportowano {target.name}")
            return
        except Exception as exc:
            QMessageBox.warning(self, "Eksport DXF", f"Nie udało się zapisać rysunku.\n{exc}")
            return
        try:
            import ezdxf

            target = Path(path).with_suffix(".dxf")
            dxf = ezdxf.new("R2010")
            dxf.header["$INSUNITS"] = 4
            modelspace = dxf.modelspace()
            known_layers = {"0"}
            for entity in self.document.active_sketch.ordered_entities():
                if entity.layer not in known_layers:
                    dxf.layers.add(entity.layer)
                    known_layers.add(entity.layer)
                attrs = {"layer": entity.layer}
                if isinstance(entity, PointEntity):
                    modelspace.add_point((entity.point.x, entity.point.y), dxfattribs=attrs)
                elif isinstance(entity, LineEntity):
                    modelspace.add_line((entity.start.x, entity.start.y), (entity.end.x, entity.end.y), dxfattribs=attrs)
                elif isinstance(entity, RectangleEntity):
                    modelspace.add_lwpolyline([(point.x, point.y) for point in entity.corners], close=True, dxfattribs=attrs)
                elif isinstance(entity, CircleEntity):
                    modelspace.add_circle((entity.center.x, entity.center.y), entity.radius, dxfattribs=attrs)
                elif isinstance(entity, ArcEntity):
                    if entity.sweep_angle_deg > 0:
                        start_angle = entity.start_angle_deg
                        end_angle = entity.start_angle_deg + entity.sweep_angle_deg
                    else:
                        start_angle = entity.start_angle_deg + entity.sweep_angle_deg
                        end_angle = entity.start_angle_deg
                    modelspace.add_arc(
                        (entity.center.x, entity.center.y),
                        entity.radius,
                        start_angle % 360.0,
                        end_angle % 360.0,
                        dxfattribs=attrs,
                    )
                elif isinstance(entity, EllipseEntity):
                    rotation = math.radians(entity.rotation_deg)
                    major_axis = (entity.major_radius * math.cos(rotation), entity.major_radius * math.sin(rotation))
                    modelspace.add_ellipse(
                        (entity.center.x, entity.center.y),
                        major_axis,
                        ratio=entity.minor_radius / entity.major_radius,
                        start_param=0.0,
                        end_param=math.tau,
                        dxfattribs=attrs,
                    )
                elif isinstance(entity, EllipticalArcEntity):
                    rotation = math.radians(entity.rotation_deg)
                    major_axis = (entity.major_radius * math.cos(rotation), entity.major_radius * math.sin(rotation))
                    if entity.sweep_parameter_deg > 0:
                        start_parameter = entity.start_parameter_deg
                        end_parameter = entity.start_parameter_deg + entity.sweep_parameter_deg
                    else:
                        start_parameter = entity.start_parameter_deg + entity.sweep_parameter_deg
                        end_parameter = entity.start_parameter_deg
                    modelspace.add_ellipse(
                        (entity.center.x, entity.center.y),
                        major_axis,
                        ratio=entity.minor_radius / entity.major_radius,
                        start_param=math.radians(start_parameter),
                        end_param=math.radians(end_parameter),
                        dxfattribs=attrs,
                    )
                elif isinstance(entity, BSplineEntity):
                    spline = modelspace.add_spline(degree=entity.degree, dxfattribs=attrs)
                    spline.control_points = [(point.x, point.y, 0.0) for point in entity.control_points]
                    spline.knots = entity.knots
                    if entity.rational:
                        spline.weights = entity.weights
                    spline.dxf.flags = (
                        (1 if entity.closed else 0)
                        | (2 if entity.periodic else 0)
                        | (4 if entity.rational else 0)
                        | 8
                    )
                elif isinstance(entity, PolylineEntity):
                    modelspace.add_lwpolyline(
                        [
                            (point.x, point.y, entity.bulges[index] if index < len(entity.bulges) else 0.0)
                            for index, point in enumerate(entity.points)
                        ],
                        format="xyb",
                        close=entity.closed,
                        dxfattribs=attrs,
                    )
                elif isinstance(entity, RegularPolygonEntity):
                    modelspace.add_lwpolyline(
                        [(point.x, point.y) for point in entity.points],
                        close=True,
                        dxfattribs=attrs,
                    )
                elif isinstance(entity, SlotEntity):
                    for line in entity.boundary_lines:
                        modelspace.add_line((line.start.x, line.start.y), (line.end.x, line.end.y), dxfattribs=attrs)
                    for arc in entity.boundary_arcs:
                        if arc.sweep_angle_deg > 0:
                            start_angle, end_angle = arc.start_angle_deg, arc.start_angle_deg + arc.sweep_angle_deg
                        else:
                            start_angle, end_angle = arc.start_angle_deg + arc.sweep_angle_deg, arc.start_angle_deg
                        modelspace.add_arc(
                            (arc.center.x, arc.center.y),
                            arc.radius,
                            start_angle % 360.0,
                            end_angle % 360.0,
                            dxfattribs=attrs,
                        )
            dxf.saveas(target)
            self.status_label.setText(f"Wyeksportowano {target.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Eksport DXF", f"Nie udało się zapisać rysunku.\n{exc}")


if __name__ == "__main__":
    app = QApplication([])
    dialog = TechnicalEditorDialog()
    dialog.show()
    raise SystemExit(app.exec())

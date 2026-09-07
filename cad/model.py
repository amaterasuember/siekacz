from __future__ import annotations

import math
import uuid
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Iterable, TypeAlias

from cad.constraints import SketchConstraint, SketchSolveStatus, constraint_from_dict
from cad.parameter_data import CadParameter
from cad.patterns import SketchPattern, pattern_from_dict


SCHEMA_VERSION = 4
MAX_ENTITIES_PER_SKETCH = 100_000
MAX_CONSTRAINTS_PER_SKETCH = 200_000
MAX_PATTERNS_PER_SKETCH = 10_000
GEOMETRIC_TOLERANCE_MM = 1e-7


class CadValidationError(ValueError):
    """A safe, user-actionable validation error for CAD data."""


def new_id() -> str:
    return str(uuid.uuid4())


def _validated_id(value: object, field_name: str = "id") -> str:
    text = str(value or "")
    try:
        uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise CadValidationError(f"Pole {field_name} nie zawiera prawidłowego UUID.") from exc
    return text


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise CadValidationError(f"Pole {field_name} musi być liczbą.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CadValidationError(f"Pole {field_name} musi być liczbą.") from exc
    if not math.isfinite(number):
        raise CadValidationError(f"Pole {field_name} musi być skończoną liczbą.")
    if abs(number) > 1e12:
        raise CadValidationError(f"Pole {field_name} przekracza bezpieczny zakres modelu.")
    return number


@dataclass(frozen=True, slots=True)
class Point2D:
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite(self.x, "x"))
        object.__setattr__(self, "y", _finite(self.y, "y"))

    def distance_to(self, other: "Point2D") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}

    @classmethod
    def from_dict(cls, data: object) -> "Point2D":
        if not isinstance(data, dict):
            raise CadValidationError("Punkt geometrii ma nieprawidłowy format.")
        return cls(_finite(data.get("x"), "x"), _finite(data.get("y"), "y"))


Bounds: TypeAlias = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class EntityStyle:
    """Portable entity appearance with lossless DXF override values."""

    color: int = 256
    true_color: int | None = None
    linetype: str = "BYLAYER"
    lineweight: int = -1
    source_handle: str = ""

    def __post_init__(self) -> None:
        color = int(self.color)
        if not 0 <= color <= 256:
            raise CadValidationError("Indeks koloru encji musi należeć do zakresu 0..256.")
        object.__setattr__(self, "color", color)
        if self.true_color is not None:
            true_color = int(self.true_color)
            if not 0 <= true_color <= 0xFFFFFF:
                raise CadValidationError("Kolor RGB encji ma nieprawidłową wartość.")
            object.__setattr__(self, "true_color", true_color)
        object.__setattr__(self, "linetype", str(self.linetype or "BYLAYER")[:128])
        object.__setattr__(self, "lineweight", int(self.lineweight))
        object.__setattr__(self, "source_handle", str(self.source_handle or "")[:128])

    def to_dict(self) -> dict[str, Any]:
        return {
            "color": self.color,
            "true_color": self.true_color,
            "linetype": self.linetype,
            "lineweight": self.lineweight,
            "source_handle": self.source_handle,
        }

    @classmethod
    def from_dict(cls, data: object) -> "EntityStyle":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise CadValidationError("Styl encji ma nieprawidłowy format.")
        return cls(
            data.get("color", 256), data.get("true_color"),
            data.get("linetype", "BYLAYER"), data.get("lineweight", -1),
            data.get("source_handle", ""),
        )


@dataclass(frozen=True, slots=True)
class CadLayer:
    name: str
    color: int = 7
    true_color: int | None = None
    linetype: str = "CONTINUOUS"
    visible: bool = True
    locked: bool = False

    def __post_init__(self) -> None:
        name = str(self.name).strip()[:128]
        if not name:
            raise CadValidationError("Warstwa musi mieć nazwę.")
        object.__setattr__(self, "name", name)
        color = abs(int(self.color))
        if not 1 <= color <= 255:
            raise CadValidationError("Indeks koloru warstwy musi należeć do zakresu 1..255.")
        object.__setattr__(self, "color", color)
        if self.true_color is not None:
            true_color = int(self.true_color)
            if not 0 <= true_color <= 0xFFFFFF:
                raise CadValidationError("Kolor RGB warstwy ma nieprawidłową wartość.")
            object.__setattr__(self, "true_color", true_color)
        object.__setattr__(self, "linetype", str(self.linetype or "CONTINUOUS")[:128])

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "color": self.color, "true_color": self.true_color,
            "linetype": self.linetype, "visible": self.visible, "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, data: object) -> "CadLayer":
        if not isinstance(data, dict):
            raise CadValidationError("Warstwa ma nieprawidłowy format.")
        return cls(
            data.get("name", ""), data.get("color", 7), data.get("true_color"),
            data.get("linetype", "CONTINUOUS"), bool(data.get("visible", True)),
            bool(data.get("locked", False)),
        )


@dataclass(frozen=True, slots=True)
class PointEntity:
    point: Point2D
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "point"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))

    def bounds(self) -> Bounds:
        return self.point.x, self.point.y, self.point.x, self.point.y

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "point": self.point.to_dict(),
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class LineEntity:
    start: Point2D
    end: Point2D
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "line"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        if self.start.distance_to(self.end) <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Linia musi mieć niezerową długość.")

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def angle_deg(self) -> float:
        return math.degrees(math.atan2(self.end.y - self.start.y, self.end.x - self.start.x))

    def bounds(self) -> Bounds:
        return (
            min(self.start.x, self.end.x),
            min(self.start.y, self.end.y),
            max(self.start.x, self.end.x),
            max(self.start.y, self.end.y),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RectangleEntity:
    origin: Point2D
    width: float
    height: float
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "rectangle"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        object.__setattr__(self, "width", _finite(self.width, "width"))
        object.__setattr__(self, "height", _finite(self.height, "height"))
        if self.width <= GEOMETRIC_TOLERANCE_MM or self.height <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Prostokąt musi mieć dodatnią szerokość i wysokość.")

    @property
    def corners(self) -> tuple[Point2D, Point2D, Point2D, Point2D]:
        x, y = self.origin.x, self.origin.y
        return (
            self.origin,
            Point2D(x + self.width, y),
            Point2D(x + self.width, y + self.height),
            Point2D(x, y + self.height),
        )

    @property
    def center(self) -> Point2D:
        return Point2D(self.origin.x + self.width / 2, self.origin.y + self.height / 2)

    def bounds(self) -> Bounds:
        return (self.origin.x, self.origin.y, self.origin.x + self.width, self.origin.y + self.height)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "origin": self.origin.to_dict(),
            "width": self.width,
            "height": self.height,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CircleEntity:
    center: Point2D
    radius: float
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "circle"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        object.__setattr__(self, "radius", _finite(self.radius, "radius"))
        if self.radius <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Okrąg musi mieć dodatni promień.")

    def bounds(self) -> Bounds:
        return (
            self.center.x - self.radius,
            self.center.y - self.radius,
            self.center.x + self.radius,
            self.center.y + self.radius,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "center": self.center.to_dict(),
            "radius": self.radius,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ArcEntity:
    """Analytic circular arc; angles are degrees and sweep may be clockwise."""

    center: Point2D
    radius: float
    start_angle_deg: float
    sweep_angle_deg: float
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "arc"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        object.__setattr__(self, "radius", _finite(self.radius, "radius"))
        object.__setattr__(self, "start_angle_deg", _finite(self.start_angle_deg, "start_angle_deg"))
        object.__setattr__(self, "sweep_angle_deg", _finite(self.sweep_angle_deg, "sweep_angle_deg"))
        if self.radius <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Łuk musi mieć dodatni promień.")
        if abs(self.sweep_angle_deg) <= 1e-9 or abs(self.sweep_angle_deg) >= 360.0 - 1e-9:
            raise CadValidationError("Kąt rozwarcia łuku musi być niezerowy i mniejszy niż 360°; pełny obwód utwórz jako okrąg.")

    def point_at(self, fraction: float) -> Point2D:
        angle = math.radians(self.start_angle_deg + self.sweep_angle_deg * float(fraction))
        return Point2D(self.center.x + self.radius * math.cos(angle), self.center.y + self.radius * math.sin(angle))

    @property
    def start(self) -> Point2D:
        return self.point_at(0.0)

    @property
    def end(self) -> Point2D:
        return self.point_at(1.0)

    @property
    def midpoint(self) -> Point2D:
        return self.point_at(0.5)

    @property
    def length(self) -> float:
        return self.radius * math.radians(abs(self.sweep_angle_deg))

    def contains_angle(self, angle_deg: float, tolerance_deg: float = 1e-9) -> bool:
        if abs(self.sweep_angle_deg) >= 360.0 - tolerance_deg:
            return True
        if self.sweep_angle_deg > 0:
            travelled = (angle_deg - self.start_angle_deg) % 360.0
        else:
            travelled = (self.start_angle_deg - angle_deg) % 360.0
        return travelled <= abs(self.sweep_angle_deg) + tolerance_deg

    def nearest_point(self, point: Point2D) -> Point2D:
        dx, dy = point.x - self.center.x, point.y - self.center.y
        if math.hypot(dx, dy) <= 1e-12:
            return self.start
        angle = math.degrees(math.atan2(dy, dx))
        radial = Point2D(
            self.center.x + self.radius * math.cos(math.radians(angle)),
            self.center.y + self.radius * math.sin(math.radians(angle)),
        )
        if self.contains_angle(angle):
            return radial
        return min((self.start, self.end), key=point.distance_to)

    def bounds(self) -> Bounds:
        points = [self.start, self.end]
        for angle in (0.0, 90.0, 180.0, 270.0):
            if self.contains_angle(angle):
                radians = math.radians(angle)
                points.append(Point2D(self.center.x + self.radius * math.cos(radians), self.center.y + self.radius * math.sin(radians)))
        return min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "center": self.center.to_dict(),
            "radius": self.radius,
            "start_angle_deg": self.start_angle_deg,
            "sweep_angle_deg": self.sweep_angle_deg,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


def _ellipse_point(center: Point2D, major_radius: float, minor_radius: float, rotation_deg: float, parameter_deg: float) -> Point2D:
    parameter = math.radians(parameter_deg)
    rotation = math.radians(rotation_deg)
    local_x, local_y = major_radius * math.cos(parameter), minor_radius * math.sin(parameter)
    return Point2D(
        center.x + local_x * math.cos(rotation) - local_y * math.sin(rotation),
        center.y + local_x * math.sin(rotation) + local_y * math.cos(rotation),
    )


def _ellipse_parameter_for_point(center: Point2D, major_radius: float, minor_radius: float, rotation_deg: float, point: Point2D) -> float:
    rotation = math.radians(rotation_deg)
    dx, dy = point.x - center.x, point.y - center.y
    local_x = dx * math.cos(rotation) + dy * math.sin(rotation)
    local_y = -dx * math.sin(rotation) + dy * math.cos(rotation)
    return math.degrees(math.atan2(local_y / minor_radius, local_x / major_radius))


def _nearest_ellipse_parameter(
    center: Point2D,
    major_radius: float,
    minor_radius: float,
    rotation_deg: float,
    point: Point2D,
) -> float:
    """Numerically stable nearest parameter; geometry remains analytic."""
    samples = 96
    parameter = min(
        (index * math.tau / samples for index in range(samples)),
        key=lambda value: point.distance_to(
            _ellipse_point(center, major_radius, minor_radius, rotation_deg, math.degrees(value))
        ),
    )
    rotation = math.radians(rotation_deg)
    dx, dy = point.x - center.x, point.y - center.y
    target_x = dx * math.cos(rotation) + dy * math.sin(rotation)
    target_y = -dx * math.sin(rotation) + dy * math.cos(rotation)
    for _ in range(20):
        sine, cosine = math.sin(parameter), math.cos(parameter)
        ellipse_x, ellipse_y = major_radius * cosine, minor_radius * sine
        derivative_x, derivative_y = -major_radius * sine, minor_radius * cosine
        second_x, second_y = -major_radius * cosine, -minor_radius * sine
        first = (ellipse_x - target_x) * derivative_x + (ellipse_y - target_y) * derivative_y
        second = derivative_x * derivative_x + derivative_y * derivative_y + (ellipse_x - target_x) * second_x + (ellipse_y - target_y) * second_y
        if abs(second) <= 1e-15:
            break
        step = max(-0.5, min(0.5, first / second))
        parameter -= step
        if abs(step) <= 1e-13:
            break
    return math.degrees(parameter) % 360.0


def _ellipse_arc_length(major_radius: float, minor_radius: float, start_deg: float, sweep_deg: float) -> float:
    count = max(64, int(math.ceil(abs(sweep_deg) * 2.0)))
    if count % 2:
        count += 1
    start, end = math.radians(start_deg), math.radians(start_deg + sweep_deg)
    step = (end - start) / count

    def speed(parameter: float) -> float:
        return math.hypot(major_radius * math.sin(parameter), minor_radius * math.cos(parameter))

    total = speed(start) + speed(end)
    total += 4.0 * math.fsum(speed(start + index * step) for index in range(1, count, 2))
    total += 2.0 * math.fsum(speed(start + index * step) for index in range(2, count, 2))
    return abs(step) * total / 3.0


@dataclass(frozen=True, slots=True)
class EllipseEntity:
    center: Point2D
    major_radius: float
    minor_radius: float
    rotation_deg: float = 0.0
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "ellipse"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        object.__setattr__(self, "major_radius", _finite(self.major_radius, "major_radius"))
        object.__setattr__(self, "minor_radius", _finite(self.minor_radius, "minor_radius"))
        object.__setattr__(self, "rotation_deg", _finite(self.rotation_deg, "rotation_deg"))
        if self.minor_radius <= GEOMETRIC_TOLERANCE_MM or self.major_radius <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Promienie elipsy muszą być dodatnie.")
        if self.minor_radius > self.major_radius + GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Promień główny elipsy nie może być mniejszy od promienia pomocniczego.")

    def point_at_parameter(self, parameter_deg: float) -> Point2D:
        return _ellipse_point(self.center, self.major_radius, self.minor_radius, self.rotation_deg, parameter_deg)

    def parameter_for_point(self, point: Point2D) -> float:
        return _ellipse_parameter_for_point(self.center, self.major_radius, self.minor_radius, self.rotation_deg, point)

    def nearest_parameter_deg(self, point: Point2D) -> float:
        return _nearest_ellipse_parameter(self.center, self.major_radius, self.minor_radius, self.rotation_deg, point)

    def nearest_point(self, point: Point2D) -> Point2D:
        return self.point_at_parameter(self.nearest_parameter_deg(point))

    @property
    def major_positive(self) -> Point2D:
        return self.point_at_parameter(0.0)

    @property
    def major_negative(self) -> Point2D:
        return self.point_at_parameter(180.0)

    @property
    def minor_positive(self) -> Point2D:
        return self.point_at_parameter(90.0)

    @property
    def minor_negative(self) -> Point2D:
        return self.point_at_parameter(270.0)

    @property
    def circumference(self) -> float:
        return _ellipse_arc_length(self.major_radius, self.minor_radius, 0.0, 360.0)

    @property
    def area(self) -> float:
        return math.pi * self.major_radius * self.minor_radius

    def bounds(self) -> Bounds:
        rotation = math.radians(self.rotation_deg)
        extent_x = math.hypot(self.major_radius * math.cos(rotation), self.minor_radius * math.sin(rotation))
        extent_y = math.hypot(self.major_radius * math.sin(rotation), self.minor_radius * math.cos(rotation))
        return self.center.x - extent_x, self.center.y - extent_y, self.center.x + extent_x, self.center.y + extent_y

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "center": self.center.to_dict(),
            "major_radius": self.major_radius,
            "minor_radius": self.minor_radius,
            "rotation_deg": self.rotation_deg,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EllipticalArcEntity:
    center: Point2D
    major_radius: float
    minor_radius: float
    rotation_deg: float
    start_parameter_deg: float
    sweep_parameter_deg: float
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "elliptical_arc"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        object.__setattr__(self, "major_radius", _finite(self.major_radius, "major_radius"))
        object.__setattr__(self, "minor_radius", _finite(self.minor_radius, "minor_radius"))
        object.__setattr__(self, "rotation_deg", _finite(self.rotation_deg, "rotation_deg"))
        object.__setattr__(self, "start_parameter_deg", _finite(self.start_parameter_deg, "start_parameter_deg"))
        object.__setattr__(self, "sweep_parameter_deg", _finite(self.sweep_parameter_deg, "sweep_parameter_deg"))
        if self.minor_radius <= GEOMETRIC_TOLERANCE_MM or self.major_radius <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Promienie łuku elipsy muszą być dodatnie.")
        if self.minor_radius > self.major_radius + GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Promień główny elipsy nie może być mniejszy od promienia pomocniczego.")
        if abs(self.sweep_parameter_deg) <= 1e-9 or abs(self.sweep_parameter_deg) >= 360.0 - 1e-9:
            raise CadValidationError("Rozwarcie łuku elipsy musi być niezerowe i mniejsze niż 360°.")

    def point_at(self, fraction: float) -> Point2D:
        return _ellipse_point(
            self.center,
            self.major_radius,
            self.minor_radius,
            self.rotation_deg,
            self.start_parameter_deg + self.sweep_parameter_deg * float(fraction),
        )

    @property
    def start(self) -> Point2D:
        return self.point_at(0.0)

    @property
    def midpoint(self) -> Point2D:
        return self.point_at(0.5)

    @property
    def end(self) -> Point2D:
        return self.point_at(1.0)

    @property
    def length(self) -> float:
        return _ellipse_arc_length(self.major_radius, self.minor_radius, self.start_parameter_deg, self.sweep_parameter_deg)

    def contains_parameter(self, parameter_deg: float, tolerance_deg: float = 1e-9) -> bool:
        if self.sweep_parameter_deg > 0:
            travelled = (parameter_deg - self.start_parameter_deg) % 360.0
        else:
            travelled = (self.start_parameter_deg - parameter_deg) % 360.0
        return travelled <= abs(self.sweep_parameter_deg) + tolerance_deg

    def parameter_for_point(self, point: Point2D) -> float:
        return _ellipse_parameter_for_point(self.center, self.major_radius, self.minor_radius, self.rotation_deg, point)

    def nearest_point(self, point: Point2D) -> Point2D:
        parameter = _nearest_ellipse_parameter(self.center, self.major_radius, self.minor_radius, self.rotation_deg, point)
        radial = _ellipse_point(self.center, self.major_radius, self.minor_radius, self.rotation_deg, parameter)
        if self.contains_parameter(parameter):
            return radial
        return min((self.start, self.end), key=point.distance_to)

    def bounds(self) -> Bounds:
        candidates = [self.start_parameter_deg, self.start_parameter_deg + self.sweep_parameter_deg]
        rotation = math.radians(self.rotation_deg)
        x_extreme = math.degrees(math.atan2(-self.minor_radius * math.sin(rotation), self.major_radius * math.cos(rotation)))
        y_extreme = math.degrees(math.atan2(self.minor_radius * math.cos(rotation), self.major_radius * math.sin(rotation)))
        for parameter in (x_extreme, x_extreme + 180.0, y_extreme, y_extreme + 180.0):
            if self.contains_parameter(parameter):
                candidates.append(parameter)
        points = [
            _ellipse_point(self.center, self.major_radius, self.minor_radius, self.rotation_deg, parameter)
            for parameter in candidates
        ]
        return min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "center": self.center.to_dict(),
            "major_radius": self.major_radius,
            "minor_radius": self.minor_radius,
            "rotation_deg": self.rotation_deg,
            "start_parameter_deg": self.start_parameter_deg,
            "sweep_parameter_deg": self.sweep_parameter_deg,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


def _open_uniform_bspline_knots(control_point_count: int, degree: int) -> tuple[float, ...]:
    """Return a normalized, clamped knot vector for an open B-spline."""
    interior_count = control_point_count - degree - 1
    denominator = control_point_count - degree
    return (
        *(0.0 for _ in range(degree + 1)),
        *(index / denominator for index in range(1, interior_count + 1)),
        *(1.0 for _ in range(degree + 1)),
    )


@dataclass(frozen=True, slots=True)
class BSplineEntity:
    """Exact non-uniform rational B-spline independent from the renderer.

    The curve is defined by its control polygon, degree, knot vector and
    rational weights.  Sampling is used only for display/diagnostics; model
    evaluation uses the homogeneous de Boor algorithm.
    """

    control_points: tuple[Point2D, ...]
    degree: int = 3
    knots: tuple[float, ...] = ()
    weights: tuple[float, ...] = ()
    closed: bool = False
    periodic: bool = False
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "bspline"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        points = tuple(self.control_points)
        object.__setattr__(self, "control_points", points)
        if isinstance(self.degree, bool) or not isinstance(self.degree, int) or not 1 <= self.degree <= 10:
            raise CadValidationError("Stopień B-spline musi być liczbą całkowitą od 1 do 10.")
        if len(points) < self.degree + 1:
            raise CadValidationError("B-spline wymaga co najmniej degree + 1 punktów kontrolnych.")
        if len(points) > 100_000:
            raise CadValidationError("B-spline ma zbyt wiele punktów kontrolnych.")
        knots = tuple(_finite(value, "knot") for value in self.knots) if self.knots else _open_uniform_bspline_knots(len(points), self.degree)
        if len(knots) != len(points) + self.degree + 1:
            raise CadValidationError("Liczba węzłów B-spline musi wynosić liczba punktów + stopień + 1.")
        if any(second < first for first, second in zip(knots, knots[1:])):
            raise CadValidationError("Węzły B-spline muszą być niemalejące.")
        if knots[self.degree] >= knots[-self.degree - 1]:
            raise CadValidationError("Dziedzina parametru B-spline musi mieć niezerową długość.")
        object.__setattr__(self, "knots", knots)
        weights = tuple(_finite(value, "weight") for value in self.weights) if self.weights else (1.0,) * len(points)
        if len(weights) != len(points) or any(value <= 0.0 for value in weights):
            raise CadValidationError("B-spline wymaga jednej dodatniej wagi dla każdego punktu kontrolnego.")
        object.__setattr__(self, "weights", weights)
        if self.periodic and not self.closed:
            object.__setattr__(self, "closed", True)
        if self.closed and self.start.distance_to(self.end) > 1e-6:
            raise CadValidationError("Zamknięta B-spline musi mieć zgodny początek i koniec krzywej.")

    @property
    def parameter_domain(self) -> tuple[float, float]:
        return self.knots[self.degree], self.knots[-self.degree - 1]

    @property
    def rational(self) -> bool:
        return any(abs(weight - 1.0) > 1e-12 for weight in self.weights)

    def point_at_parameter(self, parameter: float) -> Point2D:
        start, end = self.parameter_domain
        value = min(end, max(start, _finite(parameter, "parameter")))
        count = len(self.control_points)
        span = count - 1 if value >= end else bisect_right(self.knots, value) - 1
        span = max(self.degree, min(count - 1, span))
        work = []
        for index in range(span - self.degree, span + 1):
            point, weight = self.control_points[index], self.weights[index]
            work.append([point.x * weight, point.y * weight, weight])
        for level in range(1, self.degree + 1):
            for offset in range(self.degree, level - 1, -1):
                index = span - self.degree + offset
                denominator = self.knots[index + self.degree - level + 1] - self.knots[index]
                alpha = 0.0 if abs(denominator) <= 1e-15 else (value - self.knots[index]) / denominator
                work[offset] = [
                    (1.0 - alpha) * work[offset - 1][axis] + alpha * work[offset][axis]
                    for axis in range(3)
                ]
        homogeneous = work[self.degree]
        if abs(homogeneous[2]) <= 1e-15:
            raise CadValidationError("B-spline ma osobliwą wagę jednorodną.")
        return Point2D(homogeneous[0] / homogeneous[2], homogeneous[1] / homogeneous[2])

    def point_at(self, fraction: float) -> Point2D:
        start, end = self.parameter_domain
        return self.point_at_parameter(start + (end - start) * min(1.0, max(0.0, float(fraction))))

    @property
    def start(self) -> Point2D:
        return self.point_at(0.0)

    @property
    def midpoint(self) -> Point2D:
        return self.point_at(0.5)

    @property
    def end(self) -> Point2D:
        return self.point_at(1.0)

    def nearest_parameter(self, point: Point2D) -> float:
        start, end = self.parameter_domain
        samples = 128
        parameters = [start + (end - start) * index / samples for index in range(samples + 1)]
        best_index = min(range(len(parameters)), key=lambda index: self.point_at_parameter(parameters[index]).distance_to(point))
        lower = parameters[max(0, best_index - 1)]
        upper = parameters[min(samples, best_index + 1)]
        ratio = (math.sqrt(5.0) - 1.0) / 2.0
        left = upper - ratio * (upper - lower)
        right = lower + ratio * (upper - lower)

        def squared_distance(parameter: float) -> float:
            candidate = self.point_at_parameter(parameter)
            return (candidate.x - point.x) ** 2 + (candidate.y - point.y) ** 2

        left_value, right_value = squared_distance(left), squared_distance(right)
        for _ in range(36):
            if left_value <= right_value:
                upper, right, right_value = right, left, left_value
                left = upper - ratio * (upper - lower)
                left_value = squared_distance(left)
            else:
                lower, left, left_value = left, right, right_value
                right = lower + ratio * (upper - lower)
                right_value = squared_distance(right)
        return (lower + upper) / 2.0

    def nearest_point(self, point: Point2D) -> Point2D:
        return self.point_at_parameter(self.nearest_parameter(point))

    @property
    def length(self) -> float:
        # Numerical arc-length integration operates on the exact curve and is
        # intentionally separate from renderer tessellation.
        count = max(128, len(self.control_points) * 32)
        points = [self.point_at(index / count) for index in range(count + 1)]
        return math.fsum(first.distance_to(second) for first, second in zip(points, points[1:]))

    def diagnostic_points(self, count: int | None = None) -> tuple[Point2D, ...]:
        sample_count = max(16, count or len(self.control_points) * 24)
        points = tuple(self.point_at(index / sample_count) for index in range(sample_count + 1))
        return points

    def bounds(self) -> Bounds:
        points = self.diagnostic_points(max(256, len(self.control_points) * 32))
        return min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "control_points": [point.to_dict() for point in self.control_points],
            "degree": self.degree,
            "knots": list(self.knots),
            "weights": list(self.weights),
            "closed": self.closed,
            "periodic": self.periodic,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PolylineEntity:
    points: tuple[Point2D, ...]
    closed: bool = False
    bulges: tuple[float, ...] = ()
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "polyline"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        points = tuple(self.points)
        object.__setattr__(self, "points", points)
        if len(points) < (3 if self.closed else 2):
            raise CadValidationError("Polilinia wymaga co najmniej dwóch punktów, a zamknięta co najmniej trzech.")
        if self.closed and points[0].distance_to(points[-1]) <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Zamknięta polilinia nie powinna powtarzać pierwszego punktu na końcu.")
        pairs = list(zip(points, points[1:])) + ([(points[-1], points[0])] if self.closed else [])
        if any(first.distance_to(second) <= GEOMETRIC_TOLERANCE_MM for first, second in pairs):
            raise CadValidationError("Polilinia zawiera odcinek o zerowej długości.")
        bulges = tuple(_finite(value, "bulge") for value in self.bulges) if self.bulges else (0.0,) * len(pairs)
        if len(bulges) != len(pairs):
            raise CadValidationError("Polilinia wymaga jednej wartości bulge dla każdego segmentu.")
        object.__setattr__(self, "bulges", bulges)

    @property
    def segments(self) -> tuple[tuple[Point2D, Point2D], ...]:
        result = list(zip(self.points, self.points[1:]))
        if self.closed:
            result.append((self.points[-1], self.points[0]))
        return tuple(result)

    @property
    def segment_entities(self) -> tuple[LineEntity | ArcEntity, ...]:
        result: list[LineEntity | ArcEntity] = []
        namespace = uuid.UUID(self.id)
        for index, ((start, end), bulge) in enumerate(zip(self.segments, self.bulges)):
            segment_id = str(uuid.uuid5(namespace, f"segment-{index}"))
            if abs(bulge) <= 1e-12:
                result.append(LineEntity(start, end, id=segment_id, construction=self.construction, visible=self.visible, locked=self.locked, layer=self.layer))
            else:
                result.append(arc_from_bulge(
                    start,
                    end,
                    bulge,
                    entity_id=segment_id,
                    construction=self.construction,
                    visible=self.visible,
                    locked=self.locked,
                    layer=self.layer,
                ))
        return tuple(result)

    @property
    def length(self) -> float:
        return math.fsum(segment.length for segment in self.segment_entities)

    @property
    def start(self) -> Point2D:
        return self.points[0]

    @property
    def end(self) -> Point2D:
        return self.points[0] if self.closed else self.points[-1]

    def bounds(self) -> Bounds:
        segment_bounds = [segment.bounds() for segment in self.segment_entities]
        return (
            min(bounds[0] for bounds in segment_bounds),
            min(bounds[1] for bounds in segment_bounds),
            max(bounds[2] for bounds in segment_bounds),
            max(bounds[3] for bounds in segment_bounds),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "points": [point.to_dict() for point in self.points],
            "closed": self.closed,
            "bulges": list(self.bulges),
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RegularPolygonEntity:
    center: Point2D
    radius: float
    sides: int
    rotation_deg: float = 0.0
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "regular_polygon"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        object.__setattr__(self, "radius", _finite(self.radius, "radius"))
        object.__setattr__(self, "rotation_deg", _finite(self.rotation_deg, "rotation_deg"))
        if self.radius <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Promień wielokąta musi być dodatni.")
        if isinstance(self.sides, bool) or not isinstance(self.sides, int) or not 3 <= self.sides <= 10_000:
            raise CadValidationError("Wielokąt foremny wymaga od 3 do 10000 boków.")

    @property
    def points(self) -> tuple[Point2D, ...]:
        return tuple(
            Point2D(
                self.center.x + self.radius * math.cos(math.radians(self.rotation_deg + index * 360.0 / self.sides)),
                self.center.y + self.radius * math.sin(math.radians(self.rotation_deg + index * 360.0 / self.sides)),
            )
            for index in range(self.sides)
        )

    @property
    def segments(self) -> tuple[tuple[Point2D, Point2D], ...]:
        points = self.points
        return tuple((points[index], points[(index + 1) % self.sides]) for index in range(self.sides))

    @property
    def side_length(self) -> float:
        return 2.0 * self.radius * math.sin(math.pi / self.sides)

    @property
    def perimeter(self) -> float:
        return self.side_length * self.sides

    @property
    def area(self) -> float:
        return self.sides * self.radius * self.radius * math.sin(math.tau / self.sides) / 2.0

    def bounds(self) -> Bounds:
        points = self.points
        return min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "center": self.center.to_dict(),
            "radius": self.radius,
            "sides": self.sides,
            "rotation_deg": self.rotation_deg,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SlotEntity:
    """Analytic straight slot defined by its centreline and end-cap radius."""

    axis_start: Point2D
    axis_end: Point2D
    radius: float
    id: str = field(default_factory=new_id)
    construction: bool = False
    visible: bool = True
    locked: bool = False
    layer: str = "0"
    style: EntityStyle = field(default_factory=EntityStyle)
    kind: ClassVar[str] = "slot"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validated_id(self.id))
        object.__setattr__(self, "radius", _finite(self.radius, "radius"))
        if self.axis_start.distance_to(self.axis_end) <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Oś szczeliny musi mieć niezerową długość.")
        if self.radius <= GEOMETRIC_TOLERANCE_MM:
            raise CadValidationError("Szczelina musi mieć dodatnią szerokość.")

    @property
    def axis_length(self) -> float:
        return self.axis_start.distance_to(self.axis_end)

    @property
    def width(self) -> float:
        return self.radius * 2.0

    @property
    def center(self) -> Point2D:
        return Point2D(
            (self.axis_start.x + self.axis_end.x) / 2.0,
            (self.axis_start.y + self.axis_end.y) / 2.0,
        )

    @property
    def angle_deg(self) -> float:
        return math.degrees(math.atan2(self.axis_end.y - self.axis_start.y, self.axis_end.x - self.axis_start.x))

    @property
    def boundary_points(self) -> tuple[Point2D, Point2D, Point2D, Point2D]:
        dx = (self.axis_end.x - self.axis_start.x) / self.axis_length
        dy = (self.axis_end.y - self.axis_start.y) / self.axis_length
        nx, ny = -dy * self.radius, dx * self.radius
        return (
            Point2D(self.axis_start.x + nx, self.axis_start.y + ny),
            Point2D(self.axis_end.x + nx, self.axis_end.y + ny),
            Point2D(self.axis_end.x - nx, self.axis_end.y - ny),
            Point2D(self.axis_start.x - nx, self.axis_start.y - ny),
        )

    @property
    def boundary_lines(self) -> tuple[LineEntity, LineEntity]:
        start_top, end_top, end_bottom, start_bottom = self.boundary_points
        return LineEntity(start_top, end_top), LineEntity(end_bottom, start_bottom)

    @property
    def boundary_arcs(self) -> tuple[ArcEntity, ArcEntity]:
        angle = self.angle_deg
        return (
            ArcEntity(self.axis_end, self.radius, angle + 90.0, -180.0),
            ArcEntity(self.axis_start, self.radius, angle - 90.0, -180.0),
        )

    @property
    def perimeter(self) -> float:
        return self.axis_length * 2.0 + math.tau * self.radius

    @property
    def area(self) -> float:
        return self.axis_length * self.width + math.pi * self.radius * self.radius

    def bounds(self) -> Bounds:
        return (
            min(self.axis_start.x, self.axis_end.x) - self.radius,
            min(self.axis_start.y, self.axis_end.y) - self.radius,
            max(self.axis_start.x, self.axis_end.x) + self.radius,
            max(self.axis_start.y, self.axis_end.y) + self.radius,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "id": self.id,
            "axis_start": self.axis_start.to_dict(),
            "axis_end": self.axis_end.to_dict(),
            "radius": self.radius,
            "construction": self.construction,
            "visible": self.visible,
            "locked": self.locked,
            "layer": self.layer,
            "style": self.style.to_dict(),
        }


SketchEntity: TypeAlias = (
    PointEntity | LineEntity | RectangleEntity | CircleEntity | ArcEntity | EllipseEntity |
    EllipticalArcEntity | BSplineEntity | PolylineEntity | RegularPolygonEntity | SlotEntity
)


def entity_from_dict(data: object) -> SketchEntity:
    if not isinstance(data, dict):
        raise CadValidationError("Encja szkicu ma nieprawidłowy format.")
    common = {
        "id": _validated_id(data.get("id")),
        "construction": bool(data.get("construction", False)),
        "visible": bool(data.get("visible", True)),
        "locked": bool(data.get("locked", False)),
        "layer": str(data.get("layer", "0"))[:128],
        "style": EntityStyle.from_dict(data.get("style")),
    }
    kind = data.get("type")
    if kind == PointEntity.kind:
        return PointEntity(Point2D.from_dict(data.get("point")), **common)
    if kind == LineEntity.kind:
        return LineEntity(Point2D.from_dict(data.get("start")), Point2D.from_dict(data.get("end")), **common)
    if kind == RectangleEntity.kind:
        return RectangleEntity(
            Point2D.from_dict(data.get("origin")),
            _finite(data.get("width"), "width"),
            _finite(data.get("height"), "height"),
            **common,
        )
    if kind == CircleEntity.kind:
        return CircleEntity(
            Point2D.from_dict(data.get("center")),
            _finite(data.get("radius"), "radius"),
            **common,
        )
    if kind == ArcEntity.kind:
        return ArcEntity(
            Point2D.from_dict(data.get("center")),
            _finite(data.get("radius"), "radius"),
            _finite(data.get("start_angle_deg"), "start_angle_deg"),
            _finite(data.get("sweep_angle_deg"), "sweep_angle_deg"),
            **common,
        )
    if kind == EllipseEntity.kind:
        return EllipseEntity(
            Point2D.from_dict(data.get("center")),
            _finite(data.get("major_radius"), "major_radius"),
            _finite(data.get("minor_radius"), "minor_radius"),
            _finite(data.get("rotation_deg", 0.0), "rotation_deg"),
            **common,
        )
    if kind == EllipticalArcEntity.kind:
        return EllipticalArcEntity(
            Point2D.from_dict(data.get("center")),
            _finite(data.get("major_radius"), "major_radius"),
            _finite(data.get("minor_radius"), "minor_radius"),
            _finite(data.get("rotation_deg", 0.0), "rotation_deg"),
            _finite(data.get("start_parameter_deg"), "start_parameter_deg"),
            _finite(data.get("sweep_parameter_deg"), "sweep_parameter_deg"),
            **common,
        )
    if kind == BSplineEntity.kind:
        raw_points = data.get("control_points")
        raw_knots = data.get("knots", [])
        raw_weights = data.get("weights", [])
        if not isinstance(raw_points, list) or len(raw_points) > 100_000:
            raise CadValidationError("Lista punktów kontrolnych B-spline jest nieprawidłowa lub zbyt długa.")
        if not isinstance(raw_knots, list) or len(raw_knots) > 100_020:
            raise CadValidationError("Lista węzłów B-spline jest nieprawidłowa lub zbyt długa.")
        if not isinstance(raw_weights, list) or len(raw_weights) > 100_000:
            raise CadValidationError("Lista wag B-spline jest nieprawidłowa lub zbyt długa.")
        raw_degree = data.get("degree", 3)
        if isinstance(raw_degree, bool) or not isinstance(raw_degree, int):
            raise CadValidationError("Stopień B-spline musi być liczbą całkowitą.")
        return BSplineEntity(
            tuple(Point2D.from_dict(point) for point in raw_points),
            raw_degree,
            tuple(_finite(value, "knot") for value in raw_knots),
            tuple(_finite(value, "weight") for value in raw_weights),
            bool(data.get("closed", False)),
            bool(data.get("periodic", False)),
            **common,
        )
    if kind == PolylineEntity.kind:
        raw_points = data.get("points")
        raw_bulges = data.get("bulges", [])
        if not isinstance(raw_points, list) or len(raw_points) > 100_000:
            raise CadValidationError("Lista punktów polilinii jest nieprawidłowa lub zbyt długa.")
        if not isinstance(raw_bulges, list) or len(raw_bulges) > 100_000:
            raise CadValidationError("Lista parametrów bulge polilinii jest nieprawidłowa lub zbyt długa.")
        return PolylineEntity(
            tuple(Point2D.from_dict(point) for point in raw_points),
            bool(data.get("closed", False)),
            tuple(_finite(value, "bulge") for value in raw_bulges),
            **common,
        )
    if kind == RegularPolygonEntity.kind:
        raw_sides = data.get("sides")
        if isinstance(raw_sides, bool) or not isinstance(raw_sides, int):
            raise CadValidationError("Liczba boków wielokąta musi być liczbą całkowitą.")
        return RegularPolygonEntity(
            Point2D.from_dict(data.get("center")),
            _finite(data.get("radius"), "radius"),
            raw_sides,
            _finite(data.get("rotation_deg", 0.0), "rotation_deg"),
            **common,
        )
    if kind == SlotEntity.kind:
        return SlotEntity(
            Point2D.from_dict(data.get("axis_start")),
            Point2D.from_dict(data.get("axis_end")),
            _finite(data.get("radius"), "radius"),
            **common,
        )
    raise CadValidationError(f"Nieobsługiwany typ encji szkicu: {kind!r}.")


@dataclass(slots=True)
class Sketch:
    id: str = field(default_factory=new_id)
    name: str = "Sketch"
    label: str = "Szkic"
    entities: dict[str, SketchEntity] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    constraints: dict[str, SketchConstraint] = field(default_factory=dict)
    constraint_order: list[str] = field(default_factory=list)
    patterns: dict[str, SketchPattern] = field(default_factory=dict)
    pattern_order: list[str] = field(default_factory=list)
    visible: bool = True
    locked: bool = False
    inputs: set[str] = field(default_factory=set)
    outputs: set[str] = field(default_factory=set)
    recompute_status: str = "clean"
    error: str = ""
    diagnostics: list[str] = field(default_factory=list)
    solve_status: SketchSolveStatus = SketchSolveStatus.UNDER_CONSTRAINED
    degrees_of_freedom: int = 0

    def __post_init__(self) -> None:
        self.id = _validated_id(self.id)

    def _add_entity(self, entity: SketchEntity, index: int | None = None) -> None:
        if entity.id in self.entities:
            raise CadValidationError(f"Encja {entity.id} już istnieje w szkicu.")
        if len(self.entities) >= MAX_ENTITIES_PER_SKETCH:
            raise CadValidationError("Szkic przekracza bezpieczny limit liczby encji.")
        self.entities[entity.id] = entity
        if index is None or index >= len(self.order):
            self.order.append(entity.id)
        else:
            self.order.insert(max(0, index), entity.id)
        self.recompute_status = "dirty"

    def _remove_entity(self, entity_id: str) -> tuple[SketchEntity, int]:
        if entity_id not in self.entities:
            raise CadValidationError(f"Nie znaleziono encji {entity_id}.")
        index = self.order.index(entity_id)
        entity = self.entities.pop(entity_id)
        self.order.remove(entity_id)
        self.recompute_status = "dirty"
        return entity, index

    def _replace_entity(self, entity: SketchEntity) -> SketchEntity:
        if entity.id not in self.entities:
            raise CadValidationError(f"Nie znaleziono encji {entity.id}.")
        previous = self.entities[entity.id]
        self.entities[entity.id] = entity
        self.recompute_status = "dirty"
        return previous

    def _add_constraint(self, constraint: SketchConstraint, index: int | None = None) -> None:
        if constraint.id in self.constraints:
            raise CadValidationError(f"Więz {constraint.id} już istnieje w szkicu.")
        if len(self.constraints) >= MAX_CONSTRAINTS_PER_SKETCH:
            raise CadValidationError("Szkic przekracza bezpieczny limit liczby więzów.")
        self.constraints[constraint.id] = constraint
        if index is None or index >= len(self.constraint_order):
            self.constraint_order.append(constraint.id)
        else:
            self.constraint_order.insert(max(0, index), constraint.id)
        self.recompute_status = "dirty"

    def _remove_constraint(self, constraint_id: str) -> tuple[SketchConstraint, int]:
        if constraint_id not in self.constraints:
            raise CadValidationError(f"Nie znaleziono więzu {constraint_id}.")
        index = self.constraint_order.index(constraint_id)
        constraint = self.constraints.pop(constraint_id)
        self.constraint_order.remove(constraint_id)
        self.recompute_status = "dirty"
        return constraint, index

    def _replace_constraint(self, constraint: SketchConstraint) -> SketchConstraint:
        if constraint.id not in self.constraints:
            raise CadValidationError(f"Nie znaleziono więzu {constraint.id}.")
        previous = self.constraints[constraint.id]
        self.constraints[constraint.id] = constraint
        self.recompute_status = "dirty"
        return previous

    def _add_pattern(self, pattern: SketchPattern, index: int | None = None) -> None:
        if pattern.id in self.patterns:
            raise CadValidationError(f"Szyk {pattern.id} już istnieje w szkicu.")
        if len(self.patterns) >= MAX_PATTERNS_PER_SKETCH:
            raise CadValidationError("Szkic przekracza bezpieczny limit liczby szyków.")
        self.patterns[pattern.id] = pattern
        if index is None or index >= len(self.pattern_order):
            self.pattern_order.append(pattern.id)
        else:
            self.pattern_order.insert(max(0, index), pattern.id)
        self.recompute_status = "dirty"

    def _remove_pattern(self, pattern_id: str) -> tuple[SketchPattern, int]:
        if pattern_id not in self.patterns:
            raise CadValidationError(f"Nie znaleziono szyku {pattern_id}.")
        index = self.pattern_order.index(pattern_id)
        pattern = self.patterns.pop(pattern_id)
        self.pattern_order.remove(pattern_id)
        self.recompute_status = "dirty"
        return pattern, index

    def _replace_pattern(self, pattern: SketchPattern) -> SketchPattern:
        if pattern.id not in self.patterns:
            raise CadValidationError(f"Nie znaleziono szyku {pattern.id}.")
        previous = self.patterns[pattern.id]
        self.patterns[pattern.id] = pattern
        self.recompute_status = "dirty"
        return previous

    def ordered_entities(self) -> list[SketchEntity]:
        return [self.entities[entity_id] for entity_id in self.order]

    def ordered_constraints(self) -> list[SketchConstraint]:
        return [self.constraints[constraint_id] for constraint_id in self.constraint_order]

    def ordered_patterns(self) -> list[SketchPattern]:
        return [self.patterns[pattern_id] for pattern_id in self.pattern_order]

    def bounds(self) -> Bounds | None:
        visible = [entity.bounds() for entity in self.ordered_entities() if entity.visible]
        if not visible:
            return None
        return (
            min(item[0] for item in visible),
            min(item[1] for item in visible),
            max(item[2] for item in visible),
            max(item[3] for item in visible),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "label": self.label,
            "visible": self.visible,
            "locked": self.locked,
            "inputs": sorted(self.inputs),
            "entities": [self.entities[entity_id].to_dict() for entity_id in self.order],
            "constraints": [self.constraints[constraint_id].to_dict() for constraint_id in self.constraint_order],
            "patterns": [self.patterns[pattern_id].to_dict() for pattern_id in self.pattern_order],
        }

    @classmethod
    def from_dict(cls, data: object) -> "Sketch":
        if not isinstance(data, dict):
            raise CadValidationError("Szkic ma nieprawidłowy format.")
        raw_entities = data.get("entities", [])
        if not isinstance(raw_entities, list) or len(raw_entities) > MAX_ENTITIES_PER_SKETCH:
            raise CadValidationError("Lista encji szkicu jest nieprawidłowa lub zbyt duża.")
        raw_constraints = data.get("constraints", [])
        if not isinstance(raw_constraints, list) or len(raw_constraints) > MAX_CONSTRAINTS_PER_SKETCH:
            raise CadValidationError("Lista więzów szkicu jest nieprawidłowa lub zbyt duża.")
        raw_patterns = data.get("patterns", [])
        if not isinstance(raw_patterns, list) or len(raw_patterns) > MAX_PATTERNS_PER_SKETCH:
            raise CadValidationError("Lista szyków szkicu jest nieprawidłowa lub zbyt duża.")
        sketch = cls(
            id=_validated_id(data.get("id")),
            name=str(data.get("name", "Sketch"))[:128],
            label=str(data.get("label", "Szkic"))[:256],
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            inputs={_validated_id(value, "inputs") for value in data.get("inputs", [])},
        )
        for raw in raw_entities:
            sketch._add_entity(entity_from_dict(raw))
        for raw in raw_constraints:
            try:
                sketch._add_constraint(constraint_from_dict(raw))
            except (TypeError, ValueError) as exc:
                raise CadValidationError(f"Nieprawidłowy więz szkicu: {exc}") from exc
        for raw in raw_patterns:
            try:
                sketch._add_pattern(pattern_from_dict(raw))
            except (TypeError, ValueError) as exc:
                raise CadValidationError(f"Nieprawidłowy szyk szkicu: {exc}") from exc
        sketch.recompute_status = "clean"
        return sketch


@dataclass(slots=True)
class CadDocument:
    id: str = field(default_factory=new_id)
    name: str = "Document"
    label: str = "Projekt CAD"
    unit: str = "mm"
    sketches: dict[str, Sketch] = field(default_factory=dict)
    object_order: list[str] = field(default_factory=list)
    active_sketch_id: str = ""
    parameters: dict[str, CadParameter] = field(default_factory=dict)
    parameter_order: list[str] = field(default_factory=list)
    view_state: dict[str, Any] = field(default_factory=dict)
    layers: dict[str, CadLayer] = field(default_factory=lambda: {"0": CadLayer("0")})

    def __post_init__(self) -> None:
        self.id = _validated_id(self.id)
        if self.unit != "mm":
            raise CadValidationError("Wersja 1 dokumentu przechowuje geometrię wewnętrznie w milimetrach.")
        if "0" not in self.layers:
            self.layers["0"] = CadLayer("0")

    @classmethod
    def create(cls, label: str = "Projekt CAD") -> "CadDocument":
        document = cls(label=label)
        sketch = Sketch()
        document._add_sketch(sketch)
        document.active_sketch_id = sketch.id
        return document

    @property
    def active_sketch(self) -> Sketch:
        try:
            return self.sketches[self.active_sketch_id]
        except KeyError as exc:
            raise CadValidationError("Dokument nie ma aktywnego szkicu.") from exc

    def _add_sketch(self, sketch: Sketch) -> None:
        if sketch.id in self.sketches:
            raise CadValidationError(f"Obiekt {sketch.id} już istnieje w dokumencie.")
        self.sketches[sketch.id] = sketch
        self.object_order.append(sketch.id)

    def _add_parameter(self, parameter: CadParameter, index: int | None = None) -> None:
        if parameter.id in self.parameters:
            raise CadValidationError(f"Parametr {parameter.id} już istnieje w dokumencie.")
        if len(self.parameters) >= 10_000:
            raise CadValidationError("Dokument przekracza bezpieczny limit liczby parametrów.")
        if any(
            existing.name == parameter.name and existing.scope_object_id == parameter.scope_object_id
            for existing in self.parameters.values()
        ):
            raise CadValidationError(f"Parametr {parameter.name} już istnieje w tym zakresie.")
        self.parameters[parameter.id] = parameter
        if index is None or index >= len(self.parameter_order):
            self.parameter_order.append(parameter.id)
        else:
            self.parameter_order.insert(max(0, index), parameter.id)

    def _remove_parameter(self, parameter_id: str) -> tuple[CadParameter, int]:
        if parameter_id not in self.parameters:
            raise CadValidationError(f"Nie znaleziono parametru {parameter_id}.")
        index = self.parameter_order.index(parameter_id)
        parameter = self.parameters.pop(parameter_id)
        self.parameter_order.remove(parameter_id)
        return parameter, index

    def _replace_parameter(self, parameter: CadParameter) -> CadParameter:
        if parameter.id not in self.parameters:
            raise CadValidationError(f"Nie znaleziono parametru {parameter.id}.")
        if any(
            existing.id != parameter.id
            and existing.name == parameter.name
            and existing.scope_object_id == parameter.scope_object_id
            for existing in self.parameters.values()
        ):
            raise CadValidationError(f"Parametr {parameter.name} już istnieje w tym zakresie.")
        previous = self.parameters[parameter.id]
        self.parameters[parameter.id] = parameter
        return previous

    def ordered_parameters(self) -> list[CadParameter]:
        return [self.parameters[parameter_id] for parameter_id in self.parameter_order]

    def parameter_values(self, scope_object_id: str = "") -> dict[str, float]:
        values = {
            parameter.name: parameter.value
            for parameter in self.ordered_parameters()
            if not parameter.scope_object_id
        }
        if scope_object_id:
            values.update(
                (parameter.name, parameter.value)
                for parameter in self.ordered_parameters()
                if parameter.scope_object_id == scope_object_id
            )
        return values

    def _topological_order(self) -> list[str]:
        known = set(self.sketches)
        for sketch in self.sketches.values():
            if not sketch.inputs <= known:
                raise CadValidationError(f"Szkic {sketch.label} wskazuje brakującą zależność.")
        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[str] = []

        def visit(object_id: str) -> None:
            if object_id in visiting:
                raise CadValidationError("Wykryto cykl w grafie zależności dokumentu CAD.")
            if object_id in visited:
                return
            visiting.add(object_id)
            for dependency in self.sketches[object_id].inputs:
                visit(dependency)
            visiting.remove(object_id)
            visited.add(object_id)
            ordered.append(object_id)

        for object_id in self.object_order:
            visit(object_id)
        return ordered

    def validate_dependency_graph(self) -> None:
        self._topological_order()

    def recompute(self) -> list[str]:
        order = self._topological_order()
        changed: list[str] = []
        for object_id in order:
            sketch = self.sketches[object_id]
            if sketch.recompute_status == "dirty" or any(
                dependency in changed for dependency in sketch.inputs
            ):
                sketch.recompute_status = "clean"
                sketch.error = ""
                changed.append(object_id)
        return changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "document": {
                "id": self.id,
                "name": self.name,
                "label": self.label,
                "unit": self.unit,
                "active_sketch_id": self.active_sketch_id,
                "parameters": [self.parameters[parameter_id].to_dict() for parameter_id in self.parameter_order],
                "view_state": dict(self.view_state),
                "layers": [layer.to_dict() for layer in self.layers.values()],
                "objects": [self.sketches[object_id].to_dict() for object_id in self.object_order],
            },
        }

    @classmethod
    def from_dict(cls, payload: object) -> "CadDocument":
        if not isinstance(payload, dict):
            raise CadValidationError("Plik projektu CAD ma nieprawidłową strukturę.")
        version = payload.get("schema_version")
        if version != SCHEMA_VERSION:
            raise CadValidationError(f"Nieobsługiwana wersja schematu CAD: {version!r}.")
        data = payload.get("document")
        if not isinstance(data, dict):
            raise CadValidationError("W pliku brakuje dokumentu CAD.")
        raw_objects = data.get("objects", [])
        if not isinstance(raw_objects, list) or len(raw_objects) > 10_000:
            raise CadValidationError("Lista obiektów CAD jest nieprawidłowa lub zbyt duża.")
        raw_parameters = data.get("parameters", [])
        if not isinstance(raw_parameters, list) or len(raw_parameters) > 10_000:
            raise CadValidationError("Lista parametrów CAD jest nieprawidłowa lub zbyt duża.")
        view_state = data.get("view_state", {})
        if not isinstance(view_state, dict):
            raise CadValidationError("Ustawienia widoku CAD mają nieprawidłowy format.")
        raw_layers = data.get("layers", [{"name": "0"}])
        if not isinstance(raw_layers, list) or len(raw_layers) > 10_000:
            raise CadValidationError("Lista warstw CAD jest nieprawidłowa lub zbyt długa.")
        layers = {layer.name: layer for layer in (CadLayer.from_dict(raw) for raw in raw_layers)}
        document = cls(
            id=_validated_id(data.get("id")),
            name=str(data.get("name", "Document"))[:128],
            label=str(data.get("label", "Projekt CAD"))[:256],
            unit=str(data.get("unit", "mm")),
            view_state=dict(view_state),
            layers=layers,
        )
        for raw in raw_objects:
            document._add_sketch(Sketch.from_dict(raw))
        for raw in raw_parameters:
            try:
                document._add_parameter(CadParameter.from_dict(raw))
            except (TypeError, ValueError) as exc:
                raise CadValidationError(f"Nieprawidłowy parametr CAD: {exc}") from exc
        document.active_sketch_id = _validated_id(data.get("active_sketch_id"), "active_sketch_id")
        if document.active_sketch_id not in document.sketches:
            raise CadValidationError("Aktywny szkic nie istnieje w dokumencie.")
        if any(
            parameter.scope_object_id and parameter.scope_object_id not in document.sketches
            for parameter in document.parameters.values()
        ):
            raise CadValidationError("Parametr lokalny wskazuje nieistniejący obiekt zakresu.")
        for sketch in document.sketches.values():
            sketch.outputs.clear()
        for sketch in document.sketches.values():
            for dependency in sketch.inputs:
                if dependency in document.sketches:
                    document.sketches[dependency].outputs.add(sketch.id)
        document.validate_dependency_graph()
        return document


def line_from_length_angle(start: Point2D, length: float, angle_deg: float, *, entity_id: str | None = None) -> LineEntity:
    checked_length = _finite(length, "length")
    if checked_length <= GEOMETRIC_TOLERANCE_MM:
        raise CadValidationError("Długość linii musi być dodatnia.")
    radians = math.radians(_finite(angle_deg, "angle"))
    end = Point2D(start.x + checked_length * math.cos(radians), start.y + checked_length * math.sin(radians))
    return LineEntity(start, end, id=entity_id or new_id())


def arc_from_bulge(
    start: Point2D,
    end: Point2D,
    bulge: float,
    *,
    entity_id: str | None = None,
    construction: bool = False,
    visible: bool = True,
    locked: bool = False,
    layer: str = "0",
) -> ArcEntity:
    """Convert a DXF bulge to an exact circular arc.

    Bulge is tan(included_angle / 4); its sign preserves CW/CCW direction.
    """
    checked_bulge = _finite(bulge, "bulge")
    chord = start.distance_to(end)
    if chord <= GEOMETRIC_TOLERANCE_MM:
        raise CadValidationError("Łuk bulge wymaga dwóch różnych końców.")
    if abs(checked_bulge) <= 1e-12:
        raise CadValidationError("Zerowy bulge opisuje odcinek, nie łuk.")
    dx, dy = end.x - start.x, end.y - start.y
    midpoint = Point2D((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
    center_offset = chord * (1.0 - checked_bulge * checked_bulge) / (4.0 * checked_bulge)
    center = Point2D(
        midpoint.x - dy * center_offset / chord,
        midpoint.y + dx * center_offset / chord,
    )
    radius = chord * (1.0 + checked_bulge * checked_bulge) / (4.0 * abs(checked_bulge))
    start_angle = math.degrees(math.atan2(start.y - center.y, start.x - center.x))
    sweep = math.degrees(4.0 * math.atan(checked_bulge))
    return ArcEntity(
        center,
        radius,
        start_angle,
        sweep,
        id=entity_id or new_id(),
        construction=construction,
        visible=visible,
        locked=locked,
        layer=layer,
    )


def arc_from_three_points(start: Point2D, through: Point2D, end: Point2D, *, entity_id: str | None = None) -> ArcEntity:
    """Return the unique circular arc from start to end passing through through."""
    determinant = 2.0 * (
        start.x * (through.y - end.y)
        + through.x * (end.y - start.y)
        + end.x * (start.y - through.y)
    )
    scale = max(1.0, start.distance_to(through), through.distance_to(end), start.distance_to(end))
    if abs(determinant) <= GEOMETRIC_TOLERANCE_MM * scale:
        raise CadValidationError("Trzy punkty łuku nie mogą być współliniowe.")
    start_sq = start.x * start.x + start.y * start.y
    through_sq = through.x * through.x + through.y * through.y
    end_sq = end.x * end.x + end.y * end.y
    center = Point2D(
        (start_sq * (through.y - end.y) + through_sq * (end.y - start.y) + end_sq * (start.y - through.y)) / determinant,
        (start_sq * (end.x - through.x) + through_sq * (start.x - end.x) + end_sq * (through.x - start.x)) / determinant,
    )
    start_angle = math.degrees(math.atan2(start.y - center.y, start.x - center.x))
    through_angle = math.degrees(math.atan2(through.y - center.y, through.x - center.x))
    end_angle = math.degrees(math.atan2(end.y - center.y, end.x - center.x))
    ccw_sweep = (end_angle - start_angle) % 360.0
    ccw_to_through = (through_angle - start_angle) % 360.0
    sweep = ccw_sweep if ccw_to_through <= ccw_sweep + 1e-9 else ccw_sweep - 360.0
    return ArcEntity(center, center.distance_to(start), start_angle, sweep, id=entity_id or new_id())


def rectangle_from_center(center: Point2D, width: float, height: float, *, entity_id: str | None = None) -> RectangleEntity:
    checked_width = _finite(width, "width")
    checked_height = _finite(height, "height")
    return RectangleEntity(
        Point2D(center.x - checked_width / 2.0, center.y - checked_height / 2.0),
        checked_width,
        checked_height,
        id=entity_id or new_id(),
    )


def ellipse_from_three_points(
    center: Point2D,
    major_point: Point2D,
    minor_point: Point2D,
    *,
    entity_id: str | None = None,
) -> EllipseEntity:
    major_radius = center.distance_to(major_point)
    if major_radius <= GEOMETRIC_TOLERANCE_MM:
        raise CadValidationError("Punkt osi głównej elipsy musi różnić się od środka.")
    rotation_deg = math.degrees(math.atan2(major_point.y - center.y, major_point.x - center.x))
    rotation = math.radians(rotation_deg)
    dx, dy = minor_point.x - center.x, minor_point.y - center.y
    minor_radius = abs(-dx * math.sin(rotation) + dy * math.cos(rotation))
    return EllipseEntity(center, major_radius, minor_radius, rotation_deg, id=entity_id or new_id())


def elliptical_arc_from_five_points(
    center: Point2D,
    major_point: Point2D,
    minor_point: Point2D,
    start_point: Point2D,
    end_point: Point2D,
    *,
    entity_id: str | None = None,
) -> EllipticalArcEntity:
    ellipse = ellipse_from_three_points(center, major_point, minor_point)
    start_parameter = ellipse.parameter_for_point(start_point)
    end_parameter = ellipse.parameter_for_point(end_point)
    sweep = (end_parameter - start_parameter) % 360.0
    if sweep <= 1e-9:
        raise CadValidationError("Początek i koniec łuku elipsy muszą wyznaczać różne parametry.")
    return EllipticalArcEntity(
        ellipse.center,
        ellipse.major_radius,
        ellipse.minor_radius,
        ellipse.rotation_deg,
        start_parameter,
        sweep,
        id=entity_id or new_id(),
    )


def regular_polygon(
    center: Point2D,
    radius: float,
    sides: int,
    rotation_deg: float = 0.0,
    *,
    entity_id: str | None = None,
) -> RegularPolygonEntity:
    checked_radius = _finite(radius, "radius")
    checked_rotation = _finite(rotation_deg, "rotation_deg")
    if checked_radius <= GEOMETRIC_TOLERANCE_MM:
        raise CadValidationError("Promień wielokąta musi być dodatni.")
    if isinstance(sides, bool) or not isinstance(sides, int) or not 3 <= sides <= 10_000:
        raise CadValidationError("Wielokąt foremny wymaga od 3 do 10000 boków.")
    return RegularPolygonEntity(center, checked_radius, sides, checked_rotation, id=entity_id or new_id())


def slot_from_three_points(
    axis_start: Point2D,
    axis_end: Point2D,
    width_point: Point2D,
    *,
    entity_id: str | None = None,
) -> SlotEntity:
    length = axis_start.distance_to(axis_end)
    if length <= GEOMETRIC_TOLERANCE_MM:
        raise CadValidationError("Najpierw wskaż dwa różne końce osi szczeliny.")
    distance = abs(
        (axis_end.x - axis_start.x) * (axis_start.y - width_point.y)
        - (axis_start.x - width_point.x) * (axis_end.y - axis_start.y)
    ) / length
    if distance <= GEOMETRIC_TOLERANCE_MM:
        raise CadValidationError("Trzeci punkt musi określać niezerową połowę szerokości szczeliny.")
    return SlotEntity(axis_start, axis_end, distance, id=entity_id or new_id())


def replace_entity(entity: SketchEntity, **changes: Any) -> SketchEntity:
    return replace(entity, **changes)


def combined_bounds(entities: Iterable[SketchEntity]) -> Bounds | None:
    bounds = [entity.bounds() for entity in entities if entity.visible]
    if not bounds:
        return None
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable

import numpy as np

from cad.constraints import (
    ConstraintStatus,
    ConstraintType,
    GeometryReference,
    SketchConstraint,
    SketchSolveStatus,
)
from cad.model import (
    ArcEntity,
    BSplineEntity,
    CircleEntity,
    EllipseEntity,
    EllipticalArcEntity,
    LineEntity,
    Point2D,
    PointEntity,
    PolylineEntity,
    RegularPolygonEntity,
    RectangleEntity,
    Sketch,
    SketchEntity,
    SlotEntity,
    _ellipse_arc_length,
)


SOLVER_TOLERANCE = 1e-7
MAX_ITERATIONS = 60


class SolverGeometryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SolverResult:
    status: SketchSolveStatus
    degrees_of_freedom: int
    entities: dict[str, SketchEntity]
    constraints: dict[str, SketchConstraint]
    residual_norm: float
    conflicting_ids: tuple[str, ...] = ()
    redundant_ids: tuple[str, ...] = ()
    broken_reference_ids: tuple[str, ...] = ()
    measured_values: dict[str, float] | None = None
    iterations: int = 0
    diagnostic: str = ""

    @property
    def solved(self) -> bool:
        return self.status in {
            SketchSolveStatus.UNDER_CONSTRAINED,
            SketchSolveStatus.FULLY_CONSTRAINED,
            SketchSolveStatus.REDUNDANT,
        }


class _Parameterization:
    def __init__(self, sketch: Sketch) -> None:
        self.sketch = sketch
        self.slices: dict[str, slice] = {}
        values: list[float] = []
        for entity in sketch.ordered_entities():
            start = len(values)
            if isinstance(entity, PointEntity):
                values.extend((entity.point.x, entity.point.y))
            elif isinstance(entity, LineEntity):
                values.extend((entity.start.x, entity.start.y, entity.end.x, entity.end.y))
            elif isinstance(entity, RectangleEntity):
                values.extend((entity.origin.x, entity.origin.y, entity.width, entity.height))
            elif isinstance(entity, CircleEntity):
                values.extend((entity.center.x, entity.center.y, entity.radius))
            elif isinstance(entity, ArcEntity):
                values.extend((entity.center.x, entity.center.y, entity.radius, entity.start_angle_deg, entity.sweep_angle_deg))
            elif isinstance(entity, EllipseEntity):
                values.extend((entity.center.x, entity.center.y, entity.major_radius, entity.minor_radius, entity.rotation_deg))
            elif isinstance(entity, EllipticalArcEntity):
                values.extend((
                    entity.center.x,
                    entity.center.y,
                    entity.major_radius,
                    entity.minor_radius,
                    entity.rotation_deg,
                    entity.start_parameter_deg,
                    entity.sweep_parameter_deg,
                ))
            elif isinstance(entity, BSplineEntity):
                values.extend(coordinate for point in entity.control_points for coordinate in (point.x, point.y))
            elif isinstance(entity, PolylineEntity):
                values.extend(coordinate for point in entity.points for coordinate in (point.x, point.y))
            elif isinstance(entity, RegularPolygonEntity):
                values.extend((entity.center.x, entity.center.y, entity.radius, entity.rotation_deg))
            elif isinstance(entity, SlotEntity):
                values.extend((entity.axis_start.x, entity.axis_start.y, entity.axis_end.x, entity.axis_end.y, entity.radius))
            else:  # pragma: no cover - closed domain union
                raise TypeError(type(entity))
            self.slices[entity.id] = slice(start, len(values))
        self.initial = np.asarray(values, dtype=float)

    def _slice(self, entity_id: str) -> slice:
        try:
            return self.slices[entity_id]
        except KeyError as exc:
            raise SolverGeometryError(f"Brak encji {entity_id}.") from exc

    def entity_values(self, entity_id: str, vector: np.ndarray) -> np.ndarray:
        return vector[self._slice(entity_id)]

    def point(self, reference: GeometryReference, vector: np.ndarray) -> np.ndarray:
        entity = self.sketch.entities.get(reference.entity_id)
        if entity is None:
            raise SolverGeometryError(f"Brak encji {reference.entity_id}.")
        values = self.entity_values(entity.id, vector)
        element = reference.element
        if isinstance(entity, PointEntity):
            if element in {"entity", "point", "center"}:
                return values[0:2].copy()
        elif isinstance(entity, LineEntity):
            start, end = values[0:2], values[2:4]
            if element in {"entity", "start"}:
                return start.copy()
            if element == "end":
                return end.copy()
            if element in {"midpoint", "center"}:
                return (start + end) / 2
        elif isinstance(entity, RectangleEntity):
            x, y, width, height = values
            points = {
                "entity": np.asarray((x, y)),
                "origin": np.asarray((x, y)),
                "corner-0": np.asarray((x, y)),
                "corner-1": np.asarray((x + width, y)),
                "corner-2": np.asarray((x + width, y + height)),
                "corner-3": np.asarray((x, y + height)),
                "center": np.asarray((x + width / 2, y + height / 2)),
            }
            if element in points:
                return points[element]
        elif isinstance(entity, CircleEntity):
            x, y, radius = values
            points = {
                "entity": np.asarray((x, y)),
                "center": np.asarray((x, y)),
                "right": np.asarray((x + radius, y)),
                "left": np.asarray((x - radius, y)),
                "top": np.asarray((x, y + radius)),
                "bottom": np.asarray((x, y - radius)),
            }
            if element in points:
                return points[element]
        elif isinstance(entity, ArcEntity):
            x, y, radius, start_angle, sweep_angle = values
            fraction = {"start": 0.0, "midpoint": 0.5, "end": 1.0}.get(element)
            if element in {"entity", "center"}:
                return np.asarray((x, y))
            if fraction is not None:
                angle = math.radians(start_angle + sweep_angle * fraction)
                return np.asarray((x + radius * math.cos(angle), y + radius * math.sin(angle)))
        elif isinstance(entity, (EllipseEntity, EllipticalArcEntity)):
            x, y, major, minor, rotation = values[:5]

            def ellipse_point(parameter_deg: float) -> np.ndarray:
                parameter, angle = math.radians(parameter_deg), math.radians(rotation)
                local_x, local_y = major * math.cos(parameter), minor * math.sin(parameter)
                return np.asarray((x + local_x * math.cos(angle) - local_y * math.sin(angle), y + local_x * math.sin(angle) + local_y * math.cos(angle)))

            if element in {"entity", "center"}:
                return np.asarray((x, y))
            fixed_parameters = {
                "major-positive": 0.0,
                "minor-positive": 90.0,
                "major-negative": 180.0,
                "minor-negative": 270.0,
            }
            if element in fixed_parameters:
                return ellipse_point(fixed_parameters[element])
            if isinstance(entity, EllipticalArcEntity):
                fraction = {"start": 0.0, "midpoint": 0.5, "end": 1.0}.get(element)
                if fraction is not None:
                    return ellipse_point(values[5] + values[6] * fraction)
        elif isinstance(entity, PolylineEntity):
            points = values.reshape((-1, 2))
            if element in {"entity", "start", "vertex-0"}:
                return points[0].copy()
            if element == "end":
                return points[0 if entity.closed else -1].copy()
            if element.startswith("vertex-"):
                try:
                    index = int(element.split("-", 1)[1])
                    return points[index].copy()
                except (ValueError, IndexError):
                    pass
            if element.startswith("segment-") and element.endswith("-midpoint"):
                try:
                    index = int(element.split("-")[1])
                    current = replace(entity, points=tuple(Point2D(*coordinates) for coordinates in points))
                    segment = current.segment_entities[index]
                    midpoint = segment.midpoint if isinstance(segment, ArcEntity) else Point2D(
                        (segment.start.x + segment.end.x) / 2.0,
                        (segment.start.y + segment.end.y) / 2.0,
                    )
                    return np.asarray((midpoint.x, midpoint.y))
                except (ValueError, IndexError):
                    pass
        elif isinstance(entity, BSplineEntity):
            points = values.reshape((-1, 2))
            if element in {"entity", "start", "end", "midpoint"}:
                curve = replace(
                    entity,
                    control_points=tuple(Point2D(*coordinates) for coordinates in points),
                )
                actual = curve.start if element in {"entity", "start"} else curve.end if element == "end" else curve.midpoint
                return np.asarray((actual.x, actual.y))
            if element.startswith("control-"):
                try:
                    return points[int(element.split("-", 1)[1])].copy()
                except (ValueError, IndexError):
                    pass
        elif isinstance(entity, RegularPolygonEntity):
            x, y, radius, rotation = values
            if element in {"entity", "center"}:
                return np.asarray((x, y))
            if element.startswith("vertex-"):
                try:
                    index = int(element.split("-", 1)[1])
                    if not 0 <= index < entity.sides:
                        raise IndexError
                    angle = math.radians(rotation + index * 360.0 / entity.sides)
                    return np.asarray((x + radius * math.cos(angle), y + radius * math.sin(angle)))
                except (ValueError, IndexError):
                    pass
            if element.startswith("segment-") and element.endswith("-midpoint"):
                try:
                    index = int(element.split("-")[1])
                    first = self.point(GeometryReference(entity.id, f"vertex-{index}"), vector)
                    second = self.point(GeometryReference(entity.id, f"vertex-{(index + 1) % entity.sides}"), vector)
                    return (first + second) / 2.0
                except (ValueError, IndexError):
                    pass
        elif isinstance(entity, SlotEntity):
            start, end = values[0:2], values[2:4]
            if element in {"entity", "axis-start", "start"}:
                return start.copy()
            if element in {"axis-end", "end"}:
                return end.copy()
            if element in {"center", "midpoint"}:
                return (start + end) / 2.0
        raise SolverGeometryError(f"Encja {entity.id} nie ma pod-elementu {element!r}.")

    def line_direction(self, reference: GeometryReference, vector: np.ndarray) -> np.ndarray:
        entity = self.sketch.entities.get(reference.entity_id)
        if isinstance(entity, SlotEntity) and reference.element in {"entity", "axis"}:
            values = self.entity_values(entity.id, vector)
            return values[2:4] - values[0:2]
        if isinstance(entity, PolylineEntity) and reference.element.startswith("segment-"):
            try:
                index = int(reference.element.split("-", 1)[1])
                if abs(entity.bulges[index]) > 1e-12:
                    raise SolverGeometryError("Więz kierunkowy wymaga prostego segmentu polilinii, nie łuku bulge.")
                points = self.entity_values(entity.id, vector).reshape((-1, 2))
                next_index = (index + 1) % len(points)
                if not entity.closed and next_index == 0:
                    raise IndexError
                return points[next_index] - points[index]
            except (ValueError, IndexError) as exc:
                raise SolverGeometryError("Nieprawidłowy segment polilinii.") from exc
        if isinstance(entity, RegularPolygonEntity) and reference.element.startswith("segment-"):
            try:
                index = int(reference.element.split("-", 1)[1])
                first = self.point(GeometryReference(entity.id, f"vertex-{index}"), vector)
                second = self.point(GeometryReference(entity.id, f"vertex-{(index + 1) % entity.sides}"), vector)
                return second - first
            except (ValueError, IndexError) as exc:
                raise SolverGeometryError("Nieprawidłowy bok wielokąta.") from exc
        if isinstance(entity, (EllipseEntity, EllipticalArcEntity)) and reference.element in {"major-axis", "minor-axis"}:
            values = self.entity_values(entity.id, vector)
            rotation = math.radians(values[4] + (90.0 if reference.element == "minor-axis" else 0.0))
            radius = values[3] if reference.element == "minor-axis" else values[2]
            return np.asarray((radius * math.cos(rotation), radius * math.sin(rotation)))
        if not isinstance(entity, LineEntity):
            raise SolverGeometryError("Więz kierunkowy wymaga linii lub segmentu polilinii.")
        values = self.entity_values(entity.id, vector)
        return values[2:4] - values[0:2]

    def line_length(self, reference: GeometryReference, vector: np.ndarray) -> float:
        direction = self.line_direction(reference, vector)
        return float(np.linalg.norm(direction))

    def circle_radius(self, reference: GeometryReference, vector: np.ndarray) -> float:
        entity = self.sketch.entities.get(reference.entity_id)
        if isinstance(entity, SlotEntity):
            return float(self.entity_values(entity.id, vector)[4])
        if isinstance(entity, RegularPolygonEntity):
            return float(self.entity_values(entity.id, vector)[2])
        if isinstance(entity, (EllipseEntity, EllipticalArcEntity)):
            values = self.entity_values(entity.id, vector)
            return float(values[3] if reference.element == "minor-radius" else values[2])
        if not isinstance(entity, (CircleEntity, ArcEntity)):
            raise SolverGeometryError("Więz promienia wymaga okręgu, łuku, wielokąta lub szczeliny.")
        return float(self.entity_values(entity.id, vector)[2])

    def entities_from_vector(self, vector: np.ndarray) -> dict[str, SketchEntity]:
        result: dict[str, SketchEntity] = {}
        for entity in self.sketch.ordered_entities():
            values = self.entity_values(entity.id, vector)
            if isinstance(entity, PointEntity):
                result[entity.id] = replace(entity, point=Point2D(values[0], values[1]))
            elif isinstance(entity, LineEntity):
                result[entity.id] = replace(
                    entity,
                    start=Point2D(values[0], values[1]),
                    end=Point2D(values[2], values[3]),
                )
            elif isinstance(entity, RectangleEntity):
                result[entity.id] = replace(
                    entity,
                    origin=Point2D(values[0], values[1]),
                    width=max(float(values[2]), 1e-7),
                    height=max(float(values[3]), 1e-7),
                )
            elif isinstance(entity, CircleEntity):
                result[entity.id] = replace(
                    entity,
                    center=Point2D(values[0], values[1]),
                    radius=max(float(values[2]), 1e-7),
                )
            elif isinstance(entity, ArcEntity):
                result[entity.id] = replace(
                    entity,
                    center=Point2D(values[0], values[1]),
                    radius=max(float(values[2]), 1e-7),
                    start_angle_deg=float(values[3]),
                    sweep_angle_deg=max(-360.0, min(360.0, float(values[4]))),
                )
            elif isinstance(entity, EllipseEntity):
                result[entity.id] = replace(
                    entity,
                    center=Point2D(values[0], values[1]),
                    major_radius=max(float(values[2]), 1e-7),
                    minor_radius=max(1e-7, min(float(values[3]), float(values[2]))),
                    rotation_deg=float(values[4]),
                )
            elif isinstance(entity, EllipticalArcEntity):
                result[entity.id] = replace(
                    entity,
                    center=Point2D(values[0], values[1]),
                    major_radius=max(float(values[2]), 1e-7),
                    minor_radius=max(1e-7, min(float(values[3]), float(values[2]))),
                    rotation_deg=float(values[4]),
                    start_parameter_deg=float(values[5]),
                    sweep_parameter_deg=max(-360.0, min(360.0, float(values[6]))),
                )
            elif isinstance(entity, PolylineEntity):
                result[entity.id] = replace(
                    entity,
                    points=tuple(Point2D(values[index], values[index + 1]) for index in range(0, len(values), 2)),
                )
            elif isinstance(entity, BSplineEntity):
                result[entity.id] = replace(
                    entity,
                    control_points=tuple(Point2D(values[index], values[index + 1]) for index in range(0, len(values), 2)),
                )
            elif isinstance(entity, RegularPolygonEntity):
                result[entity.id] = replace(
                    entity,
                    center=Point2D(values[0], values[1]),
                    radius=max(float(values[2]), 1e-7),
                    rotation_deg=float(values[3]),
                )
            elif isinstance(entity, SlotEntity):
                result[entity.id] = replace(
                    entity,
                    axis_start=Point2D(values[0], values[1]),
                    axis_end=Point2D(values[2], values[3]),
                    radius=max(float(values[4]), 1e-7),
                )
        return result

    def clamp_positive_dimensions(self, vector: np.ndarray) -> None:
        for entity in self.sketch.ordered_entities():
            section = self._slice(entity.id)
            if isinstance(entity, RectangleEntity):
                vector[section.start + 2] = max(vector[section.start + 2], 1e-7)
                vector[section.start + 3] = max(vector[section.start + 3], 1e-7)
            elif isinstance(entity, (CircleEntity, ArcEntity)):
                vector[section.start + 2] = max(vector[section.start + 2], 1e-7)
                if isinstance(entity, ArcEntity):
                    sweep_index = section.start + 4
                    if abs(vector[sweep_index]) <= 1e-7:
                        vector[sweep_index] = 1e-7 if entity.sweep_angle_deg > 0 else -1e-7
                    vector[sweep_index] = max(-360.0, min(360.0, vector[sweep_index]))
            elif isinstance(entity, SlotEntity):
                vector[section.start + 4] = max(vector[section.start + 4], 1e-7)
            elif isinstance(entity, RegularPolygonEntity):
                vector[section.start + 2] = max(vector[section.start + 2], 1e-7)
            elif isinstance(entity, (EllipseEntity, EllipticalArcEntity)):
                vector[section.start + 2] = max(vector[section.start + 2], 1e-7)
                vector[section.start + 3] = max(1e-7, min(vector[section.start + 3], vector[section.start + 2]))
                if isinstance(entity, EllipticalArcEntity):
                    sweep_index = section.start + 6
                    if abs(vector[sweep_index]) <= 1e-7:
                        vector[sweep_index] = 1e-7 if entity.sweep_parameter_deg > 0 else -1e-7
                    vector[sweep_index] = max(-360.0, min(360.0, vector[sweep_index]))


def _required_value(constraint: SketchConstraint) -> float:
    if constraint.value is None:
        raise SolverGeometryError(f"Więz {constraint.constraint_type.value} nie ma wartości.")
    return float(constraint.value)


def _unit_direction(direction: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(direction))
    if length <= 1e-12:
        raise SolverGeometryError("Linia użyta przez więz ma zerową długość.")
    return direction / length


def _constraint_residual(
    parameterization: _Parameterization,
    constraint: SketchConstraint,
    vector: np.ndarray,
) -> np.ndarray:
    references = constraint.references
    kind = constraint.constraint_type
    if kind == ConstraintType.COINCIDENT:
        if len(references) != 2:
            raise SolverGeometryError("Więz zbieżności wymaga dwóch punktów.")
        return parameterization.point(references[0], vector) - parameterization.point(references[1], vector)
    if kind == ConstraintType.POINT_ON_OBJECT:
        if len(references) != 2:
            raise SolverGeometryError("Więz punktu na obiekcie wymaga punktu i krzywej.")
        point = parameterization.point(references[0], vector)
        target = parameterization.sketch.entities.get(references[1].entity_id)
        if isinstance(target, LineEntity):
            direction = _unit_direction(parameterization.line_direction(references[1], vector))
            anchor = parameterization.point(GeometryReference(target.id, "start"), vector)
            offset = point - anchor
            return np.asarray((direction[0] * offset[1] - direction[1] * offset[0],))
        if isinstance(target, (CircleEntity, ArcEntity)):
            center = parameterization.point(GeometryReference(target.id, "center"), vector)
            radius = parameterization.circle_radius(references[1], vector)
            return np.asarray((float(np.linalg.norm(point - center)) - radius,))
        if isinstance(target, RegularPolygonEntity):
            points = np.asarray([
                parameterization.point(GeometryReference(target.id, f"vertex-{index}"), vector)
                for index in range(target.sides)
            ])
            pairs = list(zip(points, np.roll(points, -1, axis=0)))
            distances = []
            for start, end in pairs:
                direction = end - start
                factor = min(1.0, max(0.0, float(np.dot(point - start, direction) / np.dot(direction, direction))))
                distances.append(float(np.linalg.norm(point - (start + direction * factor))))
            return np.asarray((min(distances),))
        if isinstance(target, PolylineEntity):
            values = parameterization.entity_values(target.id, vector)
            current = replace(
                target,
                points=tuple(Point2D(values[index], values[index + 1]) for index in range(0, len(values), 2)),
            )
            model_point = Point2D(float(point[0]), float(point[1]))
            distances = []
            for segment in current.segment_entities:
                if isinstance(segment, ArcEntity):
                    distances.append(model_point.distance_to(segment.nearest_point(model_point)))
                else:
                    direction = np.asarray((segment.end.x - segment.start.x, segment.end.y - segment.start.y))
                    start = np.asarray((segment.start.x, segment.start.y))
                    factor = min(1.0, max(0.0, float(np.dot(point - start, direction) / np.dot(direction, direction))))
                    distances.append(float(np.linalg.norm(point - (start + direction * factor))))
            return np.asarray((min(distances),))
        if isinstance(target, SlotEntity):
            values = parameterization.entity_values(target.id, vector)
            start, end, radius = values[0:2], values[2:4], float(values[4])
            direction = end - start
            factor = min(1.0, max(0.0, float(np.dot(point - start, direction) / np.dot(direction, direction))))
            distance_to_axis = float(np.linalg.norm(point - (start + direction * factor)))
            return np.asarray((distance_to_axis - radius,))
        if isinstance(target, (EllipseEntity, EllipticalArcEntity)):
            values = parameterization.entity_values(target.id, vector)
            center = values[0:2]
            rotation = math.radians(float(values[4]))
            delta = point - center
            local_x = delta[0] * math.cos(rotation) + delta[1] * math.sin(rotation)
            local_y = -delta[0] * math.sin(rotation) + delta[1] * math.cos(rotation)
            return np.asarray(((local_x / values[2]) ** 2 + (local_y / values[3]) ** 2 - 1.0,))
        if isinstance(target, BSplineEntity):
            values = parameterization.entity_values(target.id, vector)
            curve = replace(
                target,
                control_points=tuple(Point2D(values[index], values[index + 1]) for index in range(0, len(values), 2)),
            )
            nearest = curve.nearest_point(Point2D(float(point[0]), float(point[1])))
            return np.asarray((math.hypot(float(point[0]) - nearest.x, float(point[1]) - nearest.y),))
        raise SolverGeometryError("Punkt na obiekcie obsługuje linię, polilinię, B-spline, krzywe kołowe, elipsy lub szczelinę.")
    if kind == ConstraintType.HORIZONTAL:
        direction = parameterization.line_direction(references[0], vector)
        return np.asarray((direction[1],))
    if kind == ConstraintType.VERTICAL:
        direction = parameterization.line_direction(references[0], vector)
        return np.asarray((direction[0],))
    if kind in {ConstraintType.PARALLEL, ConstraintType.PERPENDICULAR, ConstraintType.COLLINEAR}:
        first = _unit_direction(parameterization.line_direction(references[0], vector))
        second = _unit_direction(parameterization.line_direction(references[1], vector))
        if kind == ConstraintType.PARALLEL:
            return np.asarray((first[0] * second[1] - first[1] * second[0],))
        if kind == ConstraintType.PERPENDICULAR:
            return np.asarray((float(np.dot(first, second)),))
        anchor_a = parameterization.point(GeometryReference(references[0].entity_id, "start"), vector)
        anchor_b = parameterization.point(GeometryReference(references[1].entity_id, "start"), vector)
        offset = anchor_b - anchor_a
        return np.asarray((first[0] * second[1] - first[1] * second[0], first[0] * offset[1] - first[1] * offset[0]))
    if kind == ConstraintType.CONCENTRIC:
        first = parameterization.point(GeometryReference(references[0].entity_id, "center"), vector)
        second = parameterization.point(GeometryReference(references[1].entity_id, "center"), vector)
        return first - second
    if kind == ConstraintType.TANGENT:
        if len(references) != 2:
            raise SolverGeometryError("Więz styczności wymaga dwóch krzywych.")
        first_entity = parameterization.sketch.entities.get(references[0].entity_id)
        second_entity = parameterization.sketch.entities.get(references[1].entity_id)
        circular = (CircleEntity, ArcEntity)
        if isinstance(first_entity, LineEntity) and isinstance(second_entity, circular):
            line_ref, circle_ref = references[0], references[1]
        elif isinstance(first_entity, circular) and isinstance(second_entity, LineEntity):
            line_ref, circle_ref = references[1], references[0]
        elif isinstance(first_entity, circular) and isinstance(second_entity, circular):
            first_center = parameterization.point(GeometryReference(first_entity.id, "center"), vector)
            second_center = parameterization.point(GeometryReference(second_entity.id, "center"), vector)
            radii = parameterization.circle_radius(references[0], vector) + parameterization.circle_radius(references[1], vector)
            return np.asarray((float(np.linalg.norm(second_center - first_center)) - radii,))
        else:
            raise SolverGeometryError("Styczność obsługuje linię z okręgiem/łukiem albo dwie geometrie kołowe.")
        direction = _unit_direction(parameterization.line_direction(line_ref, vector))
        anchor = parameterization.point(GeometryReference(line_ref.entity_id, "start"), vector)
        center = parameterization.point(GeometryReference(circle_ref.entity_id, "center"), vector)
        signed_distance = direction[0] * (center[1] - anchor[1]) - direction[1] * (center[0] - anchor[0])
        radius = parameterization.circle_radius(circle_ref, vector)
        return np.asarray((signed_distance * signed_distance - radius * radius,))
    if kind == ConstraintType.EQUAL_LENGTH:
        return np.asarray((parameterization.line_length(references[0], vector) - parameterization.line_length(references[1], vector),))
    if kind == ConstraintType.EQUAL_RADIUS:
        return np.asarray((parameterization.circle_radius(references[0], vector) - parameterization.circle_radius(references[1], vector),))
    if kind == ConstraintType.MIDPOINT:
        point = parameterization.point(references[0], vector)
        midpoint = parameterization.point(GeometryReference(references[1].entity_id, "midpoint"), vector)
        return point - midpoint
    if kind == ConstraintType.LENGTH:
        return np.asarray((parameterization.line_length(references[0], vector) - _required_value(constraint),))
    if kind == ConstraintType.ARC_LENGTH:
        entity = parameterization.sketch.entities.get(references[0].entity_id)
        values = parameterization.entity_values(entity.id, vector)
        if isinstance(entity, ArcEntity):
            length = float(values[2]) * math.radians(abs(float(values[4])))
        elif isinstance(entity, EllipticalArcEntity):
            length = _ellipse_arc_length(float(values[2]), float(values[3]), float(values[5]), float(values[6]))
        else:
            raise SolverGeometryError("Więz długości łuku wymaga łuku kołowego lub eliptycznego.")
        return np.asarray((length - _required_value(constraint),))
    if kind in {ConstraintType.DISTANCE, ConstraintType.DISTANCE_X, ConstraintType.DISTANCE_Y}:
        if len(references) != 2:
            raise SolverGeometryError("Więz odległości wymaga dwóch punktów.")
        delta = parameterization.point(references[1], vector) - parameterization.point(references[0], vector)
        value = _required_value(constraint)
        if kind == ConstraintType.DISTANCE_X:
            return np.asarray((delta[0] - value,))
        if kind == ConstraintType.DISTANCE_Y:
            return np.asarray((delta[1] - value,))
        return np.asarray((float(np.linalg.norm(delta)) - value,))
    if kind in {ConstraintType.X_COORDINATE, ConstraintType.Y_COORDINATE}:
        point = parameterization.point(references[0], vector)
        coordinate = point[0] if kind == ConstraintType.X_COORDINATE else point[1]
        return np.asarray((coordinate - _required_value(constraint),))
    if kind == ConstraintType.RADIUS:
        return np.asarray((parameterization.circle_radius(references[0], vector) - _required_value(constraint),))
    if kind == ConstraintType.DIAMETER:
        return np.asarray((parameterization.circle_radius(references[0], vector) * 2 - _required_value(constraint),))
    if kind == ConstraintType.ANGLE:
        direction = parameterization.line_direction(references[0], vector)
        current = math.atan2(float(direction[1]), float(direction[0]))
        target = math.radians(_required_value(constraint))
        delta = (current - target + math.pi) % (2 * math.pi) - math.pi
        return np.asarray((delta,))
    if kind == ConstraintType.FIX:
        values = parameterization.entity_values(references[0].entity_id, vector)
        if len(constraint.fixed_values) != len(values):
            raise SolverGeometryError("Więz blokujący nie zawiera kompletnego stanu geometrii.")
        return values - np.asarray(constraint.fixed_values, dtype=float)
    raise SolverGeometryError(f"Solver nie obsługuje więzu {kind.value}.")


def _active_constraints(constraints: Iterable[SketchConstraint]) -> list[SketchConstraint]:
    return [constraint for constraint in constraints if constraint.enabled and constraint.driving]


def _residual_vector(
    parameterization: _Parameterization,
    constraints: list[SketchConstraint],
    vector: np.ndarray,
) -> tuple[np.ndarray, list[str], set[str]]:
    values: list[float] = []
    owners: list[str] = []
    broken: set[str] = set()
    for constraint in constraints:
        try:
            residual = _constraint_residual(parameterization, constraint, vector)
        except SolverGeometryError:
            broken.add(constraint.id)
            continue
        values.extend(float(value) for value in residual)
        owners.extend([constraint.id] * len(residual))
    return np.asarray(values, dtype=float), owners, broken


def _jacobian(
    parameterization: _Parameterization,
    constraints: list[SketchConstraint],
    vector: np.ndarray,
    baseline: np.ndarray,
) -> np.ndarray:
    if baseline.size == 0 or vector.size == 0:
        return np.zeros((baseline.size, vector.size), dtype=float)
    jacobian = np.zeros((baseline.size, vector.size), dtype=float)
    for column in range(vector.size):
        epsilon = 1e-6 * max(1.0, abs(float(vector[column])))
        varied = vector.copy()
        varied[column] += epsilon
        residual, _owners, _broken = _residual_vector(parameterization, constraints, varied)
        if residual.shape != baseline.shape:
            raise SolverGeometryError("Zmienił się wymiar układu równań więzów.")
        jacobian[:, column] = (residual - baseline) / epsilon
    return jacobian


@dataclass(frozen=True, slots=True)
class _Attempt:
    vector: np.ndarray
    residual: np.ndarray
    owners: list[str]
    broken: set[str]
    iterations: int
    converged: bool


def _attempt_solve(
    parameterization: _Parameterization,
    constraints: list[SketchConstraint],
    start: np.ndarray,
    *,
    max_iterations: int = MAX_ITERATIONS,
) -> _Attempt:
    vector = start.copy()
    iterations = 0
    for iterations in range(max_iterations + 1):
        residual, owners, broken = _residual_vector(parameterization, constraints, vector)
        norm = float(np.max(np.abs(residual))) if residual.size else 0.0
        if broken:
            return _Attempt(vector, residual, owners, broken, iterations, False)
        if norm <= SOLVER_TOLERANCE:
            return _Attempt(vector, residual, owners, broken, iterations, True)
        jacobian = _jacobian(parameterization, constraints, vector, residual)
        if not np.all(np.isfinite(jacobian)):
            break
        damping = 1e-10
        matrix = jacobian.T @ jacobian + np.eye(vector.size) * damping
        rhs = -(jacobian.T @ residual)
        try:
            delta = np.linalg.solve(matrix, rhs)
        except np.linalg.LinAlgError:
            delta, *_ = np.linalg.lstsq(jacobian, -residual, rcond=None)
        if not np.all(np.isfinite(delta)):
            break
        accepted = False
        current_norm = float(np.linalg.norm(residual))
        for scale in (1.0, 0.5, 0.25, 0.1, 0.05, 0.01):
            candidate = vector + delta * scale
            parameterization.clamp_positive_dimensions(candidate)
            candidate_residual, _candidate_owners, candidate_broken = _residual_vector(parameterization, constraints, candidate)
            if candidate_broken:
                continue
            if float(np.linalg.norm(candidate_residual)) < current_norm:
                vector = candidate
                accepted = True
                break
        if not accepted or float(np.linalg.norm(delta)) <= 1e-11:
            break
    residual, owners, broken = _residual_vector(parameterization, constraints, vector)
    converged = not broken and (float(np.max(np.abs(residual))) if residual.size else 0.0) <= SOLVER_TOLERANCE
    return _Attempt(vector, residual, owners, broken, iterations, converged)


def _constraint_rank_data(
    parameterization: _Parameterization,
    constraints: list[SketchConstraint],
    vector: np.ndarray,
) -> tuple[int, tuple[str, ...]]:
    residual, owners, _broken = _residual_vector(parameterization, constraints, vector)
    jacobian = _jacobian(parameterization, constraints, vector, residual)
    rank = int(np.linalg.matrix_rank(jacobian, tol=1e-8)) if jacobian.size else 0
    redundant: list[str] = []
    accepted_rows: list[int] = []
    previous_rank = 0
    for constraint in constraints:
        rows = [index for index, owner in enumerate(owners) if owner == constraint.id]
        if not rows:
            continue
        candidate_rows = accepted_rows + rows
        candidate_rank = int(np.linalg.matrix_rank(jacobian[candidate_rows, :], tol=1e-8))
        if candidate_rank == previous_rank:
            redundant.append(constraint.id)
        else:
            accepted_rows = candidate_rows
            previous_rank = candidate_rank
    return rank, tuple(redundant)


def _measured_value(parameterization: _Parameterization, constraint: SketchConstraint, vector: np.ndarray) -> float | None:
    try:
        kind = constraint.constraint_type
        refs = constraint.references
        if kind == ConstraintType.LENGTH:
            return parameterization.line_length(refs[0], vector)
        if kind == ConstraintType.ARC_LENGTH:
            entity = parameterization.sketch.entities.get(refs[0].entity_id)
            values = parameterization.entity_values(entity.id, vector)
            if isinstance(entity, ArcEntity):
                return float(values[2]) * math.radians(abs(float(values[4])))
            if isinstance(entity, EllipticalArcEntity):
                return _ellipse_arc_length(float(values[2]), float(values[3]), float(values[5]), float(values[6]))
            raise SolverGeometryError("Pomiar długości łuku wymaga łuku kołowego lub eliptycznego.")
        if kind == ConstraintType.RADIUS:
            return parameterization.circle_radius(refs[0], vector)
        if kind == ConstraintType.DIAMETER:
            return parameterization.circle_radius(refs[0], vector) * 2
        if kind in {ConstraintType.DISTANCE, ConstraintType.DISTANCE_X, ConstraintType.DISTANCE_Y}:
            delta = parameterization.point(refs[1], vector) - parameterization.point(refs[0], vector)
            if kind == ConstraintType.DISTANCE_X:
                return float(delta[0])
            if kind == ConstraintType.DISTANCE_Y:
                return float(delta[1])
            return float(np.linalg.norm(delta))
        if kind in {ConstraintType.X_COORDINATE, ConstraintType.Y_COORDINATE}:
            point = parameterization.point(refs[0], vector)
            return float(point[0] if kind == ConstraintType.X_COORDINATE else point[1])
        if kind == ConstraintType.ANGLE:
            direction = parameterization.line_direction(refs[0], vector)
            return math.degrees(math.atan2(float(direction[1]), float(direction[0])))
    except SolverGeometryError:
        return None
    return None


def solve_sketch(sketch: Sketch) -> SolverResult:
    if not sketch.constraints:
        degrees_of_freedom = sum(
            len(entity.control_points) * 2
            if isinstance(entity, BSplineEntity)
            else len(entity.points) * 2
            if isinstance(entity, PolylineEntity)
            else 5
            if isinstance(entity, (ArcEntity, EllipseEntity, SlotEntity))
            else 7
            if isinstance(entity, EllipticalArcEntity)
            else 4
            if isinstance(entity, (LineEntity, RectangleEntity, RegularPolygonEntity))
            else 3
            if isinstance(entity, CircleEntity)
            else 2
            for entity in sketch.ordered_entities()
        )
        status = SketchSolveStatus.FULLY_CONSTRAINED if degrees_of_freedom == 0 else SketchSolveStatus.UNDER_CONSTRAINED
        return SolverResult(
            status=status,
            degrees_of_freedom=degrees_of_freedom,
            entities=dict(sketch.entities),
            constraints={},
            residual_norm=0.0,
            diagnostic="Szkic jest pusty." if degrees_of_freedom == 0 else f"Szkic ma {degrees_of_freedom} pozostałych stopni swobody.",
        )
    parameterization = _Parameterization(sketch)
    ordered = sketch.ordered_constraints()
    active = _active_constraints(ordered)
    attempt = _attempt_solve(parameterization, active, parameterization.initial)
    broken_ids = tuple(sorted(attempt.broken))
    conflicting: tuple[str, ...] = ()
    redundant: tuple[str, ...] = ()
    vector_for_result = attempt.vector if attempt.converged else parameterization.initial

    if broken_ids:
        status = SketchSolveStatus.UNSOLVABLE
        diagnostic = "Co najmniej jeden więz wskazuje brakującą lub nieprawidłową geometrię."
    elif not attempt.converged:
        conflicts: list[str] = []
        for constraint in active:
            reduced = [item for item in active if item.id != constraint.id]
            reduced_attempt = _attempt_solve(parameterization, reduced, parameterization.initial, max_iterations=30)
            if reduced_attempt.converged:
                conflicts.append(constraint.id)
        conflicting = tuple(conflicts or [owner for owner in dict.fromkeys(attempt.owners)])
        status = SketchSolveStatus.CONFLICTING if conflicting else SketchSolveStatus.UNSOLVABLE
        diagnostic = "Szkic zawiera sprzeczne więzy." if conflicting else "Solver nie znalazł stabilnego rozwiązania."
    else:
        rank, redundant = _constraint_rank_data(parameterization, active, attempt.vector)
        degrees_of_freedom = max(0, int(parameterization.initial.size) - rank)
        if redundant:
            status = SketchSolveStatus.REDUNDANT
            diagnostic = "Szkic zawiera redundantne więzy."
        elif degrees_of_freedom == 0:
            status = SketchSolveStatus.FULLY_CONSTRAINED
            diagnostic = "Szkic jest w pełni związany."
        else:
            status = SketchSolveStatus.UNDER_CONSTRAINED
            diagnostic = f"Szkic ma {degrees_of_freedom} pozostałych stopni swobody."

    if not attempt.converged:
        rank = 0
        degrees_of_freedom = int(parameterization.initial.size)
    constraints: dict[str, SketchConstraint] = {}
    measured: dict[str, float] = {}
    for constraint in ordered:
        if constraint.id in broken_ids:
            constraints[constraint.id] = constraint.with_runtime_status(ConstraintStatus.BROKEN_REFERENCE, "Referencja geometrii jest uszkodzona.")
        elif constraint.id in conflicting:
            constraints[constraint.id] = constraint.with_runtime_status(ConstraintStatus.CONFLICTING, "Więz uczestniczy w konflikcie.")
        elif constraint.id in redundant:
            constraints[constraint.id] = constraint.with_runtime_status(ConstraintStatus.REDUNDANT, "Więz nie odbiera dodatkowego stopnia swobody.")
        elif not constraint.enabled:
            constraints[constraint.id] = constraint.with_runtime_status(ConstraintStatus.DISABLED)
        elif not constraint.driving:
            constraints[constraint.id] = constraint.with_runtime_status(ConstraintStatus.REFERENCE)
        else:
            constraints[constraint.id] = constraint.with_runtime_status(ConstraintStatus.OK)
        if not constraint.driving:
            value = _measured_value(parameterization, constraint, vector_for_result)
            if value is not None:
                measured[constraint.id] = value

    residual_norm = float(np.max(np.abs(attempt.residual))) if attempt.residual.size else 0.0
    return SolverResult(
        status=status,
        degrees_of_freedom=degrees_of_freedom,
        entities=parameterization.entities_from_vector(vector_for_result),
        constraints=constraints,
        residual_norm=residual_norm,
        conflicting_ids=conflicting,
        redundant_ids=redundant,
        broken_reference_ids=broken_ids,
        measured_values=measured,
        iterations=attempt.iterations,
        diagnostic=diagnostic,
    )


def solve_and_apply(sketch: Sketch) -> SolverResult:
    """Solve and commit runtime geometry/status to an already owned sketch.

    Editing code should call this through a command. Loading and migration may
    call it directly because they construct a fresh document with no history.
    """

    result = solve_sketch(sketch)
    sketch.entities.update(result.entities)
    sketch.constraints.update(result.constraints)
    sketch.solve_status = result.status
    sketch.degrees_of_freedom = result.degrees_of_freedom
    sketch.error = "" if result.solved else result.diagnostic
    sketch.diagnostics = [result.diagnostic] if result.diagnostic else []
    for pattern in sketch.ordered_patterns():
        missing: list[str] = []
        if pattern.source_entity_id not in sketch.entities:
            missing.append("brak źródła")
        if any(entity_id not in sketch.entities for entity_id in pattern.generated_entity_ids):
            missing.append("brak wygenerowanej geometrii")
        if any(constraint_id not in sketch.constraints for constraint_id in pattern.constraint_ids):
            missing.append("brak więzu szyku")
        error = ", ".join(missing)
        sketch.patterns[pattern.id] = replace(pattern, error=error)
        if error:
            sketch.diagnostics.append(f"{pattern.label}: {error}.")
    sketch.recompute_status = "dirty"
    return result


def fixed_constraint(sketch: Sketch, entity_id: str, *, name: str = "") -> SketchConstraint:
    parameterization = _Parameterization(sketch)
    values = tuple(float(value) for value in parameterization.entity_values(entity_id, parameterization.initial))
    return SketchConstraint(
        ConstraintType.FIX,
        (GeometryReference(entity_id),),
        name=name,
        fixed_values=values,
    )

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

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
    RectangleEntity,
    RegularPolygonEntity,
    Sketch,
    SlotEntity,
)


class SnapKind(str, Enum):
    ENDPOINT = "endpoint"
    MIDPOINT = "midpoint"
    CENTER = "center"
    ORIGIN = "origin"
    GRID = "grid"
    INTERSECTION = "intersection"
    NEAREST = "nearest"
    AXIS_X = "axis_x"
    AXIS_Y = "axis_y"
    EXTENSION = "extension"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    PARALLEL = "parallel"
    PERPENDICULAR = "perpendicular"
    TANGENT = "tangent"
    INCREMENTAL_ANGLE = "incremental_angle"


@dataclass(frozen=True, slots=True)
class SnapCandidate:
    point: Point2D
    kind: SnapKind
    distance_pixels: float
    entity_id: str | None = None
    sub_element: str = ""
    related: tuple[tuple[str, str], ...] = ()
    suggested_constraint: str = ""


@dataclass(frozen=True, slots=True)
class SnapResult:
    point: Point2D
    active: SnapCandidate | None
    candidates: tuple[SnapCandidate, ...]


_PRIORITY = {
    SnapKind.ENDPOINT: 0,
    SnapKind.INTERSECTION: 1,
    SnapKind.CENTER: 2,
    SnapKind.MIDPOINT: 3,
    SnapKind.TANGENT: 4,
    SnapKind.HORIZONTAL: 5,
    SnapKind.VERTICAL: 5,
    SnapKind.PARALLEL: 6,
    SnapKind.PERPENDICULAR: 6,
    SnapKind.AXIS_X: 7,
    SnapKind.AXIS_Y: 7,
    SnapKind.EXTENSION: 8,
    SnapKind.NEAREST: 9,
    SnapKind.ORIGIN: 10,
    SnapKind.INCREMENTAL_ANGLE: 11,
    SnapKind.GRID: 12,
}


def _projection(point: Point2D, start: Point2D, end: Point2D) -> tuple[Point2D, float]:
    dx, dy = end.x - start.x, end.y - start.y
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        return start, 0.0
    factor = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
    return Point2D(start.x + factor * dx, start.y + factor * dy), factor


def _line_intersection(first: LineEntity, second: LineEntity, tolerance: float = 1e-9) -> Point2D | None:
    ax, ay = first.start.x, first.start.y
    bx, by = first.end.x - ax, first.end.y - ay
    cx, cy = second.start.x, second.start.y
    dx, dy = second.end.x - cx, second.end.y - cy
    denominator = bx * dy - by * dx
    if abs(denominator) <= tolerance:
        return None
    offset_x, offset_y = cx - ax, cy - ay
    first_factor = (offset_x * dy - offset_y * dx) / denominator
    second_factor = (offset_x * by - offset_y * bx) / denominator
    if -tolerance <= first_factor <= 1 + tolerance and -tolerance <= second_factor <= 1 + tolerance:
        return Point2D(ax + first_factor * bx, ay + first_factor * by)
    return None


def _line_circle_intersections(line: LineEntity, circle: CircleEntity, tolerance: float = 1e-9) -> tuple[Point2D, ...]:
    dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
    fx, fy = line.start.x - circle.center.x, line.start.y - circle.center.y
    aa = dx * dx + dy * dy
    bb = 2 * (fx * dx + fy * dy)
    cc = fx * fx + fy * fy - circle.radius * circle.radius
    discriminant = bb * bb - 4 * aa * cc
    if discriminant < -tolerance:
        return ()
    roots = (-bb / (2 * aa),) if abs(discriminant) <= tolerance else (
        (-bb - math.sqrt(max(0.0, discriminant))) / (2 * aa),
        (-bb + math.sqrt(max(0.0, discriminant))) / (2 * aa),
    )
    return tuple(
        Point2D(line.start.x + factor * dx, line.start.y + factor * dy)
        for factor in roots
        if -tolerance <= factor <= 1 + tolerance
    )


def _circle_intersections(first: CircleEntity, second: CircleEntity, tolerance: float = 1e-9) -> tuple[Point2D, ...]:
    dx, dy = second.center.x - first.center.x, second.center.y - first.center.y
    distance = math.hypot(dx, dy)
    if distance <= tolerance or distance > first.radius + second.radius + tolerance:
        return ()
    if distance < abs(first.radius - second.radius) - tolerance:
        return ()
    along = (first.radius**2 - second.radius**2 + distance**2) / (2 * distance)
    height_sq = first.radius**2 - along**2
    if height_sq < -tolerance:
        return ()
    base = Point2D(first.center.x + along * dx / distance, first.center.y + along * dy / distance)
    if abs(height_sq) <= tolerance:
        return (base,)
    height = math.sqrt(max(0.0, height_sq))
    ox, oy = -dy * height / distance, dx * height / distance
    return (Point2D(base.x + ox, base.y + oy), Point2D(base.x - ox, base.y - oy))


def _line_ellipse_intersections(
    line: LineEntity,
    ellipse: EllipseEntity | EllipticalArcEntity,
    tolerance: float = 1e-9,
) -> tuple[Point2D, ...]:
    rotation = math.radians(ellipse.rotation_deg)

    def local(point: Point2D) -> tuple[float, float]:
        dx, dy = point.x - ellipse.center.x, point.y - ellipse.center.y
        return dx * math.cos(rotation) + dy * math.sin(rotation), -dx * math.sin(rotation) + dy * math.cos(rotation)

    start_x, start_y = local(line.start)
    end_x, end_y = local(line.end)
    dx, dy = end_x - start_x, end_y - start_y
    aa = (dx / ellipse.major_radius) ** 2 + (dy / ellipse.minor_radius) ** 2
    bb = 2.0 * (start_x * dx / ellipse.major_radius**2 + start_y * dy / ellipse.minor_radius**2)
    cc = (start_x / ellipse.major_radius) ** 2 + (start_y / ellipse.minor_radius) ** 2 - 1.0
    discriminant = bb * bb - 4.0 * aa * cc
    if aa <= tolerance or discriminant < -tolerance:
        return ()
    roots = (-bb / (2.0 * aa),) if abs(discriminant) <= tolerance else (
        (-bb - math.sqrt(max(0.0, discriminant))) / (2.0 * aa),
        (-bb + math.sqrt(max(0.0, discriminant))) / (2.0 * aa),
    )
    result = []
    for factor in roots:
        if not -tolerance <= factor <= 1.0 + tolerance:
            continue
        point = Point2D(
            line.start.x + factor * (line.end.x - line.start.x),
            line.start.y + factor * (line.end.y - line.start.y),
        )
        if isinstance(ellipse, EllipticalArcEntity) and not ellipse.contains_parameter(ellipse.parameter_for_point(point), 1e-7):
            continue
        result.append(point)
    return tuple(result)


def _ellipse_curve_point(entity: EllipseEntity | EllipticalArcEntity, parameter_deg: float) -> Point2D:
    if isinstance(entity, EllipseEntity):
        return entity.point_at_parameter(parameter_deg)
    parameter = (parameter_deg - entity.start_parameter_deg) / entity.sweep_parameter_deg
    return entity.point_at(parameter)


def _curve_implicit_residual(
    entity: CircleEntity | ArcEntity | EllipseEntity | EllipticalArcEntity,
    point: Point2D,
) -> float:
    if isinstance(entity, (CircleEntity, ArcEntity)):
        return ((point.x - entity.center.x) ** 2 + (point.y - entity.center.y) ** 2) / entity.radius**2 - 1.0
    rotation = math.radians(entity.rotation_deg)
    dx, dy = point.x - entity.center.x, point.y - entity.center.y
    local_x = dx * math.cos(rotation) + dy * math.sin(rotation)
    local_y = -dx * math.sin(rotation) + dy * math.cos(rotation)
    return (local_x / entity.major_radius) ** 2 + (local_y / entity.minor_radius) ** 2 - 1.0


def _ellipse_curve_intersections(
    first: EllipseEntity | EllipticalArcEntity,
    second: CircleEntity | ArcEntity | EllipseEntity | EllipticalArcEntity,
) -> tuple[Point2D, ...]:
    start = first.start_parameter_deg if isinstance(first, EllipticalArcEntity) else 0.0
    sweep = first.sweep_parameter_deg if isinstance(first, EllipticalArcEntity) else 360.0
    count = max(720, int(abs(sweep) * 4.0))
    parameters = [start + sweep * index / count for index in range(count + 1)]
    residuals = [_curve_implicit_residual(second, _ellipse_curve_point(first, parameter)) for parameter in parameters]
    roots: list[float] = []
    for index in range(count):
        left, right = parameters[index], parameters[index + 1]
        left_value, right_value = residuals[index], residuals[index + 1]
        if abs(left_value) <= 1e-10:
            roots.append(left)
        if left_value * right_value < 0.0:
            for _ in range(50):
                middle = (left + right) / 2.0
                middle_value = _curve_implicit_residual(second, _ellipse_curve_point(first, middle))
                if left_value * middle_value <= 0.0:
                    right, right_value = middle, middle_value
                else:
                    left, left_value = middle, middle_value
            roots.append((left + right) / 2.0)
    for index in range(1, count):
        if abs(residuals[index]) >= min(abs(residuals[index - 1]), abs(residuals[index + 1])) or abs(residuals[index]) > 1e-3:
            continue
        parameter = parameters[index]
        for _ in range(20):
            value = _curve_implicit_residual(second, _ellipse_curve_point(first, parameter))
            delta = 1e-5
            derivative = (
                _curve_implicit_residual(second, _ellipse_curve_point(first, parameter + delta))
                - _curve_implicit_residual(second, _ellipse_curve_point(first, parameter - delta))
            ) / (2.0 * delta)
            if abs(derivative) <= 1e-12:
                break
            step = max(-1.0, min(1.0, value / derivative))
            parameter -= step
            if abs(step) <= 1e-10:
                break
        if min(start, start + sweep) - 1e-7 <= parameter <= max(start, start + sweep) + 1e-7:
            if abs(_curve_implicit_residual(second, _ellipse_curve_point(first, parameter))) <= 1e-8:
                roots.append(parameter)
    points = []
    for parameter in roots:
        point = _ellipse_curve_point(first, parameter)
        if isinstance(second, ArcEntity) and not _on_curve(second, point):
            continue
        if isinstance(second, EllipticalArcEntity) and not second.contains_parameter(second.parameter_for_point(point), 1e-6):
            continue
        if not any(point.distance_to(existing) <= 1e-7 for existing in points):
            points.append(point)
    return tuple(points)


def _bspline_residual_intersections(
    curve: BSplineEntity,
    residual,
    validator=lambda _point: True,
) -> tuple[Point2D, ...]:
    """Find roots on an exact B-spline; sampling only brackets the roots."""
    start, end = curve.parameter_domain
    count = max(384, len(curve.control_points) * 96)
    parameters = [start + (end - start) * index / count for index in range(count + 1)]
    values = [float(residual(curve.point_at_parameter(parameter))) for parameter in parameters]
    roots: list[float] = []
    for index in range(count):
        left, right = parameters[index], parameters[index + 1]
        left_value, right_value = values[index], values[index + 1]
        if abs(left_value) <= 1e-10:
            roots.append(left)
        if left_value * right_value < 0.0:
            for _ in range(48):
                middle = (left + right) / 2.0
                middle_value = float(residual(curve.point_at_parameter(middle)))
                if left_value * middle_value <= 0.0:
                    right, right_value = middle, middle_value
                else:
                    left, left_value = middle, middle_value
            roots.append((left + right) / 2.0)
    points: list[Point2D] = []
    for parameter in roots:
        point = curve.point_at_parameter(parameter)
        if validator(point) and not any(point.distance_to(existing) <= 1e-7 for existing in points):
            points.append(point)
    return tuple(points)


def _bspline_line_intersections(curve: BSplineEntity, line: LineEntity) -> tuple[Point2D, ...]:
    dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
    length_sq = dx * dx + dy * dy

    def residual(point: Point2D) -> float:
        return (point.x - line.start.x) * dy - (point.y - line.start.y) * dx

    def on_segment(point: Point2D) -> bool:
        factor = ((point.x - line.start.x) * dx + (point.y - line.start.y) * dy) / length_sq
        return -1e-8 <= factor <= 1.0 + 1e-8

    return _bspline_residual_intersections(curve, residual, on_segment)


def _bspline_curve_intersections(
    curve: BSplineEntity,
    target: CircleEntity | ArcEntity | EllipseEntity | EllipticalArcEntity,
) -> tuple[Point2D, ...]:
    def validator(point: Point2D) -> bool:
        if isinstance(target, ArcEntity):
            return _on_curve(target, point)
        if isinstance(target, EllipticalArcEntity):
            return target.contains_parameter(target.parameter_for_point(point), 1e-6)
        return True

    return _bspline_residual_intersections(curve, lambda point: _curve_implicit_residual(target, point), validator)


def _support_circle(entity: CircleEntity | ArcEntity) -> CircleEntity:
    return entity if isinstance(entity, CircleEntity) else CircleEntity(entity.center, entity.radius)


def _on_curve(entity: CircleEntity | ArcEntity, point: Point2D) -> bool:
    if isinstance(entity, CircleEntity):
        return True
    angle = math.degrees(math.atan2(point.y - entity.center.y, point.x - entity.center.x))
    return entity.contains_angle(angle, 1e-7)


def _line_components(entity: object) -> tuple[LineEntity, ...]:
    if isinstance(entity, LineEntity):
        return (entity,)
    if isinstance(entity, RectangleEntity):
        corners = entity.corners
        return tuple(LineEntity(corners[index], corners[(index + 1) % 4]) for index in range(4))
    if isinstance(entity, PolylineEntity):
        return tuple(segment for segment in entity.segment_entities if isinstance(segment, LineEntity))
    if isinstance(entity, RegularPolygonEntity):
        return tuple(LineEntity(start, end) for start, end in entity.segments)
    if isinstance(entity, SlotEntity):
        return entity.boundary_lines
    return ()


def _circular_components(entity: object) -> tuple[CircleEntity | ArcEntity, ...]:
    if isinstance(entity, (CircleEntity, ArcEntity)):
        return (entity,)
    if isinstance(entity, SlotEntity):
        return entity.boundary_arcs
    if isinstance(entity, PolylineEntity):
        return tuple(segment for segment in entity.segment_entities if isinstance(segment, ArcEntity))
    return ()


def _elliptical_components(entity: object) -> tuple[EllipseEntity | EllipticalArcEntity, ...]:
    if isinstance(entity, (EllipseEntity, EllipticalArcEntity)):
        return (entity,)
    return ()


def _entity_intersections(first: object, second: object) -> tuple[Point2D, ...]:
    first_lines, second_lines = _line_components(first), _line_components(second)
    first_circles, second_circles = _circular_components(first), _circular_components(second)
    first_ellipses, second_ellipses = _elliptical_components(first), _elliptical_components(second)
    result: list[Point2D] = []
    for first_line in first_lines:
        for second_line in second_lines:
            point = _line_intersection(first_line, second_line)
            if point is not None:
                result.append(point)
    for line in first_lines:
        for curve in second_circles:
            result.extend(point for point in _line_circle_intersections(line, _support_circle(curve)) if _on_curve(curve, point))
    for line in second_lines:
        for curve in first_circles:
            result.extend(point for point in _line_circle_intersections(line, _support_circle(curve)) if _on_curve(curve, point))
    for first_curve in first_circles:
        for second_curve in second_circles:
            result.extend(
                point for point in _circle_intersections(_support_circle(first_curve), _support_circle(second_curve))
                if _on_curve(first_curve, point) and _on_curve(second_curve, point)
            )
    for line in first_lines:
        for ellipse in second_ellipses:
            result.extend(_line_ellipse_intersections(line, ellipse))
    for line in second_lines:
        for ellipse in first_ellipses:
            result.extend(_line_ellipse_intersections(line, ellipse))
    for ellipse in first_ellipses:
        for curve in second_circles:
            result.extend(_ellipse_curve_intersections(ellipse, curve))
    for ellipse in second_ellipses:
        for curve in first_circles:
            result.extend(_ellipse_curve_intersections(ellipse, curve))
    for first_ellipse in first_ellipses:
        for second_ellipse in second_ellipses:
            result.extend(_ellipse_curve_intersections(first_ellipse, second_ellipse))
    if isinstance(first, BSplineEntity):
        for line in second_lines:
            result.extend(_bspline_line_intersections(first, line))
        for curve in (*second_circles, *second_ellipses):
            result.extend(_bspline_curve_intersections(first, curve))
    if isinstance(second, BSplineEntity):
        for line in first_lines:
            result.extend(_bspline_line_intersections(second, line))
        for curve in (*first_circles, *first_ellipses):
            result.extend(_bspline_curve_intersections(second, curve))
    unique = {(round(point.x, 9), round(point.y, 9)): point for point in result}
    return tuple(unique[key] for key in sorted(unique))


class SnapEngine:
    def __init__(self, radius_pixels: float = 14.0, grid_size: float = 10.0) -> None:
        self.radius_pixels = max(1.0, float(radius_pixels))
        self.grid_size = max(1e-7, float(grid_size))
        self.endpoint_enabled = True
        self.midpoint_enabled = True
        self.center_enabled = True
        self.origin_enabled = True
        self.grid_enabled = True
        self.intersection_enabled = True
        self.nearest_enabled = True
        self.axis_enabled = True
        self.extension_enabled = True
        self.direction_enabled = True
        self.tangent_enabled = True
        self.incremental_angle_enabled = True
        self.angle_increment_degrees = 15.0

    def _geometry_points(self, sketch: Sketch, excluded: set[str]) -> Iterable[tuple[Point2D, SnapKind, str, str]]:
        for entity in sketch.ordered_entities():
            if entity.id in excluded or not entity.visible:
                continue
            if isinstance(entity, PointEntity):
                if self.endpoint_enabled:
                    yield entity.point, SnapKind.ENDPOINT, entity.id, "point"
            elif isinstance(entity, LineEntity):
                if self.endpoint_enabled:
                    yield entity.start, SnapKind.ENDPOINT, entity.id, "start"
                    yield entity.end, SnapKind.ENDPOINT, entity.id, "end"
                if self.midpoint_enabled:
                    yield Point2D((entity.start.x + entity.end.x) / 2, (entity.start.y + entity.end.y) / 2), SnapKind.MIDPOINT, entity.id, "midpoint"
            elif isinstance(entity, RectangleEntity):
                if self.endpoint_enabled:
                    for index, corner in enumerate(entity.corners):
                        yield corner, SnapKind.ENDPOINT, entity.id, f"corner-{index}"
                if self.center_enabled:
                    yield entity.center, SnapKind.CENTER, entity.id, "center"
            elif isinstance(entity, CircleEntity):
                if self.center_enabled:
                    yield entity.center, SnapKind.CENTER, entity.id, "center"
            elif isinstance(entity, ArcEntity):
                if self.endpoint_enabled:
                    yield entity.start, SnapKind.ENDPOINT, entity.id, "start"
                    yield entity.end, SnapKind.ENDPOINT, entity.id, "end"
                if self.midpoint_enabled:
                    yield entity.midpoint, SnapKind.MIDPOINT, entity.id, "midpoint"
                if self.center_enabled:
                    yield entity.center, SnapKind.CENTER, entity.id, "center"
            elif isinstance(entity, EllipseEntity):
                if self.endpoint_enabled:
                    yield entity.major_positive, SnapKind.ENDPOINT, entity.id, "major-positive"
                    yield entity.major_negative, SnapKind.ENDPOINT, entity.id, "major-negative"
                    yield entity.minor_positive, SnapKind.ENDPOINT, entity.id, "minor-positive"
                    yield entity.minor_negative, SnapKind.ENDPOINT, entity.id, "minor-negative"
                if self.center_enabled:
                    yield entity.center, SnapKind.CENTER, entity.id, "center"
            elif isinstance(entity, EllipticalArcEntity):
                if self.endpoint_enabled:
                    yield entity.start, SnapKind.ENDPOINT, entity.id, "start"
                    yield entity.end, SnapKind.ENDPOINT, entity.id, "end"
                if self.midpoint_enabled:
                    yield entity.midpoint, SnapKind.MIDPOINT, entity.id, "midpoint"
                if self.center_enabled:
                    yield entity.center, SnapKind.CENTER, entity.id, "center"
            elif isinstance(entity, BSplineEntity):
                if self.endpoint_enabled:
                    yield entity.start, SnapKind.ENDPOINT, entity.id, "start"
                    yield entity.end, SnapKind.ENDPOINT, entity.id, "end"
                    for index, control_point in enumerate(entity.control_points[1:-1], start=1):
                        yield control_point, SnapKind.ENDPOINT, entity.id, f"control-{index}"
                if self.midpoint_enabled:
                    yield entity.midpoint, SnapKind.MIDPOINT, entity.id, "midpoint"
            elif isinstance(entity, PolylineEntity):
                if self.endpoint_enabled:
                    for index, vertex in enumerate(entity.points):
                        yield vertex, SnapKind.ENDPOINT, entity.id, f"vertex-{index}"
                if self.midpoint_enabled:
                    for index, segment in enumerate(entity.segment_entities):
                        midpoint = segment.midpoint if isinstance(segment, ArcEntity) else Point2D(
                            (segment.start.x + segment.end.x) / 2,
                            (segment.start.y + segment.end.y) / 2,
                        )
                        yield midpoint, SnapKind.MIDPOINT, entity.id, f"segment-{index}-midpoint"
            elif isinstance(entity, RegularPolygonEntity):
                if self.endpoint_enabled:
                    for index, vertex in enumerate(entity.points):
                        yield vertex, SnapKind.ENDPOINT, entity.id, f"vertex-{index}"
                if self.midpoint_enabled:
                    for index, (start, end) in enumerate(entity.segments):
                        yield Point2D((start.x + end.x) / 2, (start.y + end.y) / 2), SnapKind.MIDPOINT, entity.id, f"segment-{index}-midpoint"
                if self.center_enabled:
                    yield entity.center, SnapKind.CENTER, entity.id, "center"
            elif isinstance(entity, SlotEntity):
                if self.endpoint_enabled:
                    for index, point in enumerate(entity.boundary_points):
                        yield point, SnapKind.ENDPOINT, entity.id, f"boundary-{index}"
                if self.center_enabled:
                    yield entity.center, SnapKind.CENTER, entity.id, "center"
                    yield entity.axis_start, SnapKind.CENTER, entity.id, "axis-start"
                    yield entity.axis_end, SnapKind.CENTER, entity.id, "axis-end"

    def query(
        self,
        sketch: Sketch,
        point: Point2D,
        *,
        pixels_per_unit: float,
        exclude_ids: Iterable[str] = (),
        candidate_index: int = 0,
        start: Point2D | None = None,
        tool: str = "",
    ) -> SnapResult:
        scale = max(abs(float(pixels_per_unit)), 1e-9)
        excluded = set(exclude_ids)
        candidates: list[SnapCandidate] = []
        for candidate_point, kind, entity_id, sub_element in self._geometry_points(sketch, excluded):
            distance_pixels = point.distance_to(candidate_point) * scale
            if distance_pixels <= self.radius_pixels:
                candidates.append(SnapCandidate(candidate_point, kind, distance_pixels, entity_id, sub_element))
        radius_model = self.radius_pixels / scale
        nearby = [
            entity
            for entity in sketch.ordered_entities()
            if entity.id not in excluded
            and entity.visible
            and entity.bounds()[0] - radius_model <= point.x <= entity.bounds()[2] + radius_model
            and entity.bounds()[1] - radius_model <= point.y <= entity.bounds()[3] + radius_model
        ]
        if self.intersection_enabled:
            for first_index, first in enumerate(nearby):
                for second in nearby[first_index + 1:]:
                    intersections = _entity_intersections(first, second)
                    for intersection in intersections:
                        distance_pixels = point.distance_to(intersection) * scale
                        if distance_pixels <= self.radius_pixels:
                            candidates.append(SnapCandidate(
                                intersection,
                                SnapKind.INTERSECTION,
                                distance_pixels,
                                first.id,
                                "intersection",
                                ((first.id, "entity"), (second.id, "entity")),
                                "point_on_object",
                            ))
        if self.nearest_enabled or self.extension_enabled:
            for entity in sketch.ordered_entities():
                if entity.id in excluded or not entity.visible:
                    continue
                if isinstance(entity, LineEntity):
                    projected, factor = _projection(point, entity.start, entity.end)
                    kind = SnapKind.NEAREST if 0 <= factor <= 1 else SnapKind.EXTENSION
                    if (kind == SnapKind.NEAREST and not self.nearest_enabled) or (kind == SnapKind.EXTENSION and not self.extension_enabled):
                        continue
                    distance_pixels = point.distance_to(projected) * scale
                    if distance_pixels <= self.radius_pixels:
                        candidates.append(SnapCandidate(
                            projected, kind, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                        ))
                elif isinstance(entity, CircleEntity) and self.nearest_enabled:
                    dx, dy = point.x - entity.center.x, point.y - entity.center.y
                    distance = math.hypot(dx, dy)
                    if distance > 1e-12:
                        nearest = Point2D(entity.center.x + dx * entity.radius / distance, entity.center.y + dy * entity.radius / distance)
                        distance_pixels = point.distance_to(nearest) * scale
                        if distance_pixels <= self.radius_pixels:
                            candidates.append(SnapCandidate(
                                nearest, SnapKind.NEAREST, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                            ))
                elif isinstance(entity, ArcEntity) and self.nearest_enabled:
                    nearest = entity.nearest_point(point)
                    distance_pixels = point.distance_to(nearest) * scale
                    if distance_pixels <= self.radius_pixels:
                        candidates.append(SnapCandidate(
                            nearest, SnapKind.NEAREST, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                        ))
                elif isinstance(entity, (EllipseEntity, EllipticalArcEntity)) and self.nearest_enabled:
                    nearest = entity.nearest_point(point)
                    distance_pixels = point.distance_to(nearest) * scale
                    if distance_pixels <= self.radius_pixels:
                        candidates.append(SnapCandidate(
                            nearest, SnapKind.NEAREST, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                        ))
                elif isinstance(entity, BSplineEntity) and self.nearest_enabled:
                    nearest = entity.nearest_point(point)
                    distance_pixels = point.distance_to(nearest) * scale
                    if distance_pixels <= self.radius_pixels:
                        candidates.append(SnapCandidate(
                            nearest, SnapKind.NEAREST, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                        ))
                elif isinstance(entity, PolylineEntity) and self.nearest_enabled:
                    nearest_points = []
                    for segment in entity.segment_entities:
                        if isinstance(segment, ArcEntity):
                            nearest_points.append(segment.nearest_point(point))
                        else:
                            projected, factor = _projection(point, segment.start, segment.end)
                            factor = min(1.0, max(0.0, factor))
                            nearest_points.append(Point2D(
                                segment.start.x + (segment.end.x - segment.start.x) * factor,
                                segment.start.y + (segment.end.y - segment.start.y) * factor,
                            ))
                    nearest = min(nearest_points, key=point.distance_to)
                    distance_pixels = point.distance_to(nearest) * scale
                    if distance_pixels <= self.radius_pixels:
                        candidates.append(SnapCandidate(
                            nearest, SnapKind.NEAREST, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                        ))
                elif isinstance(entity, RegularPolygonEntity) and self.nearest_enabled:
                    nearest_points = []
                    for start, end in entity.segments:
                        projected, factor = _projection(point, start, end)
                        factor = min(1.0, max(0.0, factor))
                        nearest_points.append(Point2D(start.x + (end.x - start.x) * factor, start.y + (end.y - start.y) * factor))
                    nearest = min(nearest_points, key=point.distance_to)
                    distance_pixels = point.distance_to(nearest) * scale
                    if distance_pixels <= self.radius_pixels:
                        candidates.append(SnapCandidate(
                            nearest, SnapKind.NEAREST, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                        ))
                elif isinstance(entity, SlotEntity) and self.nearest_enabled:
                    projected, factor = _projection(point, entity.axis_start, entity.axis_end)
                    factor = min(1.0, max(0.0, factor))
                    axis_point = Point2D(
                        entity.axis_start.x + (entity.axis_end.x - entity.axis_start.x) * factor,
                        entity.axis_start.y + (entity.axis_end.y - entity.axis_start.y) * factor,
                    )
                    dx, dy = point.x - axis_point.x, point.y - axis_point.y
                    distance = math.hypot(dx, dy)
                    if distance <= 1e-12:
                        dx = -(entity.axis_end.y - entity.axis_start.y) / entity.axis_length
                        dy = (entity.axis_end.x - entity.axis_start.x) / entity.axis_length
                        distance = 1.0
                    nearest = Point2D(axis_point.x + dx * entity.radius / distance, axis_point.y + dy * entity.radius / distance)
                    distance_pixels = point.distance_to(nearest) * scale
                    if distance_pixels <= self.radius_pixels:
                        candidates.append(SnapCandidate(
                            nearest, SnapKind.NEAREST, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "point_on_object"
                        ))
        if self.axis_enabled:
            for axis_point, kind, constraint in (
                (Point2D(point.x, 0.0), SnapKind.AXIS_X, "y_coordinate"),
                (Point2D(0.0, point.y), SnapKind.AXIS_Y, "x_coordinate"),
            ):
                distance_pixels = point.distance_to(axis_point) * scale
                if distance_pixels <= self.radius_pixels:
                    candidates.append(SnapCandidate(axis_point, kind, distance_pixels, None, kind.value, (), constraint))
        if start is not None and tool in {"line", "rect", "rect_center", "polyline", "polygon", "slot"}:
            contextual = (
                (Point2D(point.x, start.y), SnapKind.HORIZONTAL, "horizontal"),
                (Point2D(start.x, point.y), SnapKind.VERTICAL, "vertical"),
            )
            for candidate_point, kind, constraint in contextual:
                distance_pixels = point.distance_to(candidate_point) * scale
                if self.direction_enabled and distance_pixels <= self.radius_pixels:
                    candidates.append(SnapCandidate(candidate_point, kind, distance_pixels, None, kind.value, (), constraint))
        if start is not None and tool in {"line", "polyline"} and start.distance_to(point) > 1e-9:
            if self.direction_enabled:
                for entity in sketch.ordered_entities():
                    if not isinstance(entity, LineEntity) or entity.id in excluded:
                        continue
                    ux = (entity.end.x - entity.start.x) / entity.length
                    uy = (entity.end.y - entity.start.y) / entity.length
                    for kind, vx, vy, constraint in (
                        (SnapKind.PARALLEL, ux, uy, "parallel"),
                        (SnapKind.PERPENDICULAR, -uy, ux, "perpendicular"),
                    ):
                        distance_along = (point.x - start.x) * vx + (point.y - start.y) * vy
                        if distance_along < 0:
                            vx, vy, distance_along = -vx, -vy, -distance_along
                        candidate_point = Point2D(start.x + vx * distance_along, start.y + vy * distance_along)
                        distance_pixels = point.distance_to(candidate_point) * scale
                        if distance_pixels <= self.radius_pixels:
                            candidates.append(SnapCandidate(
                                candidate_point, kind, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), constraint
                            ))
            if self.incremental_angle_enabled:
                dx, dy = point.x - start.x, point.y - start.y
                distance = math.hypot(dx, dy)
                increment = math.radians(max(0.1, self.angle_increment_degrees))
                angle = round(math.atan2(dy, dx) / increment) * increment
                candidate_point = Point2D(start.x + distance * math.cos(angle), start.y + distance * math.sin(angle))
                distance_pixels = point.distance_to(candidate_point) * scale
                if distance_pixels <= self.radius_pixels:
                    candidates.append(SnapCandidate(
                        candidate_point, SnapKind.INCREMENTAL_ANGLE, distance_pixels, None, f"{math.degrees(angle):g}", (), "angle"
                    ))
            if self.tangent_enabled:
                for entity in sketch.ordered_entities():
                    if not isinstance(entity, (CircleEntity, ArcEntity)) or entity.id in excluded:
                        continue
                    dx, dy = entity.center.x - start.x, entity.center.y - start.y
                    center_distance = math.hypot(dx, dy)
                    if center_distance <= entity.radius + 1e-9:
                        continue
                    base_angle = math.atan2(dy, dx)
                    offset = math.acos(entity.radius / center_distance)
                    for angle in (base_angle - offset, base_angle + offset):
                        tangent = Point2D(
                            entity.center.x + entity.radius * math.cos(angle + math.pi),
                            entity.center.y + entity.radius * math.sin(angle + math.pi),
                        )
                        distance_pixels = point.distance_to(tangent) * scale
                        if distance_pixels <= self.radius_pixels:
                            candidates.append(SnapCandidate(
                                tangent, SnapKind.TANGENT, distance_pixels, entity.id, "entity", ((entity.id, "entity"),), "tangent"
                            ))
        if self.origin_enabled:
            origin = Point2D(0.0, 0.0)
            distance_pixels = point.distance_to(origin) * scale
            if distance_pixels <= self.radius_pixels:
                candidates.append(SnapCandidate(origin, SnapKind.ORIGIN, distance_pixels, None, "origin"))
        if self.grid_enabled:
            grid = Point2D(
                round(point.x / self.grid_size) * self.grid_size,
                round(point.y / self.grid_size) * self.grid_size,
            )
            distance_pixels = point.distance_to(grid) * scale
            if distance_pixels <= self.radius_pixels:
                candidates.append(SnapCandidate(grid, SnapKind.GRID, distance_pixels, None, "grid"))
        unique: dict[tuple[object, ...], SnapCandidate] = {}
        for candidate in candidates:
            key = (candidate.kind, round(candidate.point.x, 9), round(candidate.point.y, 9), candidate.entity_id, candidate.sub_element)
            previous = unique.get(key)
            if previous is None or candidate.distance_pixels < previous.distance_pixels:
                unique[key] = candidate
        candidates = list(unique.values())
        candidates.sort(key=lambda item: (_PRIORITY[item.kind], round(item.distance_pixels, 9), item.entity_id or "", item.sub_element))
        if not candidates:
            return SnapResult(point, None, ())
        active = candidates[candidate_index % len(candidates)]
        return SnapResult(active.point, active, tuple(candidates))

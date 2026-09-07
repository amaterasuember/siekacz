from __future__ import annotations

import math
from dataclasses import dataclass, replace

from cad.model import ArcEntity, CadValidationError, CircleEntity, LineEntity, Point2D, SketchEntity, new_id


EDIT_TOLERANCE = 1e-7
EditableCurve = LineEntity | ArcEntity | CircleEntity


@dataclass(frozen=True, slots=True)
class CurveIntersection:
    point: Point2D
    first_parameter: float
    second_parameter: float


@dataclass(frozen=True, slots=True)
class TopologyEdit:
    operation: str
    source_id: str
    pieces: tuple[SketchEntity, ...]
    pick_point: Point2D

    def __post_init__(self) -> None:
        if not self.pieces:
            raise CadValidationError("Operacja topologiczna nie może usunąć całej geometrii.")
        if self.pieces[0].id != self.source_id:
            raise CadValidationError("Pierwszy wynik operacji musi zachować UUID geometrii źródłowej.")
        ids = [piece.id for piece in self.pieces]
        if len(set(ids)) != len(ids):
            raise CadValidationError("Wynik operacji zawiera powtórzone UUID.")


def _line_parameter(line: LineEntity, point: Point2D) -> float:
    dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
    length_sq = dx * dx + dy * dy
    return ((point.x - line.start.x) * dx + (point.y - line.start.y) * dy) / length_sq


def _circular_parameter(entity: ArcEntity | CircleEntity, point: Point2D) -> float:
    angle = math.degrees(math.atan2(point.y - entity.center.y, point.x - entity.center.x))
    if isinstance(entity, CircleEntity):
        return (angle % 360.0) / 360.0
    if entity.sweep_angle_deg > 0:
        travelled = (angle - entity.start_angle_deg) % 360.0
    else:
        travelled = -((entity.start_angle_deg - angle) % 360.0)
    return travelled / entity.sweep_angle_deg


def _parameter(entity: EditableCurve, point: Point2D) -> float:
    return _line_parameter(entity, point) if isinstance(entity, LineEntity) else _circular_parameter(entity, point)


def _point_on_domain(entity: EditableCurve, parameter: float, extend: bool) -> bool:
    return extend or -EDIT_TOLERANCE <= parameter <= 1.0 + EDIT_TOLERANCE


def _line_line(first: LineEntity, second: LineEntity) -> tuple[tuple[Point2D, float, float], ...]:
    ax, ay = first.start.x, first.start.y
    bx, by = first.end.x - ax, first.end.y - ay
    cx, cy = second.start.x, second.start.y
    dx, dy = second.end.x - cx, second.end.y - cy
    denominator = bx * dy - by * dx
    if abs(denominator) <= 1e-12 * max(1.0, first.length, second.length):
        return ()
    ox, oy = cx - ax, cy - ay
    first_parameter = (ox * dy - oy * dx) / denominator
    second_parameter = (ox * by - oy * bx) / denominator
    return ((Point2D(ax + first_parameter * bx, ay + first_parameter * by), first_parameter, second_parameter),)


def _line_circle(line: LineEntity, circle: CircleEntity | ArcEntity) -> tuple[tuple[Point2D, float, float], ...]:
    dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
    fx, fy = line.start.x - circle.center.x, line.start.y - circle.center.y
    aa = dx * dx + dy * dy
    bb = 2.0 * (fx * dx + fy * dy)
    cc = fx * fx + fy * fy - circle.radius * circle.radius
    discriminant = bb * bb - 4 * aa * cc
    if discriminant < -1e-9:
        return ()
    roots = (-bb / (2 * aa),) if abs(discriminant) <= 1e-9 else (
        (-bb - math.sqrt(max(0.0, discriminant))) / (2 * aa),
        (-bb + math.sqrt(max(0.0, discriminant))) / (2 * aa),
    )
    result = []
    for parameter in roots:
        point = Point2D(line.start.x + parameter * dx, line.start.y + parameter * dy)
        result.append((point, parameter, _circular_parameter(circle, point)))
    return tuple(result)


def _circle_circle(first: CircleEntity | ArcEntity, second: CircleEntity | ArcEntity) -> tuple[tuple[Point2D, float, float], ...]:
    dx, dy = second.center.x - first.center.x, second.center.y - first.center.y
    distance = math.hypot(dx, dy)
    if distance <= 1e-12 or distance > first.radius + second.radius + EDIT_TOLERANCE:
        return ()
    if distance < abs(first.radius - second.radius) - EDIT_TOLERANCE:
        return ()
    along = (first.radius**2 - second.radius**2 + distance**2) / (2 * distance)
    height_sq = first.radius**2 - along**2
    if height_sq < -EDIT_TOLERANCE:
        return ()
    base = Point2D(first.center.x + along * dx / distance, first.center.y + along * dy / distance)
    points = (base,)
    if height_sq > EDIT_TOLERANCE:
        height = math.sqrt(height_sq)
        ox, oy = -dy * height / distance, dx * height / distance
        points = (Point2D(base.x + ox, base.y + oy), Point2D(base.x - ox, base.y - oy))
    return tuple((point, _circular_parameter(first, point), _circular_parameter(second, point)) for point in points)


def curve_intersections(
    first: EditableCurve,
    second: EditableCurve,
    *,
    extend_first: bool = False,
    extend_second: bool = False,
) -> tuple[CurveIntersection, ...]:
    if isinstance(first, LineEntity) and isinstance(second, LineEntity):
        raw = _line_line(first, second)
    elif isinstance(first, LineEntity) and isinstance(second, (CircleEntity, ArcEntity)):
        raw = _line_circle(first, second)
    elif isinstance(second, LineEntity) and isinstance(first, (CircleEntity, ArcEntity)):
        raw = tuple((point, second_parameter, first_parameter) for point, first_parameter, second_parameter in _line_circle(second, first))
    else:
        assert isinstance(first, (CircleEntity, ArcEntity)) and isinstance(second, (CircleEntity, ArcEntity))
        raw = _circle_circle(first, second)
    unique: dict[tuple[int, int], CurveIntersection] = {}
    for point, first_parameter, second_parameter in raw:
        if not _point_on_domain(first, first_parameter, extend_first):
            continue
        if not _point_on_domain(second, second_parameter, extend_second):
            continue
        key = (round(point.x / EDIT_TOLERANCE), round(point.y / EDIT_TOLERANCE))
        unique[key] = CurveIntersection(point, first_parameter, second_parameter)
    return tuple(sorted(unique.values(), key=lambda item: item.first_parameter))


def _line_piece(source: LineEntity, start: float, end: float, *, entity_id: str) -> LineEntity:
    dx, dy = source.end.x - source.start.x, source.end.y - source.start.y
    return replace(
        source,
        id=entity_id,
        start=Point2D(source.start.x + dx * start, source.start.y + dy * start),
        end=Point2D(source.start.x + dx * end, source.start.y + dy * end),
    )


def _arc_piece(source: ArcEntity, start: float, end: float, *, entity_id: str) -> ArcEntity:
    return replace(
        source,
        id=entity_id,
        start_angle_deg=source.start_angle_deg + source.sweep_angle_deg * start,
        sweep_angle_deg=source.sweep_angle_deg * (end - start),
    )


def _split_parameters(source: LineEntity | ArcEntity, points: tuple[Point2D, ...]) -> list[float]:
    parameters = sorted({
        min(1.0, max(0.0, _parameter(source, point)))
        for point in points
        if EDIT_TOLERANCE < _parameter(source, point) < 1.0 - EDIT_TOLERANCE
    })
    if not parameters:
        raise CadValidationError("Nie znaleziono przecięcia wewnątrz wybranej geometrii.")
    return [0.0, *parameters, 1.0]


def _circle_intervals(source: CircleEntity, points: tuple[Point2D, ...]) -> list[tuple[float, float]]:
    parameters = sorted({_circular_parameter(source, point) % 1.0 for point in points})
    if len(parameters) < 2:
        raise CadValidationError("Podział okręgu wymaga co najmniej dwóch różnych punktów przecięcia.")
    return [(start, end if end > start else end + 1.0) for start, end in zip(parameters, parameters[1:] + [parameters[0]])]


def _pieces(source: EditableCurve, points: tuple[Point2D, ...]) -> list[SketchEntity]:
    if isinstance(source, CircleEntity):
        result: list[SketchEntity] = []
        for index, (start, end) in enumerate(_circle_intervals(source, points)):
            result.append(ArcEntity(
                source.center,
                source.radius,
                start * 360.0,
                (end - start) * 360.0,
                id=source.id if index == 0 else new_id(),
                construction=source.construction,
                visible=source.visible,
                locked=source.locked,
                layer=source.layer,
            ))
        return result
    parameters = _split_parameters(source, points)
    result = []
    for index, (start, end) in enumerate(zip(parameters, parameters[1:])):
        entity_id = source.id if index == 0 else new_id()
        if isinstance(source, LineEntity):
            result.append(_line_piece(source, start, end, entity_id=entity_id))
        else:
            result.append(_arc_piece(source, start, end, entity_id=entity_id))
    return result


def split_curve(source: EditableCurve, cutter: EditableCurve, pick_point: Point2D) -> TopologyEdit:
    intersections = curve_intersections(source, cutter)
    pieces = _pieces(source, tuple(item.point for item in intersections))
    return TopologyEdit("split", source.id, tuple(pieces), pick_point)


def trim_curve(source: EditableCurve, cutter: EditableCurve, pick_point: Point2D) -> TopologyEdit:
    intersections = curve_intersections(source, cutter)
    pieces = _pieces(source, tuple(item.point for item in intersections))
    remove_index = min(
        range(len(pieces)),
        key=lambda index: _representative_point(pieces[index]).distance_to(pick_point),
    )
    remaining = [piece for index, piece in enumerate(pieces) if index != remove_index]
    if not remaining:
        raise CadValidationError("Przycięcie usunęłoby całą geometrię.")
    remaining[0] = replace(remaining[0], id=source.id)
    for index in range(1, len(remaining)):
        if remaining[index].id == source.id:
            remaining[index] = replace(remaining[index], id=new_id())
    return TopologyEdit("trim", source.id, tuple(remaining), pick_point)


def _representative_point(entity: SketchEntity) -> Point2D:
    if isinstance(entity, LineEntity):
        return Point2D((entity.start.x + entity.end.x) / 2, (entity.start.y + entity.end.y) / 2)
    if isinstance(entity, ArcEntity):
        return entity.midpoint
    if isinstance(entity, CircleEntity):
        return entity.center
    raise CadValidationError("Operacja wymaga linii, łuku lub okręgu.")


def nearest_point_on_curve(entity: EditableCurve, point: Point2D) -> Point2D:
    if isinstance(entity, LineEntity):
        parameter = min(1.0, max(0.0, _line_parameter(entity, point)))
        dx, dy = entity.end.x - entity.start.x, entity.end.y - entity.start.y
        return Point2D(entity.start.x + parameter * dx, entity.start.y + parameter * dy)
    if isinstance(entity, ArcEntity):
        return entity.nearest_point(point)
    dx, dy = point.x - entity.center.x, point.y - entity.center.y
    distance = math.hypot(dx, dy)
    if distance <= 1e-12:
        return Point2D(entity.center.x + entity.radius, entity.center.y)
    return Point2D(entity.center.x + dx * entity.radius / distance, entity.center.y + dy * entity.radius / distance)


def distance_to_curve(entity: EditableCurve, point: Point2D) -> float:
    return point.distance_to(nearest_point_on_curve(entity, point))


def extend_line(source: LineEntity, boundary: EditableCurve, pick_point: Point2D) -> TopologyEdit:
    intersections = curve_intersections(
        source,
        boundary,
        extend_first=True,
        extend_second=isinstance(boundary, LineEntity),
    )
    extend_start = pick_point.distance_to(source.start) <= pick_point.distance_to(source.end)
    candidates = [item for item in intersections if item.first_parameter < -EDIT_TOLERANCE] if extend_start else [
        item for item in intersections if item.first_parameter > 1.0 + EDIT_TOLERANCE
    ]
    if not candidates:
        side = "początku" if extend_start else "końca"
        raise CadValidationError(f"Nie znaleziono granicy przedłużenia po stronie {side} linii.")
    chosen = min(candidates, key=lambda item: abs(item.first_parameter - (0.0 if extend_start else 1.0)))
    replacement = replace(source, start=chosen.point) if extend_start else replace(source, end=chosen.point)
    return TopologyEdit("extend", source.id, (replacement,), pick_point)

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from typing import Iterable

from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.model import ArcEntity, BSplineEntity, CircleEntity, EllipseEntity, EllipticalArcEntity, LineEntity, Point2D, PointEntity, PolylineEntity, RectangleEntity, RegularPolygonEntity, Sketch, SketchEntity, SlotEntity


@dataclass(frozen=True, slots=True)
class ProfileTolerance:
    """Independent tolerances used by profile analysis, all in millimetres."""

    topology: float = 1e-6
    join: float = 0.10
    duplicate: float = 1e-6
    micro_segment: float = 0.01

    def __post_init__(self) -> None:
        values = (self.topology, self.join, self.duplicate, self.micro_segment)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Tolerancje profilu musza byc dodatnimi liczbami skonczonymi.")
        if self.join < self.topology:
            raise ValueError("Tolerancja laczenia nie moze byc mniejsza od tolerancji topologii.")


class ProfileIssueType(str, Enum):
    OPEN_ENDPOINT = "open_endpoint"
    SMALL_GAP = "small_gap"
    DUPLICATE = "duplicate"
    MICRO_SEGMENT = "micro_segment"
    SELF_INTERSECTION = "self_intersection"
    OVERLAP = "overlap"
    BRANCH_POINT = "branch_point"


class ProfileSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EndpointReference:
    entity_id: str
    element: str
    point: Point2D


@dataclass(frozen=True, slots=True)
class ProfileIssue:
    issue_type: ProfileIssueType
    severity: ProfileSeverity
    message: str
    entity_ids: tuple[str, ...] = ()
    points: tuple[Point2D, ...] = ()
    references: tuple[EndpointReference, ...] = ()
    repairable: bool = False


@dataclass(frozen=True, slots=True)
class ProfileLoop:
    entity_ids: tuple[str, ...]
    signed_area: float
    orientation: str
    nesting_depth: int
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class ProfileReport:
    issues: tuple[ProfileIssue, ...]
    loops: tuple[ProfileLoop, ...]
    profile_entity_ids: tuple[str, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == ProfileSeverity.ERROR for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == ProfileSeverity.WARNING for issue in self.issues)

    @property
    def open_endpoint_count(self) -> int:
        return sum(issue.issue_type == ProfileIssueType.OPEN_ENDPOINT for issue in self.issues)

    @property
    def is_valid_surface(self) -> bool:
        return bool(self.loops) and not self.error_count

    @property
    def affected_entity_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(entity_id for issue in self.issues for entity_id in issue.entity_ids))


@dataclass(frozen=True, slots=True)
class _Segment:
    entity_id: str
    index: int
    start: EndpointReference
    end: EndpointReference

    @property
    def length(self) -> float:
        return self.start.point.distance_to(self.end.point)


def _profile_entities(sketch: Sketch) -> list[SketchEntity]:
    return [entity for entity in sketch.ordered_entities() if not entity.construction and not isinstance(entity, PointEntity)]


def _segments(entities: Iterable[SketchEntity]) -> list[_Segment]:
    result: list[_Segment] = []
    for entity in entities:
        if isinstance(entity, LineEntity):
            result.append(_Segment(
                entity.id,
                0,
                EndpointReference(entity.id, "start", entity.start),
                EndpointReference(entity.id, "end", entity.end),
            ))
        elif isinstance(entity, RectangleEntity):
            corners = entity.corners
            for index in range(4):
                next_index = (index + 1) % 4
                result.append(_Segment(
                    entity.id,
                    index,
                    EndpointReference(entity.id, f"corner-{index}", corners[index]),
                    EndpointReference(entity.id, f"corner-{next_index}", corners[next_index]),
                ))
        elif isinstance(entity, ArcEntity):
            # Diagnostic tessellation only: the source entity remains analytic.
            # Five-degree chords are sufficient for topology/intersection review
            # and preserve exact analytic endpoints for repair references.
            count = max(2, int(math.ceil(abs(entity.sweep_angle_deg) / 5.0)))
            points = [entity.point_at(index / count) for index in range(count + 1)]
            for index, (start, end) in enumerate(zip(points, points[1:])):
                start_element = "start" if index == 0 else f"diagnostic-{index}"
                end_element = "end" if index == count - 1 else f"diagnostic-{index + 1}"
                result.append(_Segment(
                    entity.id,
                    index,
                    EndpointReference(entity.id, start_element, start),
                    EndpointReference(entity.id, end_element, end),
                ))
        elif isinstance(entity, EllipticalArcEntity):
            count = max(2, int(math.ceil(abs(entity.sweep_parameter_deg) / 5.0)))
            points = [entity.point_at(index / count) for index in range(count + 1)]
            for index, (start, end) in enumerate(zip(points, points[1:])):
                start_element = "start" if index == 0 else f"diagnostic-{index}"
                end_element = "end" if index == count - 1 else f"diagnostic-{index + 1}"
                result.append(_Segment(
                    entity.id,
                    index,
                    EndpointReference(entity.id, start_element, start),
                    EndpointReference(entity.id, end_element, end),
                ))
        elif isinstance(entity, BSplineEntity):
            # Topology diagnostics use chords, while the source remains an
            # exact rational B-spline evaluated by de Boor.
            points = entity.diagnostic_points(max(48, len(entity.control_points) * 24))
            pair_count = len(points) - 1
            for index, (start, end) in enumerate(zip(points, points[1:])):
                start_element = "start" if index == 0 else f"diagnostic-{index}"
                end_element = "end" if index == pair_count - 1 else f"diagnostic-{index + 1}"
                result.append(_Segment(
                    entity.id,
                    index,
                    EndpointReference(entity.id, start_element, start),
                    EndpointReference(entity.id, end_element, end),
                ))
        elif isinstance(entity, PolylineEntity):
            diagnostic_index = 0
            for source_index, segment in enumerate(entity.segment_entities):
                if isinstance(segment, ArcEntity):
                    step_count = max(2, int(math.ceil(abs(segment.sweep_angle_deg) / 5.0)))
                    points = [segment.point_at(index / step_count) for index in range(step_count + 1)]
                else:
                    points = [segment.start, segment.end]
                for local_index, (start, end) in enumerate(zip(points, points[1:])):
                    start_element = (
                        f"vertex-{source_index}"
                        if local_index == 0
                        else f"segment-{source_index}-diagnostic-{local_index}"
                    )
                    end_element = (
                        f"vertex-{(source_index + 1) % len(entity.points)}"
                        if local_index == len(points) - 2
                        else f"segment-{source_index}-diagnostic-{local_index + 1}"
                    )
                    result.append(_Segment(
                        entity.id,
                        diagnostic_index,
                        EndpointReference(entity.id, start_element, start),
                        EndpointReference(entity.id, end_element, end),
                    ))
                    diagnostic_index += 1
        elif isinstance(entity, RegularPolygonEntity):
            for index, (start, end) in enumerate(entity.segments):
                result.append(_Segment(
                    entity.id,
                    index,
                    EndpointReference(entity.id, f"vertex-{index}", start),
                    EndpointReference(entity.id, f"vertex-{(index + 1) % entity.sides}", end),
                ))
        elif isinstance(entity, SlotEntity):
            first_line, second_line = entity.boundary_lines
            end_arc, start_arc = entity.boundary_arcs
            cap_steps = 36
            points = [first_line.start, first_line.end]
            points.extend(end_arc.point_at(index / cap_steps) for index in range(1, cap_steps + 1))
            points.append(second_line.end)
            points.extend(start_arc.point_at(index / cap_steps) for index in range(1, cap_steps))
            for index, start in enumerate(points):
                next_index = (index + 1) % len(points)
                result.append(_Segment(
                    entity.id,
                    index,
                    EndpointReference(entity.id, f"boundary-{index}", start),
                    EndpointReference(entity.id, f"boundary-{next_index}", points[next_index]),
                ))
    return result


def _near(first: Point2D, second: Point2D, tolerance: float) -> bool:
    return first.distance_to(second) <= tolerance


def _same_entity_geometry(first: SketchEntity, second: SketchEntity, tolerance: float) -> bool:
    if type(first) is not type(second):
        return False
    if isinstance(first, LineEntity) and isinstance(second, LineEntity):
        return (
            _near(first.start, second.start, tolerance) and _near(first.end, second.end, tolerance)
        ) or (
            _near(first.start, second.end, tolerance) and _near(first.end, second.start, tolerance)
        )
    if isinstance(first, RectangleEntity) and isinstance(second, RectangleEntity):
        return (
            _near(first.origin, second.origin, tolerance)
            and abs(first.width - second.width) <= tolerance
            and abs(first.height - second.height) <= tolerance
        )
    if isinstance(first, CircleEntity) and isinstance(second, CircleEntity):
        return _near(first.center, second.center, tolerance) and abs(first.radius - second.radius) <= tolerance
    if isinstance(first, ArcEntity) and isinstance(second, ArcEntity):
        return (
            _near(first.center, second.center, tolerance)
            and abs(first.radius - second.radius) <= tolerance
            and abs(((first.start_angle_deg - second.start_angle_deg + 180) % 360) - 180) <= tolerance
            and abs(first.sweep_angle_deg - second.sweep_angle_deg) <= tolerance
        )
    if isinstance(first, EllipseEntity) and isinstance(second, EllipseEntity):
        return (
            _near(first.center, second.center, tolerance)
            and abs(first.major_radius - second.major_radius) <= tolerance
            and abs(first.minor_radius - second.minor_radius) <= tolerance
            and abs(((first.rotation_deg - second.rotation_deg + 180) % 360) - 180) <= tolerance
        )
    if isinstance(first, EllipticalArcEntity) and isinstance(second, EllipticalArcEntity):
        return (
            _near(first.center, second.center, tolerance)
            and abs(first.major_radius - second.major_radius) <= tolerance
            and abs(first.minor_radius - second.minor_radius) <= tolerance
            and abs(((first.rotation_deg - second.rotation_deg + 180) % 360) - 180) <= tolerance
            and abs(((first.start_parameter_deg - second.start_parameter_deg + 180) % 360) - 180) <= tolerance
            and abs(first.sweep_parameter_deg - second.sweep_parameter_deg) <= tolerance
        )
    if isinstance(first, PolylineEntity) and isinstance(second, PolylineEntity):
        return (
            first.closed == second.closed
            and len(first.points) == len(second.points)
            and all(_near(first_point, second_point, tolerance) for first_point, second_point in zip(first.points, second.points))
            and len(first.bulges) == len(second.bulges)
            and all(abs(first_bulge - second_bulge) <= tolerance for first_bulge, second_bulge in zip(first.bulges, second.bulges))
        )
    if isinstance(first, BSplineEntity) and isinstance(second, BSplineEntity):
        return (
            first.degree == second.degree
            and first.closed == second.closed
            and first.periodic == second.periodic
            and len(first.control_points) == len(second.control_points)
            and len(first.knots) == len(second.knots)
            and all(_near(a, b, tolerance) for a, b in zip(first.control_points, second.control_points))
            and all(abs(a - b) <= tolerance for a, b in zip(first.knots, second.knots))
            and all(abs(a - b) <= tolerance for a, b in zip(first.weights, second.weights))
        )
    if isinstance(first, SlotEntity) and isinstance(second, SlotEntity):
        same_axis = (
            _near(first.axis_start, second.axis_start, tolerance)
            and _near(first.axis_end, second.axis_end, tolerance)
        ) or (
            _near(first.axis_start, second.axis_end, tolerance)
            and _near(first.axis_end, second.axis_start, tolerance)
        )
        return same_axis and abs(first.radius - second.radius) <= tolerance
    if isinstance(first, RegularPolygonEntity) and isinstance(second, RegularPolygonEntity):
        return (
            first.sides == second.sides
            and _near(first.center, second.center, tolerance)
            and abs(first.radius - second.radius) <= tolerance
            and abs(((first.rotation_deg - second.rotation_deg + 180) % 360) - 180) <= tolerance
        )
    return False


def _cross(a: Point2D, b: Point2D, c: Point2D) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _point_on_segment(point: Point2D, segment: _Segment, tolerance: float) -> bool:
    a, b = segment.start.point, segment.end.point
    if abs(_cross(a, b, point)) > tolerance * max(1.0, segment.length):
        return False
    return (
        min(a.x, b.x) - tolerance <= point.x <= max(a.x, b.x) + tolerance
        and min(a.y, b.y) - tolerance <= point.y <= max(a.y, b.y) + tolerance
    )


def _segment_intersection(first: _Segment, second: _Segment, tolerance: float) -> tuple[str, Point2D | None]:
    a, b = first.start.point, first.end.point
    c, d = second.start.point, second.end.point
    r_x, r_y = b.x - a.x, b.y - a.y
    s_x, s_y = d.x - c.x, d.y - c.y
    denominator = r_x * s_y - r_y * s_x
    q_x, q_y = c.x - a.x, c.y - a.y
    if abs(denominator) <= tolerance * max(1.0, first.length, second.length):
        if abs(q_x * r_y - q_y * r_x) > tolerance * max(1.0, first.length):
            return "none", None
        shared = [point for point in (a, b, c, d) if _point_on_segment(point, first, tolerance) and _point_on_segment(point, second, tolerance)]
        unique: list[Point2D] = []
        for point in shared:
            if not any(_near(point, other, tolerance) for other in unique):
                unique.append(point)
        return ("overlap", unique[0] if unique else None) if len(unique) >= 2 else ("touch", unique[0] if unique else None)
    t = (q_x * s_y - q_y * s_x) / denominator
    u = (q_x * r_y - q_y * r_x) / denominator
    if -tolerance <= t <= 1 + tolerance and -tolerance <= u <= 1 + tolerance:
        point = Point2D(a.x + t * r_x, a.y + t * r_y)
        endpoint_touch = any(_near(point, endpoint, tolerance) for endpoint in (a, b)) and any(
            _near(point, endpoint, tolerance) for endpoint in (c, d)
        )
        return ("touch" if endpoint_touch else "cross"), point
    return "none", None


def _line_circle_intersections(segment: _Segment, circle: CircleEntity, tolerance: float) -> list[Point2D]:
    a, b = segment.start.point, segment.end.point
    dx, dy = b.x - a.x, b.y - a.y
    fx, fy = a.x - circle.center.x, a.y - circle.center.y
    aa = dx * dx + dy * dy
    bb = 2 * (fx * dx + fy * dy)
    cc = fx * fx + fy * fy - circle.radius * circle.radius
    discriminant = bb * bb - 4 * aa * cc
    if discriminant < -tolerance:
        return []
    roots = [-bb / (2 * aa)] if abs(discriminant) <= tolerance else [
        (-bb - math.sqrt(max(0.0, discriminant))) / (2 * aa),
        (-bb + math.sqrt(max(0.0, discriminant))) / (2 * aa),
    ]
    return [Point2D(a.x + root * dx, a.y + root * dy) for root in roots if -tolerance <= root <= 1 + tolerance]


def _line_ellipse_intersections(segment: _Segment, ellipse: EllipseEntity, tolerance: float) -> list[Point2D]:
    rotation = math.radians(ellipse.rotation_deg)

    def local(point: Point2D) -> tuple[float, float]:
        dx, dy = point.x - ellipse.center.x, point.y - ellipse.center.y
        return dx * math.cos(rotation) + dy * math.sin(rotation), -dx * math.sin(rotation) + dy * math.cos(rotation)

    start_x, start_y = local(segment.start.point)
    end_x, end_y = local(segment.end.point)
    dx, dy = end_x - start_x, end_y - start_y
    aa = (dx / ellipse.major_radius) ** 2 + (dy / ellipse.minor_radius) ** 2
    bb = 2.0 * (start_x * dx / ellipse.major_radius**2 + start_y * dy / ellipse.minor_radius**2)
    cc = (start_x / ellipse.major_radius) ** 2 + (start_y / ellipse.minor_radius) ** 2 - 1.0
    discriminant = bb * bb - 4.0 * aa * cc
    if aa <= tolerance or discriminant < -tolerance:
        return []
    roots = [-bb / (2.0 * aa)] if abs(discriminant) <= tolerance else [
        (-bb - math.sqrt(max(0.0, discriminant))) / (2.0 * aa),
        (-bb + math.sqrt(max(0.0, discriminant))) / (2.0 * aa),
    ]
    start, end = segment.start.point, segment.end.point
    return [
        Point2D(start.x + root * (end.x - start.x), start.y + root * (end.y - start.y))
        for root in roots if -tolerance <= root <= 1.0 + tolerance
    ]


def _circle_intersections(first: CircleEntity, second: CircleEntity, tolerance: float) -> list[Point2D]:
    dx, dy = second.center.x - first.center.x, second.center.y - first.center.y
    distance = math.hypot(dx, dy)
    if distance <= tolerance or distance > first.radius + second.radius + tolerance:
        return []
    if distance < abs(first.radius - second.radius) - tolerance:
        return []
    along = (first.radius**2 - second.radius**2 + distance**2) / (2 * distance)
    height_sq = first.radius**2 - along**2
    if height_sq < -tolerance:
        return []
    base_x = first.center.x + along * dx / distance
    base_y = first.center.y + along * dy / distance
    if abs(height_sq) <= tolerance:
        return [Point2D(base_x, base_y)]
    height = math.sqrt(max(0.0, height_sq))
    offset_x, offset_y = -dy * height / distance, dx * height / distance
    return [Point2D(base_x + offset_x, base_y + offset_y), Point2D(base_x - offset_x, base_y - offset_y)]


def _endpoint_groups(segments: list[_Segment], tolerance: float) -> list[list[EndpointReference]]:
    groups: list[list[EndpointReference]] = []
    for reference in (item for segment in segments for item in (segment.start, segment.end)):
        matches = [group for group in groups if any(_near(reference.point, member.point, tolerance) for member in group)]
        if not matches:
            groups.append([reference])
            continue
        target = matches[0]
        target.append(reference)
        for extra in matches[1:]:
            target.extend(extra)
            groups.remove(extra)
    return groups


def _signed_area(points: list[Point2D]) -> float:
    return sum(
        first.x * second.y - second.x * first.y
        for first, second in zip(points, points[1:] + points[:1])
    ) / 2


def _line_loops(segments: list[_Segment], tolerance: float) -> list[tuple[tuple[str, ...], list[Point2D]]]:
    groups = _endpoint_groups(segments, tolerance)
    endpoint_group: dict[tuple[str, str], int] = {}
    for group_index, group in enumerate(groups):
        for reference in group:
            endpoint_group[(reference.entity_id, reference.element)] = group_index
    adjacency: dict[int, list[tuple[int, _Segment]]] = {index: [] for index in range(len(groups))}
    for segment in segments:
        start = endpoint_group[(segment.start.entity_id, segment.start.element)]
        end = endpoint_group[(segment.end.entity_id, segment.end.element)]
        adjacency[start].append((end, segment))
        adjacency[end].append((start, segment))
    loops: list[tuple[tuple[str, ...], list[Point2D]]] = []
    visited_segments: set[tuple[str, int]] = set()
    for segment in segments:
        key = (segment.entity_id, segment.index)
        if key in visited_segments:
            continue
        start_node = endpoint_group[(segment.start.entity_id, segment.start.element)]
        component_nodes = {start_node}
        pending = [start_node]
        component_segments: dict[tuple[str, int], _Segment] = {}
        while pending:
            node = pending.pop()
            for neighbour, edge in adjacency[node]:
                component_segments[(edge.entity_id, edge.index)] = edge
                if neighbour not in component_nodes:
                    component_nodes.add(neighbour)
                    pending.append(neighbour)
        visited_segments.update(component_segments)
        if not component_segments or any(len(adjacency[node]) != 2 for node in component_nodes):
            continue
        current, previous = start_node, -1
        points: list[Point2D] = []
        entity_ids: list[str] = []
        for _ in range(len(component_segments) + 1):
            points.append(groups[current][0].point)
            choices = [(node, edge) for node, edge in adjacency[current] if node != previous]
            if not choices:
                break
            next_node, edge = choices[0]
            entity_ids.append(edge.entity_id)
            previous, current = current, next_node
            if current == start_node:
                break
        if current == start_node and len(entity_ids) == len(component_segments):
            loops.append((tuple(dict.fromkeys(entity_ids)), points))
    return loops


def _bounds(points: list[Point2D]) -> tuple[float, float, float, float]:
    return min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)


def _point_in_polygon(point: Point2D, polygon: list[Point2D]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (current.y > point.y) != (previous.y > point.y):
            crossing_x = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
            if point.x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _loop_records(
    segments: list[_Segment],
    circles: list[CircleEntity],
    ellipses: list[EllipseEntity],
    tolerance: float,
) -> tuple[ProfileLoop, ...]:
    raw: list[tuple[tuple[str, ...], list[Point2D], float]] = []
    for entity_ids, points in _line_loops(segments, tolerance):
        raw.append((entity_ids, points, _signed_area(points)))
    for circle in circles:
        points = [
            Point2D(circle.center.x + circle.radius * math.cos(index * math.tau / 64), circle.center.y + circle.radius * math.sin(index * math.tau / 64))
            for index in range(64)
        ]
        raw.append(((circle.id,), points, math.pi * circle.radius**2))
    for ellipse in ellipses:
        points = [ellipse.point_at_parameter(index * 360.0 / 96.0) for index in range(96)]
        raw.append(((ellipse.id,), points, ellipse.area))
    result: list[ProfileLoop] = []
    for entity_ids, points, area in raw:
        probe = points[0]
        depth = sum(
            _point_in_polygon(probe, other_points)
            for other_ids, other_points, _other_area in raw
            if other_ids != entity_ids
        )
        expected_ccw = depth % 2 == 0
        orientation = "CCW" if area >= 0 else "CW"
        if expected_ccw != (area >= 0):
            orientation += " (odwrotna dla zagniezdzenia)"
        result.append(ProfileLoop(entity_ids, area, orientation, depth, _bounds(points)))
    return tuple(result)


def analyze_profile(sketch: Sketch, tolerance: ProfileTolerance | None = None) -> ProfileReport:
    tolerance = tolerance or ProfileTolerance()
    entities = _profile_entities(sketch)
    segments = _segments(entities)
    circles = [entity for entity in entities if isinstance(entity, CircleEntity)]
    ellipses = [entity for entity in entities if isinstance(entity, EllipseEntity)]
    issues: list[ProfileIssue] = []

    for first, second in combinations(entities, 2):
        if _same_entity_geometry(first, second, tolerance.duplicate):
            issues.append(ProfileIssue(
                ProfileIssueType.DUPLICATE,
                ProfileSeverity.ERROR,
                "Znaleziono nakladajace sie duplikaty geometrii.",
                (first.id, second.id),
                repairable=True,
            ))

    for segment in segments:
        if segment.length < tolerance.micro_segment:
            issues.append(ProfileIssue(
                ProfileIssueType.MICRO_SEGMENT,
                ProfileSeverity.ERROR,
                f"Odcinek ma tylko {segment.length:.6g} mm.",
                (segment.entity_id,),
                (segment.start.point, segment.end.point),
                repairable=True,
            ))

    groups = _endpoint_groups(segments, tolerance.topology)
    open_groups = [group for group in groups if len(group) == 1]
    paired_open: set[int] = set()
    gap_candidates: list[tuple[float, int, int]] = []
    for first_index, second_index in combinations(range(len(open_groups)), 2):
        first, second = open_groups[first_index][0], open_groups[second_index][0]
        distance = first.point.distance_to(second.point)
        if tolerance.topology < distance <= tolerance.join:
            gap_candidates.append((distance, first_index, second_index))
    for distance, first_index, second_index in sorted(gap_candidates):
        if first_index in paired_open or second_index in paired_open:
            continue
        first, second = open_groups[first_index][0], open_groups[second_index][0]
        issues.append(ProfileIssue(
            ProfileIssueType.SMALL_GAP,
            ProfileSeverity.ERROR,
            f"Szczelina {distance:.6g} mm miesci sie w tolerancji naprawy.",
            tuple(dict.fromkeys((first.entity_id, second.entity_id))),
            (first.point, second.point),
            (first, second),
            repairable=True,
        ))
        paired_open.update((first_index, second_index))
    for index, group in enumerate(open_groups):
        reference = group[0]
        issues.append(ProfileIssue(
            ProfileIssueType.OPEN_ENDPOINT,
            ProfileSeverity.ERROR,
            "Otwarty koniec konturu." + (" Mozna go polaczyc w tolerancji." if index in paired_open else ""),
            (reference.entity_id,),
            (reference.point,),
            (reference,),
            repairable=index in paired_open,
        ))
    for group in groups:
        if len(group) > 2:
            issues.append(ProfileIssue(
                ProfileIssueType.BRANCH_POINT,
                ProfileSeverity.ERROR,
                f"W jednym punkcie laczy sie {len(group)} koncow; profil nie jest rozmaitoscia 2D.",
                tuple(dict.fromkeys(reference.entity_id for reference in group)),
                (group[0].point,),
                tuple(group),
            ))

    for first, second in combinations(segments, 2):
        if first.entity_id == second.entity_id:
            source = sketch.entities[first.entity_id]
            if isinstance(source, RectangleEntity):
                continue
            if isinstance(source, PolylineEntity):
                segment_count = len([segment for segment in segments if segment.entity_id == source.id])
                adjacent = abs(first.index - second.index) == 1
                wraps = source.closed and {first.index, second.index} == {0, segment_count - 1}
                if adjacent or wraps:
                    continue
            if isinstance(source, BSplineEntity):
                segment_count = len([segment for segment in segments if segment.entity_id == source.id])
                adjacent = abs(first.index - second.index) == 1
                wraps = source.closed and {first.index, second.index} == {0, segment_count - 1}
                if adjacent or wraps:
                    continue
            if isinstance(source, SlotEntity):
                segment_count = len([segment for segment in segments if segment.entity_id == source.id])
                adjacent = abs(first.index - second.index) == 1
                wraps = {first.index, second.index} == {0, segment_count - 1}
                if adjacent or wraps:
                    continue
            if isinstance(source, RegularPolygonEntity):
                adjacent = abs(first.index - second.index) == 1
                wraps = {first.index, second.index} == {0, source.sides - 1}
                if adjacent or wraps:
                    continue
        kind, point = _segment_intersection(first, second, tolerance.topology)
        if kind in {"cross", "overlap"}:
            issue_type = ProfileIssueType.OVERLAP if kind == "overlap" else ProfileIssueType.SELF_INTERSECTION
            issues.append(ProfileIssue(
                issue_type,
                ProfileSeverity.ERROR,
                "Odcinki nakladaja sie." if kind == "overlap" else "Kontur przecina sam siebie.",
                tuple(dict.fromkeys((first.entity_id, second.entity_id))),
                (point,) if point else (),
            ))
    for segment in segments:
        for circle in circles:
            if _line_circle_intersections(segment, circle, tolerance.topology):
                points = tuple(_line_circle_intersections(segment, circle, tolerance.topology))
                issues.append(ProfileIssue(
                    ProfileIssueType.SELF_INTERSECTION,
                    ProfileSeverity.ERROR,
                    "Odcinek przecina okrag profilu.",
                    tuple(dict.fromkeys((segment.entity_id, circle.id))),
                    points,
                ))
        for ellipse in ellipses:
            points = tuple(_line_ellipse_intersections(segment, ellipse, tolerance.topology))
            if points:
                issues.append(ProfileIssue(
                    ProfileIssueType.SELF_INTERSECTION,
                    ProfileSeverity.ERROR,
                    "Odcinek przecina elipsę profilu.",
                    tuple(dict.fromkeys((segment.entity_id, ellipse.id))),
                    points,
                ))
    for first, second in combinations(circles, 2):
        points = tuple(_circle_intersections(first, second, tolerance.topology))
        if points:
            issues.append(ProfileIssue(
                ProfileIssueType.SELF_INTERSECTION,
                ProfileSeverity.ERROR,
                "Okregi profilu przecinaja sie.",
                (first.id, second.id),
                points,
            ))

    unique: list[ProfileIssue] = []
    signatures: set[tuple[object, ...]] = set()
    for issue in issues:
        signature = (issue.issue_type, tuple(sorted(issue.entity_ids)), tuple((round(p.x, 9), round(p.y, 9)) for p in issue.points))
        if signature not in signatures:
            signatures.add(signature)
            unique.append(issue)
    loops = _loop_records(segments, circles, ellipses, tolerance.topology)
    return ProfileReport(tuple(unique), loops, tuple(entity.id for entity in entities))


@dataclass(frozen=True, slots=True)
class ProfileRepairPlan:
    duplicate_entity_ids: tuple[str, ...]
    micro_entity_ids: tuple[str, ...]
    coincident_constraints: tuple[SketchConstraint, ...]

    @property
    def has_repairs(self) -> bool:
        return bool(self.duplicate_entity_ids or self.micro_entity_ids or self.coincident_constraints)


def build_repair_plan(
    sketch: Sketch,
    report: ProfileReport,
    *,
    remove_duplicates: bool = True,
    remove_micro_segments: bool = False,
    close_small_gaps: bool = True,
) -> ProfileRepairPlan:
    duplicate_ids: list[str] = []
    micro_ids: list[str] = []
    constraints: list[SketchConstraint] = []
    existing_pairs = {
        frozenset((reference.entity_id, reference.element) for reference in constraint.references)
        for constraint in sketch.constraints.values()
        if constraint.constraint_type == ConstraintType.COINCIDENT and len(constraint.references) == 2
    }
    pattern_owned_ids = {
        entity_id
        for pattern in sketch.patterns.values()
        for entity_id in (pattern.source_entity_id, *pattern.generated_entity_ids)
    }
    for issue in report.issues:
        if remove_duplicates and issue.issue_type == ProfileIssueType.DUPLICATE and len(issue.entity_ids) >= 2:
            removable = [entity_id for entity_id in issue.entity_ids[1:] if entity_id not in pattern_owned_ids]
            if not removable and issue.entity_ids[0] not in pattern_owned_ids:
                removable = [issue.entity_ids[0]]
            duplicate_ids.extend(removable[:1])
        elif remove_micro_segments and issue.issue_type == ProfileIssueType.MICRO_SEGMENT:
            micro_ids.extend(issue.entity_ids)
        elif close_small_gaps and issue.issue_type == ProfileIssueType.SMALL_GAP and len(issue.references) == 2:
            pair = frozenset((reference.entity_id, reference.element) for reference in issue.references)
            if pair in existing_pairs:
                continue
            constraints.append(SketchConstraint(
                ConstraintType.COINCIDENT,
                tuple(GeometryReference(reference.entity_id, reference.element) for reference in issue.references),
                name="Naprawa malej szczeliny",
            ))
            existing_pairs.add(pair)
    remove = tuple(dict.fromkeys((*duplicate_ids, *micro_ids)))
    return ProfileRepairPlan(
        tuple(entity_id for entity_id in dict.fromkeys(duplicate_ids) if entity_id in sketch.entities),
        tuple(entity_id for entity_id in dict.fromkeys(micro_ids) if entity_id in sketch.entities and entity_id not in duplicate_ids),
        tuple(constraints),
    )

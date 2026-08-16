from __future__ import annotations

from dataclasses import dataclass

from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.model import ArcEntity, BSplineEntity, CircleEntity, EllipseEntity, EllipticalArcEntity, LineEntity, Point2D, PointEntity, PolylineEntity, RectangleEntity, RegularPolygonEntity, Sketch, SketchEntity, SlotEntity
from cad.snapping import SnapCandidate, SnapKind


@dataclass(frozen=True, slots=True)
class AutoConstraintSet:
    constraints: tuple[SketchConstraint, ...]
    labels: tuple[str, ...]


_SUGGESTION_LABELS = {
    SnapKind.ENDPOINT: "zbieżność",
    SnapKind.MIDPOINT: "zbieżność ze środkiem",
    SnapKind.CENTER: "zbieżność z centrum",
    SnapKind.ORIGIN: "zbieżność z początkiem",
    SnapKind.INTERSECTION: "punkt na przecięciu",
    SnapKind.NEAREST: "punkt na obiekcie",
    SnapKind.EXTENSION: "punkt na przedłużeniu",
    SnapKind.AXIS_X: "punkt na osi X",
    SnapKind.AXIS_Y: "punkt na osi Y",
    SnapKind.HORIZONTAL: "poziomość",
    SnapKind.VERTICAL: "pionowość",
    SnapKind.PARALLEL: "równoległość",
    SnapKind.PERPENDICULAR: "prostopadłość",
    SnapKind.TANGENT: "styczność",
    SnapKind.INCREMENTAL_ANGLE: "kąt",
}


def suggestion_label(candidate: SnapCandidate | None) -> str:
    return _SUGGESTION_LABELS.get(candidate.kind, "") if candidate else ""


def _point_reference(entity: SketchEntity, point: Point2D, role: str) -> GeometryReference | None:
    if isinstance(entity, PointEntity):
        return GeometryReference(entity.id, "point") if entity.point.distance_to(point) <= 1e-5 else None
    if isinstance(entity, LineEntity):
        actual = entity.start if role == "start" else entity.end
        return GeometryReference(entity.id, role) if actual.distance_to(point) <= 1e-5 else None
    if isinstance(entity, CircleEntity) and role == "start":
        return GeometryReference(entity.id, "center") if entity.center.distance_to(point) <= 1e-5 else None
    if isinstance(entity, ArcEntity):
        actual = entity.center if role == "start" else entity.end
        element = "center" if role == "start" else "end"
        return GeometryReference(entity.id, element) if actual.distance_to(point) <= 1e-5 else None
    if isinstance(entity, EllipseEntity):
        if role == "start" and entity.center.distance_to(point) <= 1e-5:
            return GeometryReference(entity.id, "center")
        axis_points = (
            ("major-positive", entity.major_positive),
            ("major-negative", entity.major_negative),
            ("minor-positive", entity.minor_positive),
            ("minor-negative", entity.minor_negative),
        )
        element, actual = min(axis_points, key=lambda item: item[1].distance_to(point))
        return GeometryReference(entity.id, element) if actual.distance_to(point) <= 1e-5 else None
    if isinstance(entity, EllipticalArcEntity):
        actual = entity.center if role == "start" else entity.end
        element = "center" if role == "start" else "end"
        return GeometryReference(entity.id, element) if actual.distance_to(point) <= 1e-5 else None
    if isinstance(entity, RectangleEntity):
        if role == "start" and entity.center.distance_to(point) <= 1e-5:
            return GeometryReference(entity.id, "center")
        corners = entity.corners
        closest = min(range(4), key=lambda index: corners[index].distance_to(point))
        if corners[closest].distance_to(point) <= 1e-5:
            return GeometryReference(entity.id, f"corner-{closest}")
    if isinstance(entity, PolylineEntity):
        index = 0 if role == "start" else len(entity.points) - 1
        if entity.points[index].distance_to(point) <= 1e-5:
            return GeometryReference(entity.id, f"vertex-{index}")
    if isinstance(entity, BSplineEntity):
        actual = entity.start if role == "start" else entity.end
        return GeometryReference(entity.id, role) if actual.distance_to(point) <= 1e-5 else None
    if isinstance(entity, RegularPolygonEntity):
        if role == "start" and entity.center.distance_to(point) <= 1e-5:
            return GeometryReference(entity.id, "center")
        closest = min(range(entity.sides), key=lambda index: entity.points[index].distance_to(point))
        if entity.points[closest].distance_to(point) <= 1e-5:
            return GeometryReference(entity.id, f"vertex-{closest}")
    if isinstance(entity, SlotEntity):
        actual = entity.axis_start if role == "start" else entity.axis_end
        element = "axis-start" if role == "start" else "axis-end"
        return GeometryReference(entity.id, element) if actual.distance_to(point) <= 1e-5 else None
    return None


def _point_constraints(reference: GeometryReference, candidate: SnapCandidate) -> list[SketchConstraint]:
    result: list[SketchConstraint] = []
    if candidate.kind in {SnapKind.ENDPOINT, SnapKind.MIDPOINT, SnapKind.CENTER} and candidate.entity_id:
        result.append(SketchConstraint(
            ConstraintType.COINCIDENT,
            (reference, GeometryReference(candidate.entity_id, candidate.sub_element)),
            name="Auto — zbieżność",
        ))
    elif candidate.kind == SnapKind.ORIGIN:
        result.extend((
            SketchConstraint(ConstraintType.X_COORDINATE, (reference,), value=0.0, name="Auto — początek X"),
            SketchConstraint(ConstraintType.Y_COORDINATE, (reference,), value=0.0, name="Auto — początek Y"),
        ))
    elif candidate.kind == SnapKind.AXIS_X:
        result.append(SketchConstraint(ConstraintType.Y_COORDINATE, (reference,), value=0.0, name="Auto — oś X"))
    elif candidate.kind == SnapKind.AXIS_Y:
        result.append(SketchConstraint(ConstraintType.X_COORDINATE, (reference,), value=0.0, name="Auto — oś Y"))
    elif candidate.kind in {SnapKind.NEAREST, SnapKind.EXTENSION, SnapKind.TANGENT} and candidate.entity_id:
        result.append(SketchConstraint(
            ConstraintType.POINT_ON_OBJECT,
            (reference, GeometryReference(candidate.entity_id)),
            name="Auto — punkt na obiekcie",
        ))
    elif candidate.kind == SnapKind.INTERSECTION:
        for entity_id, _element in candidate.related:
            result.append(SketchConstraint(
                ConstraintType.POINT_ON_OBJECT,
                (reference, GeometryReference(entity_id)),
                name="Auto — punkt na przecięciu",
            ))
    return result


def build_auto_constraints(
    sketch: Sketch,
    entity: SketchEntity,
    *,
    start_point: Point2D,
    end_point: Point2D,
    start_candidate: SnapCandidate | None,
    end_candidate: SnapCandidate | None,
) -> AutoConstraintSet:
    constraints: list[SketchConstraint] = []
    labels: list[str] = []
    for role, point, candidate in (
        ("start", start_point, start_candidate),
        ("end", end_point, end_candidate),
    ):
        reference = _point_reference(entity, point, role)
        if reference is None or candidate is None:
            continue
        if candidate.entity_id == entity.id:
            continue
        point_constraints = _point_constraints(reference, candidate)
        if point_constraints:
            constraints.extend(point_constraints)
            label = suggestion_label(candidate)
            if label:
                labels.append(label)
    if isinstance(entity, (LineEntity, SlotEntity)) and end_candidate is not None:
        target_id = end_candidate.entity_id
        direction_reference = GeometryReference(entity.id, "axis" if isinstance(entity, SlotEntity) else "entity")
        actual_start = entity.axis_start if isinstance(entity, SlotEntity) else entity.start
        actual_end = entity.axis_end if isinstance(entity, SlotEntity) else entity.end
        actual_length = entity.axis_length if isinstance(entity, SlotEntity) else entity.length
        actual_angle = entity.angle_deg
        if end_candidate.kind == SnapKind.HORIZONTAL and abs(actual_end.y - actual_start.y) <= 1e-5:
            constraints.append(SketchConstraint(ConstraintType.HORIZONTAL, (direction_reference,), name="Auto — poziomość"))
        elif end_candidate.kind == SnapKind.VERTICAL and abs(actual_end.x - actual_start.x) <= 1e-5:
            constraints.append(SketchConstraint(ConstraintType.VERTICAL, (direction_reference,), name="Auto — pionowość"))
        elif end_candidate.kind == SnapKind.PARALLEL and target_id:
            target = sketch.entities.get(target_id)
            if isinstance(target, LineEntity):
                cross = (actual_end.x - actual_start.x) * (target.end.y - target.start.y) - (actual_end.y - actual_start.y) * (target.end.x - target.start.x)
                if abs(cross) <= 1e-5 * actual_length * target.length:
                    constraints.append(SketchConstraint(
                        ConstraintType.PARALLEL,
                        (direction_reference, GeometryReference(target_id)),
                        name="Auto — równoległość",
                    ))
        elif end_candidate.kind == SnapKind.PERPENDICULAR and target_id:
            target = sketch.entities.get(target_id)
            if isinstance(target, LineEntity):
                dot = (actual_end.x - actual_start.x) * (target.end.x - target.start.x) + (actual_end.y - actual_start.y) * (target.end.y - target.start.y)
                if abs(dot) <= 1e-5 * actual_length * target.length:
                    constraints.append(SketchConstraint(
                        ConstraintType.PERPENDICULAR,
                        (direction_reference, GeometryReference(target_id)),
                        name="Auto — prostopadłość",
                    ))
        elif isinstance(entity, LineEntity) and end_candidate.kind == SnapKind.TANGENT and target_id:
            target = sketch.entities.get(target_id)
            if isinstance(target, (CircleEntity, ArcEntity)):
                cross = abs((entity.end.x - entity.start.x) * (target.center.y - entity.start.y) - (entity.end.y - entity.start.y) * (target.center.x - entity.start.x))
                if abs(cross / entity.length - target.radius) <= 1e-5:
                    constraints.append(SketchConstraint(
                        ConstraintType.TANGENT,
                        (GeometryReference(entity.id), GeometryReference(target_id)),
                        name="Auto — styczność",
                    ))
        elif end_candidate.kind == SnapKind.INCREMENTAL_ANGLE:
            angle = float(end_candidate.sub_element)
            delta = (actual_angle - angle + 180) % 360 - 180
            if abs(delta) <= 1e-5:
                constraints.append(SketchConstraint(
                    ConstraintType.ANGLE,
                    (direction_reference,),
                    value=angle,
                    name="Auto — kąt",
                ))
        direction_label = suggestion_label(end_candidate)
        if direction_label and end_candidate.kind in {
            SnapKind.HORIZONTAL,
            SnapKind.VERTICAL,
            SnapKind.PARALLEL,
            SnapKind.PERPENDICULAR,
            SnapKind.TANGENT,
            SnapKind.INCREMENTAL_ANGLE,
        }:
            labels.append(direction_label)
    unique: list[SketchConstraint] = []
    signatures: set[tuple[object, ...]] = set()
    for constraint in constraints:
        signature = (
            constraint.constraint_type,
            tuple((reference.entity_id, reference.element) for reference in constraint.references),
            constraint.value,
        )
        if signature not in signatures:
            signatures.add(signature)
            unique.append(constraint)
    return AutoConstraintSet(tuple(unique), tuple(dict.fromkeys(labels)))


def build_polyline_auto_constraints(
    sketch: Sketch,
    entity: PolylineEntity,
    candidates: tuple[SnapCandidate | None, ...],
) -> AutoConstraintSet:
    if len(candidates) != len(entity.points):
        raise ValueError("Liczba kandydatów snapu musi odpowiadać liczbie wierzchołków polilinii.")
    constraints: list[SketchConstraint] = []
    labels: list[str] = []
    for index, (point, candidate) in enumerate(zip(entity.points, candidates)):
        if candidate is None or point.distance_to(candidate.point) > 1e-5 or candidate.entity_id == entity.id:
            continue
        reference = GeometryReference(entity.id, f"vertex-{index}")
        constraints.extend(_point_constraints(reference, candidate))
        label = suggestion_label(candidate)
        if label:
            labels.append(label)
        if index == 0:
            continue
        segment_reference = GeometryReference(entity.id, f"segment-{index - 1}")
        straight_segment = abs(entity.bulges[index - 1]) <= 1e-12
        if straight_segment and candidate.kind == SnapKind.HORIZONTAL and abs(entity.points[index].y - entity.points[index - 1].y) <= 1e-5:
            constraints.append(SketchConstraint(ConstraintType.HORIZONTAL, (segment_reference,), name="Auto — poziomość segmentu"))
        elif straight_segment and candidate.kind == SnapKind.VERTICAL and abs(entity.points[index].x - entity.points[index - 1].x) <= 1e-5:
            constraints.append(SketchConstraint(ConstraintType.VERTICAL, (segment_reference,), name="Auto — pionowość segmentu"))
        elif straight_segment and candidate.kind in {SnapKind.PARALLEL, SnapKind.PERPENDICULAR} and candidate.entity_id:
            target = sketch.entities.get(candidate.entity_id)
            if isinstance(target, LineEntity):
                constraints.append(SketchConstraint(
                    ConstraintType.PARALLEL if candidate.kind == SnapKind.PARALLEL else ConstraintType.PERPENDICULAR,
                    (segment_reference, GeometryReference(target.id)),
                    name="Auto — kierunek segmentu",
                ))
        elif straight_segment and candidate.kind == SnapKind.INCREMENTAL_ANGLE:
            constraints.append(SketchConstraint(
                ConstraintType.ANGLE,
                (segment_reference,),
                value=float(candidate.sub_element),
                name="Auto — kąt segmentu",
            ))
    unique: list[SketchConstraint] = []
    signatures: set[tuple[object, ...]] = set()
    for constraint in constraints:
        signature = (
            constraint.constraint_type,
            tuple((reference.entity_id, reference.element) for reference in constraint.references),
            constraint.value,
        )
        if signature not in signatures:
            signatures.add(signature)
            unique.append(constraint)
    return AutoConstraintSet(tuple(unique), tuple(dict.fromkeys(labels)))


def build_bspline_auto_constraints(
    entity: BSplineEntity,
    candidates: tuple[SnapCandidate | None, ...],
) -> AutoConstraintSet:
    if len(candidates) != len(entity.control_points):
        raise ValueError("Liczba kandydatów snapu musi odpowiadać liczbie punktów kontrolnych B-spline.")
    constraints: list[SketchConstraint] = []
    labels: list[str] = []
    endpoint_data = (
        (GeometryReference(entity.id, "start"), entity.start, candidates[0]),
        (GeometryReference(entity.id, "end"), entity.end, candidates[-1]),
    )
    for reference, actual, candidate in endpoint_data:
        if candidate is None or actual.distance_to(candidate.point) > 1e-5 or candidate.entity_id == entity.id:
            continue
        constraints.extend(_point_constraints(reference, candidate))
        label = suggestion_label(candidate)
        if label:
            labels.append(label)
    return AutoConstraintSet(tuple(constraints), tuple(dict.fromkeys(labels)))


def build_ellipse_auto_constraints(
    entity: EllipseEntity,
    candidates: tuple[SnapCandidate | None, SnapCandidate | None, SnapCandidate | None],
) -> AutoConstraintSet:
    references = (
        (GeometryReference(entity.id, "center"), entity.center),
        (GeometryReference(entity.id, "major-positive"), entity.major_positive),
        (
            GeometryReference(
                entity.id,
                "minor-positive" if entity.minor_positive.distance_to(candidates[2].point) <= entity.minor_negative.distance_to(candidates[2].point) else "minor-negative",
            ) if candidates[2] is not None else GeometryReference(entity.id, "minor-positive"),
            entity.minor_positive if candidates[2] is None or entity.minor_positive.distance_to(candidates[2].point) <= entity.minor_negative.distance_to(candidates[2].point) else entity.minor_negative,
        ),
    )
    constraints: list[SketchConstraint] = []
    labels: list[str] = []
    for (reference, actual), candidate in zip(references, candidates):
        if candidate is None or actual.distance_to(candidate.point) > 1e-5 or candidate.entity_id == entity.id:
            continue
        constraints.extend(_point_constraints(reference, candidate))
        label = suggestion_label(candidate)
        if label:
            labels.append(label)
    return AutoConstraintSet(tuple(constraints), tuple(dict.fromkeys(labels)))


def build_elliptical_arc_auto_constraints(
    entity: EllipticalArcEntity,
    candidates: tuple[SnapCandidate | None, ...],
) -> AutoConstraintSet:
    if len(candidates) != 5:
        raise ValueError("Łuk elipsy wymaga pięciu kandydatów snapu.")
    constraints: list[SketchConstraint] = []
    labels: list[str] = []
    for reference, actual, candidate in (
        (GeometryReference(entity.id, "center"), entity.center, candidates[0]),
        (GeometryReference(entity.id, "start"), entity.start, candidates[3]),
        (GeometryReference(entity.id, "end"), entity.end, candidates[4]),
    ):
        if candidate is None or actual.distance_to(candidate.point) > 1e-5 or candidate.entity_id == entity.id:
            continue
        constraints.extend(_point_constraints(reference, candidate))
        label = suggestion_label(candidate)
        if label:
            labels.append(label)
    return AutoConstraintSet(tuple(constraints), tuple(dict.fromkeys(labels)))

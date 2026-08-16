from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad.auto_constraints import build_auto_constraints
from cad.commands import AddConstraintCommand, AddEntityCommand, CommandStack
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.model import CadDocument, CircleEntity, LineEntity, Point2D
from cad.snapping import SnapCandidate, SnapEngine, SnapKind
from cad.solver import fixed_constraint, solve_and_apply


def _document_with(*entities):
    document = CadDocument.create()
    for entity in entities:
        document.active_sketch._add_entity(entity)
    document.recompute()
    return document


def test_extended_snap_candidates_and_tab_index_are_deterministic() -> None:
    horizontal = LineEntity(Point2D(-50, 0), Point2D(50, 0))
    vertical = LineEntity(Point2D(0, -50), Point2D(0, 50))
    circle = CircleEntity(Point2D(30, 30), 10)
    document = _document_with(horizontal, vertical, circle)
    engine = SnapEngine(radius_pixels=14, grid_size=10)
    result = engine.query(document.active_sketch, Point2D(0.8, 0.7), pixels_per_unit=5)
    assert result.active is not None and result.active.kind == SnapKind.INTERSECTION
    assert result.active.point == Point2D(0, 0)
    assert len(result.active.related) == 2
    cycled = engine.query(document.active_sketch, Point2D(0.8, 0.7), pixels_per_unit=5, candidate_index=1)
    assert cycled.active == result.candidates[1]
    assert cycled.active != result.active

    nearest = engine.query(document.active_sketch, Point2D(20, 1), pixels_per_unit=5)
    assert any(candidate.kind == SnapKind.NEAREST for candidate in nearest.candidates)
    extension = engine.query(document.active_sketch, Point2D(60, 1), pixels_per_unit=5)
    assert any(candidate.kind == SnapKind.EXTENSION for candidate in extension.candidates)
    axis = engine.query(document.active_sketch, Point2D(12, 1), pixels_per_unit=5)
    assert any(candidate.kind == SnapKind.AXIS_X for candidate in axis.candidates)
    print("[OK] intersection, nearest, extension, axes and candidate-index cycling are deterministic")


def test_contextual_direction_tangent_and_incremental_angle_candidates() -> None:
    reference = LineEntity(Point2D(0, 0), Point2D(100, 0))
    circle = CircleEntity(Point2D(100, 0), 20)
    document = _document_with(reference, circle)
    engine = SnapEngine(radius_pixels=16)
    start = Point2D(0, 60)
    horizontal = engine.query(document.active_sketch, Point2D(50, 60.8), pixels_per_unit=5, start=start, tool="line")
    kinds = {candidate.kind for candidate in horizontal.candidates}
    assert SnapKind.HORIZONTAL in kinds and SnapKind.PARALLEL in kinds
    vertical = engine.query(document.active_sketch, Point2D(0.7, 100), pixels_per_unit=5, start=start, tool="line")
    assert any(candidate.kind in {SnapKind.VERTICAL, SnapKind.PERPENDICULAR} for candidate in vertical.candidates)
    angle = engine.query(document.active_sketch, Point2D(40, 82.5), pixels_per_unit=5, start=start, tool="line")
    assert any(candidate.kind == SnapKind.INCREMENTAL_ANGLE for candidate in angle.candidates)

    tangent_start = Point2D(40, 0)
    tangent_angle = math.acos(circle.radius / circle.center.distance_to(tangent_start))
    tangent_point = Point2D(
        circle.center.x + circle.radius * math.cos(tangent_angle + math.pi),
        circle.center.y + circle.radius * math.sin(tangent_angle + math.pi),
    )
    tangent = engine.query(document.active_sketch, tangent_point, pixels_per_unit=5, start=tangent_start, tool="line")
    assert any(candidate.kind == SnapKind.TANGENT and candidate.entity_id == circle.id for candidate in tangent.candidates)
    print("[OK] H/V, parallel, perpendicular, tangent and incremental-angle suggestions are available")


def test_point_on_object_angle_and_tangent_solver_constraints() -> None:
    document = CadDocument.create()
    sketch = document.active_sketch
    target = LineEntity(Point2D(0, 0), Point2D(100, 0))
    probe = LineEntity(Point2D(25, 5), Point2D(35, 15))
    sketch._add_entity(target)
    sketch._add_entity(probe)
    for constraint in (
        fixed_constraint(sketch, target.id),
        SketchConstraint(ConstraintType.X_COORDINATE, (GeometryReference(probe.id, "start"),), value=25),
        SketchConstraint(ConstraintType.POINT_ON_OBJECT, (GeometryReference(probe.id, "start"), GeometryReference(target.id))),
        SketchConstraint(ConstraintType.LENGTH, (GeometryReference(probe.id),), value=20),
        SketchConstraint(ConstraintType.ANGLE, (GeometryReference(probe.id),), value=30),
    ):
        sketch._add_constraint(constraint)
    solve_and_apply(sketch)
    solved_probe = sketch.entities[probe.id]
    assert isinstance(solved_probe, LineEntity)
    assert abs(solved_probe.start.y) < 1e-5
    assert abs(solved_probe.angle_deg - 30) < 1e-4

    tangent_document = CadDocument.create()
    tangent_sketch = tangent_document.active_sketch
    circle = CircleEntity(Point2D(0, 0), 10)
    line = LineEntity(Point2D(-20, 10.5), Point2D(20, 10.5))
    tangent_sketch._add_entity(circle)
    tangent_sketch._add_entity(line)
    for constraint in (
        fixed_constraint(tangent_sketch, circle.id),
        SketchConstraint(ConstraintType.X_COORDINATE, (GeometryReference(line.id, "start"),), value=-20),
        SketchConstraint(ConstraintType.Y_COORDINATE, (GeometryReference(line.id, "start"),), value=10),
        SketchConstraint(ConstraintType.LENGTH, (GeometryReference(line.id),), value=40),
        SketchConstraint(ConstraintType.TANGENT, (GeometryReference(line.id), GeometryReference(circle.id))),
    ):
        tangent_sketch._add_constraint(constraint)
    solve_and_apply(tangent_sketch)
    solved_line = tangent_sketch.entities[line.id]
    assert isinstance(solved_line, LineEntity)
    distance = abs((solved_line.end.x - solved_line.start.x) * (circle.center.y - solved_line.start.y) - (solved_line.end.y - solved_line.start.y) * (circle.center.x - solved_line.start.x)) / solved_line.length
    assert abs(distance - circle.radius) < 1e-4
    print("[OK] point-on-object, angle and tangent are real solver constraints")


def test_auto_constraints_commit_as_one_undo_transaction() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    target = LineEntity(Point2D(0, 0), Point2D(100, 0))
    stack.execute(AddEntityCommand(document.active_sketch_id, target))
    created = LineEntity(Point2D(100, 0), Point2D(160, 0))
    start_candidate = SnapCandidate(Point2D(100, 0), SnapKind.ENDPOINT, 0, target.id, "end")
    end_candidate = SnapCandidate(Point2D(160, 0), SnapKind.HORIZONTAL, 0, suggested_constraint="horizontal")
    proposal = build_auto_constraints(
        document.active_sketch,
        created,
        start_point=created.start,
        end_point=created.end,
        start_candidate=start_candidate,
        end_candidate=end_candidate,
    )
    assert {constraint.constraint_type for constraint in proposal.constraints} == {ConstraintType.COINCIDENT, ConstraintType.HORIZONTAL}
    before = len(stack.undo_commands)
    with stack.transaction("Linia z auto-więzami"):
        stack.execute(AddEntityCommand(document.active_sketch_id, created))
        for constraint in proposal.constraints:
            stack.execute(AddConstraintCommand(document.active_sketch_id, constraint))
    assert len(stack.undo_commands) == before + 1
    assert created.id in document.active_sketch.entities
    assert len(document.active_sketch.constraints) == 2
    assert stack.undo()
    assert created.id not in document.active_sketch.entities
    assert not document.active_sketch.constraints
    assert stack.redo()
    assert created.id in document.active_sketch.entities and len(document.active_sketch.constraints) == 2
    print("[OK] geometry and inferred constraints commit as one reversible transaction")


if __name__ == "__main__":
    test_extended_snap_candidates_and_tab_index_are_deterministic()
    test_contextual_direction_tangent_and_incremental_angle_candidates()
    test_point_on_object_angle_and_tangent_solver_constraints()
    test_auto_constraints_commit_as_one_undo_transaction()
    print("CAD AUTO-CONSTRAINT TESTS OK")

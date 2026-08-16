from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad.commands import (
    AddConstraintCommand,
    AddEntityCommand,
    CommandStack,
    RemoveEntitiesCommand,
    ReplaceConstraintCommand,
)
from cad.constraints import (
    ConstraintStatus,
    ConstraintType,
    GeometryReference,
    SketchConstraint,
    SketchSolveStatus,
)
from cad.io import load_document, save_document
from cad.model import CadDocument, CircleEntity, LineEntity, Point2D, RectangleEntity
from cad.solver import solve_sketch


def _constraint(kind: ConstraintType, *references: GeometryReference, value: float | None = None) -> SketchConstraint:
    return SketchConstraint(kind, tuple(references), value=value)


def test_line_constraints_reach_fully_constrained_state() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    line = LineEntity(Point2D(4, 7), Point2D(90, 31))
    stack.execute(AddEntityCommand(document.active_sketch_id, line))
    constraints = (
        _constraint(ConstraintType.HORIZONTAL, GeometryReference(line.id)),
        _constraint(ConstraintType.LENGTH, GeometryReference(line.id), value=125),
        _constraint(ConstraintType.X_COORDINATE, GeometryReference(line.id, "start"), value=10),
        _constraint(ConstraintType.Y_COORDINATE, GeometryReference(line.id, "start"), value=20),
    )
    for constraint in constraints:
        stack.execute(AddConstraintCommand(document.active_sketch_id, constraint))
    solved = document.active_sketch.entities[line.id]
    assert isinstance(solved, LineEntity)
    assert abs(solved.start.x - 10) < 1e-6
    assert abs(solved.start.y - 20) < 1e-6
    assert abs(solved.end.y - 20) < 1e-6
    assert abs(solved.length - 125) < 1e-6
    assert document.active_sketch.degrees_of_freedom == 0
    assert document.active_sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
    print("[OK] line becomes fully constrained with 0 DoF")


def test_parametric_plate_and_hole_survive_width_change() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    rectangle = RectangleEntity(Point2D(-80, -40), 160, 80)
    circle = CircleEntity(Point2D(30, 10), 5)
    for entity in (rectangle, circle):
        stack.execute(AddEntityCommand(document.active_sketch_id, entity))
    width_constraint = _constraint(
        ConstraintType.DISTANCE_X,
        GeometryReference(rectangle.id, "corner-0"),
        GeometryReference(rectangle.id, "corner-1"),
        value=180,
    )
    constraints = (
        width_constraint,
        _constraint(ConstraintType.DISTANCE_Y, GeometryReference(rectangle.id, "corner-0"), GeometryReference(rectangle.id, "corner-3"), value=100),
        _constraint(ConstraintType.X_COORDINATE, GeometryReference(rectangle.id, "center"), value=0),
        _constraint(ConstraintType.Y_COORDINATE, GeometryReference(rectangle.id, "center"), value=0),
        _constraint(ConstraintType.DIAMETER, GeometryReference(circle.id), value=14),
        _constraint(ConstraintType.X_COORDINATE, GeometryReference(circle.id, "center"), value=35),
        _constraint(ConstraintType.Y_COORDINATE, GeometryReference(circle.id, "center"), value=0),
    )
    for constraint in constraints:
        stack.execute(AddConstraintCommand(document.active_sketch_id, constraint))
    assert document.active_sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
    assert document.active_sketch.degrees_of_freedom == 0
    solved_rectangle = document.active_sketch.entities[rectangle.id]
    solved_circle = document.active_sketch.entities[circle.id]
    assert isinstance(solved_rectangle, RectangleEntity)
    assert isinstance(solved_circle, CircleEntity)
    assert solved_rectangle.center.distance_to(Point2D(0, 0)) < 1e-6
    assert abs(solved_rectangle.width - 180) < 1e-6
    assert abs(solved_rectangle.height - 100) < 1e-6
    assert abs(solved_circle.radius - 7) < 1e-6

    stack.execute(ReplaceConstraintCommand(document.active_sketch_id, replace(width_constraint, value=200)))
    resized = document.active_sketch.entities[rectangle.id]
    assert isinstance(resized, RectangleEntity)
    assert abs(resized.width - 200) < 1e-6
    assert resized.center.distance_to(Point2D(0, 0)) < 1e-6
    assert document.active_sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
    assert stack.undo()
    restored = document.active_sketch.entities[rectangle.id]
    assert isinstance(restored, RectangleEntity) and abs(restored.width - 180) < 1e-6
    assert stack.redo()
    assert abs(document.active_sketch.entities[rectangle.id].width - 200) < 1e-6
    print("[OK] parametric plate and hole remain constrained after width change")


def test_conflicting_constraints_identify_exact_constraints() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    line = LineEntity(Point2D(0, 0), Point2D(10, 0))
    stack.execute(AddEntityCommand(document.active_sketch_id, line))
    first = _constraint(ConstraintType.LENGTH, GeometryReference(line.id), value=10)
    second = _constraint(ConstraintType.LENGTH, GeometryReference(line.id), value=20)
    stack.execute(AddConstraintCommand(document.active_sketch_id, first))
    stack.execute(AddConstraintCommand(document.active_sketch_id, second))
    sketch = document.active_sketch
    assert sketch.solve_status == SketchSolveStatus.CONFLICTING
    assert sketch.constraints[first.id].status == ConstraintStatus.CONFLICTING
    assert sketch.constraints[second.id].status == ConstraintStatus.CONFLICTING
    result = solve_sketch(sketch)
    assert set(result.conflicting_ids) == {first.id, second.id}
    print("[OK] contradictory dimensional constraints are identified exactly")


def test_redundant_and_reference_constraints_are_reported() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    line = LineEntity(Point2D(0, 0), Point2D(100, 0))
    stack.execute(AddEntityCommand(document.active_sketch_id, line))
    first = _constraint(ConstraintType.HORIZONTAL, GeometryReference(line.id))
    duplicate = _constraint(ConstraintType.HORIZONTAL, GeometryReference(line.id))
    reference = SketchConstraint(
        ConstraintType.LENGTH,
        (GeometryReference(line.id),),
        value=None,
        driving=False,
    )
    for constraint in (first, duplicate, reference):
        stack.execute(AddConstraintCommand(document.active_sketch_id, constraint))
    sketch = document.active_sketch
    assert sketch.solve_status == SketchSolveStatus.REDUNDANT
    assert sketch.constraints[duplicate.id].status == ConstraintStatus.REDUNDANT
    result = solve_sketch(sketch)
    assert result.measured_values and abs(result.measured_values[reference.id] - 100) < 1e-6
    assert result.constraints[reference.id].status == ConstraintStatus.REFERENCE
    print("[OK] redundant and reference constraints have distinct diagnostics")


def test_broken_reference_is_visible_and_undo_restores_it() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    line = LineEntity(Point2D(0, 0), Point2D(25, 0))
    stack.execute(AddEntityCommand(document.active_sketch_id, line))
    length = _constraint(ConstraintType.LENGTH, GeometryReference(line.id), value=25)
    stack.execute(AddConstraintCommand(document.active_sketch_id, length))
    stack.execute(RemoveEntitiesCommand(document.active_sketch_id, (line.id,)))
    sketch = document.active_sketch
    assert sketch.solve_status == SketchSolveStatus.UNSOLVABLE
    assert sketch.constraints[length.id].status == ConstraintStatus.BROKEN_REFERENCE
    assert stack.undo()
    assert line.id in sketch.entities
    assert sketch.constraints[length.id].status == ConstraintStatus.OK
    print("[OK] broken reference is reported and undo restores model state")


def test_constraints_round_trip_with_schema_v3() -> None:
    document = CadDocument.create()
    line = LineEntity(Point2D(0, 0), Point2D(10, 0))
    document.active_sketch._add_entity(line)
    constraint = _constraint(ConstraintType.LENGTH, GeometryReference(line.id), value=10)
    document.active_sketch._add_constraint(constraint)
    payload = document.to_dict()
    assert payload["schema_version"] == 4
    restored = CadDocument.from_dict(payload)
    assert restored.active_sketch.constraint_order == [constraint.id]
    assert restored.active_sketch.constraints[constraint.id].references[0].entity_id == line.id
    with tempfile.TemporaryDirectory() as directory:
        path = save_document(document, Path(directory) / "constrained.siekcad")
        loaded = load_document(path)
        assert loaded.active_sketch.constraint_order == [constraint.id]
        assert loaded.active_sketch.degrees_of_freedom == 3
    print("[OK] constraints round-trip in native schema v4")


if __name__ == "__main__":
    test_line_constraints_reach_fully_constrained_state()
    test_parametric_plate_and_hole_survive_width_change()
    test_conflicting_constraints_identify_exact_constraints()
    test_redundant_and_reference_constraints_are_reported()
    test_broken_reference_is_visible_and_undo_restores_it()
    test_constraints_round_trip_with_schema_v3()
    print("CAD CONSTRAINT TESTS OK")

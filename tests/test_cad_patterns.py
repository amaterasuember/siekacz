from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad.commands import (
    AddConstraintCommand,
    AddEntityCommand,
    AddPatternCommand,
    CommandStack,
    EditLinearPatternCommand,
    RemovePatternCommand,
    ReplaceConstraintCommand,
)
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint, SketchSolveStatus
from cad.io import load_document, save_document
from cad.model import CadDocument, CircleEntity, Point2D, RectangleEntity
from cad.patterns import create_linear_circle_pattern


def _constraint(kind: ConstraintType, *references: GeometryReference, value: float) -> SketchConstraint:
    return SketchConstraint(kind, tuple(references), value=value)


def _fully_constrained_plate_with_hole() -> tuple[CadDocument, CommandStack, RectangleEntity, CircleEntity, SketchConstraint]:
    document = CadDocument.create()
    stack = CommandStack(document)
    plate = RectangleEntity(Point2D(-90, -50), 180, 100)
    hole = CircleEntity(Point2D(-60, 0), 7)
    for entity in (plate, hole):
        stack.execute(AddEntityCommand(document.active_sketch_id, entity))
    width = _constraint(ConstraintType.DISTANCE_X, GeometryReference(plate.id, "corner-0"), GeometryReference(plate.id, "corner-1"), value=180)
    constraints = (
        width,
        _constraint(ConstraintType.DISTANCE_Y, GeometryReference(plate.id, "corner-0"), GeometryReference(plate.id, "corner-3"), value=100),
        _constraint(ConstraintType.X_COORDINATE, GeometryReference(plate.id, "center"), value=0),
        _constraint(ConstraintType.Y_COORDINATE, GeometryReference(plate.id, "center"), value=0),
        _constraint(ConstraintType.DIAMETER, GeometryReference(hole.id), value=14),
        _constraint(ConstraintType.X_COORDINATE, GeometryReference(hole.id, "center"), value=-60),
        _constraint(ConstraintType.Y_COORDINATE, GeometryReference(hole.id, "center"), value=0),
    )
    for constraint in constraints:
        stack.execute(AddConstraintCommand(document.active_sketch_id, constraint))
    return document, stack, plate, hole, width


def test_linear_hole_pattern_is_parametric_and_fully_constrained() -> None:
    document, stack, _plate, hole, _width = _fully_constrained_plate_with_hole()
    build = create_linear_circle_pattern(document.active_sketch, hole.id, count=4, spacing_x=40, spacing_y=0)
    stack.execute(AddPatternCommand(document.active_sketch_id, build))
    sketch = document.active_sketch
    assert sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
    assert sketch.degrees_of_freedom == 0
    pattern = sketch.patterns[build.pattern.id]
    centers = [sketch.entities[entity_id].center for entity_id in pattern.generated_entity_ids]
    assert centers == [Point2D(-20, 0), Point2D(20, 0), Point2D(60, 0)]
    assert all(abs(sketch.entities[entity_id].radius - 7) < 1e-6 for entity_id in pattern.generated_entity_ids)
    assert len(pattern.constraint_ids) == 9
    print("[OK] linear hole pattern is a fully constrained parametric feature")


def test_pattern_edit_count_spacing_and_master_diameter_with_undo() -> None:
    document, stack, _plate, hole, _width = _fully_constrained_plate_with_hole()
    diameter = next(
        constraint
        for constraint in document.active_sketch.ordered_constraints()
        if constraint.constraint_type == ConstraintType.DIAMETER
    )
    build = create_linear_circle_pattern(document.active_sketch, hole.id, count=3, spacing_x=30)
    stack.execute(AddPatternCommand(document.active_sketch_id, build))
    stack.execute(EditLinearPatternCommand(document.active_sketch_id, build.pattern.id, count=5, spacing_x=25, spacing_y=5))
    pattern = document.active_sketch.patterns[build.pattern.id]
    assert pattern.count == 5
    assert len(pattern.generated_entity_ids) == 4
    assert [document.active_sketch.entities[item].center for item in pattern.generated_entity_ids] == [
        Point2D(-35, 5), Point2D(-10, 10), Point2D(15, 15), Point2D(40, 20)
    ]
    stack.execute(ReplaceConstraintCommand(document.active_sketch_id, replace(diameter, value=20)))
    assert all(abs(document.active_sketch.entities[item].radius - 10) < 1e-6 for item in pattern.generated_entity_ids)
    assert stack.undo()
    assert all(abs(document.active_sketch.entities[item].radius - 7) < 1e-6 for item in pattern.generated_entity_ids)
    assert stack.undo()
    restored_pattern = document.active_sketch.patterns[build.pattern.id]
    assert restored_pattern.count == 3
    assert len(restored_pattern.generated_entity_ids) == 2
    assert stack.redo()
    assert document.active_sketch.patterns[build.pattern.id].count == 5
    print("[OK] pattern count, spacing and master diameter remain editable with undo/redo")


def test_acceptance_a_width_change_and_native_round_trip() -> None:
    document, stack, plate, hole, width = _fully_constrained_plate_with_hole()
    build = create_linear_circle_pattern(document.active_sketch, hole.id, count=4, spacing_x=40)
    stack.execute(AddPatternCommand(document.active_sketch_id, build))
    all_constraint_ids = tuple(document.active_sketch.constraint_order)
    stack.execute(ReplaceConstraintCommand(document.active_sketch_id, replace(width, value=200)))
    sketch = document.active_sketch
    resized_plate = sketch.entities[plate.id]
    assert isinstance(resized_plate, RectangleEntity)
    assert abs(resized_plate.width - 200) < 1e-6
    assert resized_plate.center.distance_to(Point2D(0, 0)) < 1e-6
    assert sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
    assert sketch.degrees_of_freedom == 0
    assert tuple(sketch.constraint_order) == all_constraint_ids
    with tempfile.TemporaryDirectory() as directory:
        path = save_document(document, Path(directory) / "acceptance-a.siekcad")
        reopened = load_document(path)
        assert reopened.active_sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
        assert reopened.active_sketch.degrees_of_freedom == 0
        assert reopened.active_sketch.pattern_order == [build.pattern.id]
        assert abs(reopened.active_sketch.entities[plate.id].width - 200) < 1e-6
    print("[OK] acceptance A core: constrained plate, hole pattern, width change and reopen")


def test_remove_pattern_is_one_reversible_command() -> None:
    document, stack, _plate, hole, _width = _fully_constrained_plate_with_hole()
    build = create_linear_circle_pattern(document.active_sketch, hole.id, count=4, spacing_x=30)
    stack.execute(AddPatternCommand(document.active_sketch_id, build))
    stack.execute(RemovePatternCommand(document.active_sketch_id, build.pattern.id))
    assert build.pattern.id not in document.active_sketch.patterns
    assert not any(entity_id in document.active_sketch.entities for entity_id in build.pattern.generated_entity_ids)
    assert stack.undo()
    assert build.pattern.id in document.active_sketch.patterns
    assert all(entity_id in document.active_sketch.entities for entity_id in build.pattern.generated_entity_ids)
    print("[OK] removing a pattern is one reversible command")


if __name__ == "__main__":
    test_linear_hole_pattern_is_parametric_and_fully_constrained()
    test_pattern_edit_count_spacing_and_master_diameter_with_undo()
    test_acceptance_a_width_change_and_native_round_trip()
    test_remove_pattern_is_one_reversible_command()
    print("CAD PATTERN TESTS OK")

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad.commands import (
    AddConstraintCommand,
    AddEntityCommand,
    AddParameterCommand,
    CommandStack,
    EditParameterCommand,
    RemoveParameterCommand,
)
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.io import load_document, save_document
from cad.model import CadDocument, CadValidationError, Point2D, RectangleEntity
from cad.parameter_engine import bind_constraint_expression, make_parameter


def _parameter(document: CadDocument, name: str, expression: str, scope: str = ""):
    return make_parameter(document, name, expression, scope)


def test_parameter_dag_units_and_local_shadowing() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    diameter = _parameter(document, "holeDiameter", "1.4 cm")
    stack.execute(AddParameterCommand(diameter))
    margin = _parameter(document, "edgeMargin", "holeDiameter * 2")
    stack.execute(AddParameterCommand(margin))
    global_thickness = _parameter(document, "materialThickness", "10 mm")
    stack.execute(AddParameterCommand(global_thickness))
    local_thickness = _parameter(document, "materialThickness", "12 mm", document.active_sketch_id)
    stack.execute(AddParameterCommand(local_thickness))
    local_allowance = _parameter(document, "allowance", "materialThickness / 2", document.active_sketch_id)
    stack.execute(AddParameterCommand(local_allowance))
    assert document.parameters[diameter.id].value == 14
    assert document.parameters[margin.id].value == 28
    assert document.parameter_values()["materialThickness"] == 10
    assert document.parameter_values(document.active_sketch_id)["materialThickness"] == 12
    assert document.parameters[local_allowance.id].bindings[0].parameter_id == local_thickness.id
    assert document.parameters[local_allowance.id].value == 6
    print("[OK] parameter DAG evaluates units and local scope shadows global scope")


def test_parameter_drives_constraint_and_undo_redo_recomputes_geometry() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    rectangle = RectangleEntity(Point2D(0, 0), 180, 100)
    stack.execute(AddEntityCommand(document.active_sketch_id, rectangle))
    width = _parameter(document, "plateWidth", "180 mm")
    stack.execute(AddParameterCommand(width))
    raw_constraint = SketchConstraint(
        ConstraintType.DISTANCE_X,
        (GeometryReference(rectangle.id, "corner-0"), GeometryReference(rectangle.id, "corner-1")),
        value=180,
        name="Szerokość płytki",
    )
    bound = bind_constraint_expression(document, document.active_sketch_id, raw_constraint, "plateWidth")
    stack.execute(AddConstraintCommand(document.active_sketch_id, bound))
    stack.execute(EditParameterCommand(width.id, "plateWidth", "200 mm"))
    resized = document.active_sketch.entities[rectangle.id]
    assert isinstance(resized, RectangleEntity) and abs(resized.width - 200) < 1e-6
    assert document.active_sketch.constraints[bound.id].value == 200
    assert stack.undo()
    restored = document.active_sketch.entities[rectangle.id]
    assert isinstance(restored, RectangleEntity) and abs(restored.width - 180) < 1e-6
    assert stack.redo()
    assert abs(document.active_sketch.entities[rectangle.id].width - 200) < 1e-6
    print("[OK] a UUID-bound parameter drives a constraint through edit, undo and redo")


def test_rename_preserves_parameter_and_constraint_references() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    source = _parameter(document, "holeDiameter", "14 mm")
    stack.execute(AddParameterCommand(source))
    dependant = _parameter(document, "edgeMargin", "holeDiameter * 2")
    stack.execute(AddParameterCommand(dependant))
    rectangle = RectangleEntity(Point2D(0, 0), 28, 10)
    stack.execute(AddEntityCommand(document.active_sketch_id, rectangle))
    raw = SketchConstraint(
        ConstraintType.DISTANCE_X,
        (GeometryReference(rectangle.id, "corner-0"), GeometryReference(rectangle.id, "corner-1")),
        value=28,
    )
    bound = bind_constraint_expression(document, document.active_sketch_id, raw, "edgeMargin")
    stack.execute(AddConstraintCommand(document.active_sketch_id, bound))
    stack.execute(EditParameterCommand(source.id, "drillDiameter", "16 mm"))
    renamed = document.parameters[source.id]
    updated_dependant = document.parameters[dependant.id]
    assert renamed.name == "drillDiameter" and renamed.id == source.id
    assert updated_dependant.expression == "drillDiameter * 2"
    assert updated_dependant.bindings[0].parameter_id == source.id
    assert updated_dependant.value == 32
    assert abs(document.active_sketch.entities[rectangle.id].width - 32) < 1e-6
    assert document.active_sketch.constraints[bound.id].parameter_bindings[0].parameter_id == dependant.id
    print("[OK] rename rewrites display expressions while stable UUID references survive")


def test_cycles_and_removing_used_parameters_are_rejected_atomically() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    first = _parameter(document, "first", "10 mm")
    stack.execute(AddParameterCommand(first))
    second = _parameter(document, "second", "first * 2")
    stack.execute(AddParameterCommand(second))
    before = document.to_dict()
    try:
        stack.execute(EditParameterCommand(first.id, "first", "second / 2"))
    except CadValidationError as exc:
        assert "cykl" in str(exc).lower()
    else:
        raise AssertionError("parameter cycle was accepted")
    assert document.to_dict() == before
    try:
        stack.execute(RemoveParameterCommand(first.id))
    except CadValidationError as exc:
        assert "używanego" in str(exc).lower()
    else:
        raise AssertionError("used parameter was removed")
    assert document.to_dict() == before
    print("[OK] cycles and removal of referenced parameters fail without partial mutation")


def test_dimensional_error_and_shadowed_rename_are_safe() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    first = _parameter(document, "firstLength", "10 mm")
    second = _parameter(document, "secondLength", "20 mm")
    stack.execute(AddParameterCommand(first))
    stack.execute(AddParameterCommand(second))
    try:
        _parameter(document, "invalidArea", "firstLength * secondLength")
    except CadValidationError as exc:
        assert "wymiar" in str(exc).lower()
    else:
        raise AssertionError("area expression was accepted as a length")

    local_second = _parameter(document, "renamed", "5 mm", document.active_sketch_id)
    stack.execute(AddParameterCommand(local_second))
    dependant = _parameter(document, "combined", "firstLength + renamed", document.active_sketch_id)
    stack.execute(AddParameterCommand(dependant))
    stack.execute(EditParameterCommand(first.id, "renamed", "12 mm"))
    updated = document.parameters[dependant.id]
    assert updated.value == 17
    assert {binding.parameter_id for binding in updated.bindings} == {first.id, local_second.id}
    assert len({binding.alias for binding in updated.bindings}) == 2
    print("[OK] dimensional errors are rejected and rename survives a local name shadow")


def test_schema_v4_round_trip_keeps_expressions_bindings_and_ids() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    width = _parameter(document, "plateWidth", "180 mm")
    stack.execute(AddParameterCommand(width))
    half = _parameter(document, "halfWidth", "plateWidth / 2")
    stack.execute(AddParameterCommand(half))
    with tempfile.TemporaryDirectory() as directory:
        path = save_document(document, Path(directory) / "parameters.siekcad")
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 4
        restored = load_document(path)
    assert restored.to_dict() == document.to_dict()
    assert restored.parameters[half.id].bindings[0].parameter_id == width.id
    print("[OK] native schema v4 preserves parameter expressions, bindings and UUIDs")


if __name__ == "__main__":
    test_parameter_dag_units_and_local_shadowing()
    test_parameter_drives_constraint_and_undo_redo_recomputes_geometry()
    test_rename_preserves_parameter_and_constraint_references()
    test_cycles_and_removing_used_parameters_are_rejected_atomically()
    test_dimensional_error_and_shadowed_rename_are_safe()
    test_schema_v4_round_trip_keeps_expressions_bindings_and_ids()
    print("CAD PARAMETER TESTS OK")

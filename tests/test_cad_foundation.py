from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad.commands import AddEntityCommand, CommandStack, RemoveEntitiesCommand, ReplaceEntityCommand
from cad.io import CadIoError, load_document, save_document
from cad.model import (
    CadDocument,
    CadValidationError,
    LineEntity,
    Point2D,
    RectangleEntity,
    Sketch,
    line_from_length_angle,
)
from cad.parameter_engine import make_parameter, recompute_parameters
from cad.selection import SelectionModel
from cad.snapping import SnapEngine, SnapKind
from cad.units import parse_length


def test_precise_geometry_and_stable_ids() -> None:
    line = line_from_length_angle(Point2D(12.5, -3.0), 125.0, 30.0)
    assert abs(line.length - 125.0) < 1e-10
    assert abs(line.angle_deg - 30.0) < 1e-10
    rectangle = RectangleEntity(Point2D(-90, -50), 180, 100)
    assert rectangle.center == Point2D(0, 0)
    restored = CadDocument.from_dict(_document_with(line, rectangle).to_dict())
    assert restored.active_sketch.order == [line.id, rectangle.id]
    assert restored.active_sketch.entities[line.id].id == line.id
    print("[OK] CAD geometry is precise and UUIDs survive serialization")


def _document_with(*entities):
    document = CadDocument.create()
    for entity in entities:
        document.active_sketch._add_entity(entity)
    document.recompute()
    return document


def test_commands_transactions_and_undo_redo() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    line = LineEntity(Point2D(0, 0), Point2D(10, 0))
    rectangle = RectangleEntity(Point2D(0, 0), 20, 30)
    with stack.transaction("Utwórz profil"):
        stack.execute(AddEntityCommand(document.active_sketch_id, line))
        stack.execute(AddEntityCommand(document.active_sketch_id, rectangle))
    assert len(stack.undo_commands) == 1
    assert document.active_sketch.order == [line.id, rectangle.id]
    assert stack.undo()
    assert not document.active_sketch.entities
    assert stack.redo()
    assert document.active_sketch.order == [line.id, rectangle.id]

    changed = LineEntity(Point2D(0, 0), Point2D(25, 0), id=line.id)
    stack.execute(ReplaceEntityCommand(document.active_sketch_id, changed))
    assert document.active_sketch.entities[line.id].length == 25
    stack.execute(RemoveEntitiesCommand(document.active_sketch_id, (line.id, rectangle.id)))
    assert not document.active_sketch.entities
    assert stack.undo()
    assert document.active_sketch.order == [line.id, rectangle.id]
    print("[OK] CAD commands are transactional and reversible")


def test_snapping_priority_candidates_and_screen_radius() -> None:
    document = _document_with(LineEntity(Point2D(0, 0), Point2D(100, 0)))
    engine = SnapEngine(radius_pixels=12, grid_size=10)
    near_end = engine.query(document.active_sketch, Point2D(99.5, 0.3), pixels_per_unit=4)
    assert near_end.active is not None and near_end.active.kind == SnapKind.ENDPOINT
    assert near_end.point == Point2D(100, 0)
    near_middle = engine.query(document.active_sketch, Point2D(50.4, 0.2), pixels_per_unit=4)
    assert near_middle.active is not None and near_middle.active.kind == SnapKind.MIDPOINT
    too_far_on_screen = engine.query(document.active_sketch, Point2D(96, 0), pixels_per_unit=4)
    assert all(candidate.kind != SnapKind.ENDPOINT for candidate in too_far_on_screen.candidates)
    assert len(near_end.candidates) >= 1
    print("[OK] snapping exposes candidates and uses a screen-pixel radius")


def test_selection_is_renderer_independent() -> None:
    selection = SelectionModel()
    snapshots: list[tuple[str, ...]] = []
    selection.subscribe(snapshots.append)
    selection.set(("a", "b", "a"))
    selection.toggle("a")
    selection.remove_missing(("b",))
    assert selection.selected_ids == ["b"]
    assert snapshots[0] == ("a", "b")
    print("[OK] selection is a shared domain service")


def test_safe_units_and_expressions() -> None:
    assert parse_length("25") == 25
    assert parse_length("2.5 cm") == 25
    assert parse_length("1 m - 25 mm") == 975
    assert parse_length("plateWidth / 2", {"plateWidth": 180}) == 90
    assert parse_length("2,5 cm") == 25
    for unsafe in ("__import__('os')", "value.__class__", "[1][0]"):
        try:
            parse_length(unsafe, {"value": 1})
        except CadValidationError:
            pass
        else:
            raise AssertionError(f"unsafe expression accepted: {unsafe}")
    print("[OK] numeric input supports units without executing arbitrary code")


def test_native_project_round_trip_and_validation() -> None:
    document = _document_with(
        LineEntity(Point2D(0, 0), Point2D(180, 0), layer="PROFILE"),
        RectangleEntity(Point2D(-90, -50), 180, 100),
    )
    document._add_parameter(make_parameter(document, "plateWidth", "180 mm"))
    recompute_parameters(document)
    document.view_state.update({"board_width": 500.0, "board_height": 300.0})
    with tempfile.TemporaryDirectory() as directory:
        target = save_document(document, Path(directory) / "plate")
        assert target.suffix == ".siekcad"
        restored = load_document(target)
        assert restored.to_dict() == document.to_dict()
        raw = json.loads(target.read_text(encoding="utf-8"))
        raw["schema_version"] = 999
        target.write_text(json.dumps(raw), encoding="utf-8")
        try:
            load_document(target)
        except CadIoError as exc:
            assert "wersja" in str(exc).lower()
        else:
            raise AssertionError("unsupported schema version was accepted")
    print("[OK] .siekcad round-trip is versioned and validated")


def test_checked_in_v1_fixture_loads() -> None:
    fixture = Path(__file__).parent / "fixtures" / "cad" / "minimal-v1.siekcad"
    document = load_document(fixture)
    assert document.label == "Fixture CAD v1"
    assert len(document.active_sketch.entities) == 2
    assert document.parameter_values()["plateWidth"] == 180.0
    assert document.to_dict()["schema_version"] == 4
    assert not document.active_sketch.constraints
    print("[OK] checked-in CAD v1 fixture loads")


def test_schema_v2_migrates_to_v4() -> None:
    document = _document_with(RectangleEntity(Point2D(0, 0), 180, 100))
    payload = document.to_dict()
    payload["schema_version"] = 2
    payload["document"]["parameters"] = {"plateWidth": 180.0}
    for item in payload["document"]["objects"]:
        item.pop("patterns", None)
        for constraint in item.get("constraints", []):
            constraint.pop("expression", None)
            constraint.pop("parameter_bindings", None)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "legacy-v2.siekcad"
        path.write_text(json.dumps(payload), encoding="utf-8")
        migrated = load_document(path)
        assert migrated.to_dict()["schema_version"] == 4
        assert not migrated.active_sketch.patterns
        assert migrated.parameter_values()["plateWidth"] == 180
    print("[OK] CAD schema v2 migrates through v3 to v4")


def test_schema_v3_parameter_map_migrates_to_uuid_definitions() -> None:
    document = _document_with(RectangleEntity(Point2D(0, 0), 180, 100))
    payload = document.to_dict()
    payload["schema_version"] = 3
    payload["document"]["parameters"] = {"plateWidth": 180.0}
    for item in payload["document"]["objects"]:
        for constraint in item.get("constraints", []):
            constraint.pop("expression", None)
            constraint.pop("parameter_bindings", None)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "legacy-v3.siekcad"
        path.write_text(json.dumps(payload), encoding="utf-8")
        migrated = load_document(path)
    parameter = migrated.ordered_parameters()[0]
    assert migrated.to_dict()["schema_version"] == 4
    assert parameter.name == "plateWidth" and parameter.value == 180
    assert parameter.id
    print("[OK] CAD schema v3 numeric parameter map migrates to UUID definitions")


def test_dependency_cycle_is_rejected_and_recompute_is_incremental() -> None:
    document = CadDocument.create()
    second = Sketch(label="Szkic zależny", inputs={document.active_sketch_id})
    document._add_sketch(second)
    document.active_sketch.outputs.add(second.id)
    document.active_sketch.recompute_status = "dirty"
    changed = document.recompute()
    assert changed == [document.active_sketch_id, second.id]
    document.active_sketch.inputs.add(second.id)
    try:
        document.validate_dependency_graph()
    except CadValidationError as exc:
        assert "cykl" in str(exc).lower()
    else:
        raise AssertionError("dependency cycle was accepted")
    print("[OK] dependency graph recomputes dependants and rejects cycles")


def test_twenty_mixed_commands_round_trip_without_identity_loss() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    ids: list[str] = []
    for index in range(20):
        if index % 2:
            entity = RectangleEntity(Point2D(index * 3, index), 10 + index, 5 + index)
        else:
            entity = LineEntity(Point2D(index, 0), Point2D(index + 10, index + 1))
        ids.append(entity.id)
        stack.execute(AddEntityCommand(document.active_sketch_id, entity))
    assert document.active_sketch.order == ids
    for _ in range(20):
        assert stack.undo()
    assert not document.active_sketch.entities
    for _ in range(20):
        assert stack.redo()
    assert document.active_sketch.order == ids
    assert set(document.active_sketch.entities) == set(ids)
    print("[OK] 20 mixed operations undo/redo without identity loss")


if __name__ == "__main__":
    test_precise_geometry_and_stable_ids()
    test_commands_transactions_and_undo_redo()
    test_snapping_priority_candidates_and_screen_radius()
    test_selection_is_renderer_independent()
    test_safe_units_and_expressions()
    test_native_project_round_trip_and_validation()
    test_checked_in_v1_fixture_loads()
    test_schema_v2_migrates_to_v4()
    test_schema_v3_parameter_map_migrates_to_uuid_definitions()
    test_dependency_cycle_is_rejected_and_recompute_is_incremental()
    test_twenty_mixed_commands_round_trip_without_identity_loss()
    print("CAD FOUNDATION TESTS OK")

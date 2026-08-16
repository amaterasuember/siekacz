from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.technical_editor import TechnicalEditorDialog
from cad.commands import AddConstraintCommand, AddEntityCommand, ApplyTopologyEditCommand, CommandStack
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.editing import curve_intersections, extend_line, split_curve, trim_curve
from cad.model import ArcEntity, CadDocument, CadValidationError, CircleEntity, LineEntity, Point2D


def test_analytic_intersections_and_split_preserve_source_uuid() -> None:
    line = LineEntity(Point2D(0, 0), Point2D(20, 0))
    cutter = LineEntity(Point2D(10, -10), Point2D(10, 10))
    intersections = curve_intersections(line, cutter)
    assert len(intersections) == 1 and intersections[0].point == Point2D(10, 0)
    edit = split_curve(line, cutter, Point2D(10, 0))
    assert len(edit.pieces) == 2 and edit.pieces[0].id == line.id
    assert isinstance(edit.pieces[0], LineEntity) and edit.pieces[0].end == Point2D(10, 0)
    assert isinstance(edit.pieces[1], LineEntity) and edit.pieces[1].start == Point2D(10, 0)

    arc = ArcEntity(Point2D(0, 0), 10, 0, 180)
    arc_edit = split_curve(arc, LineEntity(Point2D(0, 0), Point2D(0, 20)), Point2D(0, 10))
    assert len(arc_edit.pieces) == 2
    assert all(isinstance(piece, ArcEntity) for piece in arc_edit.pieces)
    assert abs(sum(piece.sweep_angle_deg for piece in arc_edit.pieces) - 180) < 1e-9

    circle = CircleEntity(Point2D(0, 0), 10)
    circle_edit = split_curve(circle, LineEntity(Point2D(-20, 0), Point2D(20, 0)), Point2D(10, 0))
    assert len(circle_edit.pieces) == 2
    assert all(isinstance(piece, ArcEntity) and abs(piece.sweep_angle_deg - 180) < 1e-9 for piece in circle_edit.pieces)
    print("[OK] line/arc/circle split uses analytic intersections and stable source UUID")


def test_trim_uses_pick_point_and_extend_uses_selected_endpoint() -> None:
    line = LineEntity(Point2D(0, 0), Point2D(20, 0))
    cutter = LineEntity(Point2D(10, -10), Point2D(10, 10))
    trimmed = trim_curve(line, cutter, Point2D(18, 0))
    assert len(trimmed.pieces) == 1
    kept = trimmed.pieces[0]
    assert isinstance(kept, LineEntity) and kept.start == Point2D(0, 0) and kept.end == Point2D(10, 0)

    circle = CircleEntity(Point2D(0, 0), 10)
    circle_trim = trim_curve(circle, LineEntity(Point2D(-20, 0), Point2D(20, 0)), Point2D(0, 9))
    assert len(circle_trim.pieces) == 1 and isinstance(circle_trim.pieces[0], ArcEntity)
    assert circle_trim.pieces[0].midpoint.distance_to(Point2D(0, -10)) < 1e-7

    short = LineEntity(Point2D(0, 0), Point2D(10, 0))
    boundary = LineEntity(Point2D(20, -5), Point2D(20, 5))
    extended = extend_line(short, boundary, Point2D(9, 0))
    result = extended.pieces[0]
    assert isinstance(result, LineEntity) and result.start == short.start and result.end == Point2D(20, 0)
    try:
        extend_line(short, boundary, Point2D(1, 0))
    except CadValidationError as exc:
        assert "początku" in str(exc)
    else:
        raise AssertionError("extension on the wrong side must fail explicitly")
    print("[OK] trim follows the pick point and extend follows the selected endpoint")


def test_topology_command_is_atomic_and_removes_stale_constraints_reversibly() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    source = LineEntity(Point2D(0, 0), Point2D(20, 0))
    cutter = LineEntity(Point2D(10, -10), Point2D(10, 10))
    stack.execute(AddEntityCommand(document.active_sketch_id, source))
    stack.execute(AddEntityCommand(document.active_sketch_id, cutter))
    length = SketchConstraint(ConstraintType.LENGTH, (GeometryReference(source.id),), value=20)
    stack.execute(AddConstraintCommand(document.active_sketch_id, length))
    before = document.to_dict()
    undo_count = len(stack.undo_commands)

    command = ApplyTopologyEditCommand(
        document.active_sketch_id,
        split_curve(source, cutter, Point2D(10, 0)),
        "Podziel geometrię",
    )
    stack.execute(command)
    assert len(stack.undo_commands) == undo_count + 1
    assert len(document.active_sketch.entities) == 3
    assert length.id not in document.active_sketch.constraints
    assert command.removed_constraint_ids == (length.id,)
    assert len(command.created_constraint_ids) == 1
    continuity = document.active_sketch.constraints[command.created_constraint_ids[0]]
    assert continuity.constraint_type == ConstraintType.COINCIDENT
    assert any("usunęła 1 więzów" in message for message in document.active_sketch.diagnostics)
    assert stack.undo() and document.to_dict() == before
    assert stack.redo() and len(document.active_sketch.entities) == 3
    assert command.created_constraint_ids[0] in document.active_sketch.constraints
    print("[OK] topology edit and stale-constraint cleanup are one exact undo/redo step")


def test_locked_geometry_is_rejected_without_partial_mutation() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    source = LineEntity(Point2D(0, 0), Point2D(20, 0), locked=True)
    cutter = LineEntity(Point2D(10, -10), Point2D(10, 10))
    document.active_sketch._add_entity(source)
    document.active_sketch._add_entity(cutter)
    before = document.to_dict()
    try:
        stack.execute(ApplyTopologyEditCommand(document.active_sketch_id, split_curve(source, cutter, Point2D(10, 0))))
    except CadValidationError as exc:
        assert "Zablokowanej" in str(exc)
    else:
        raise AssertionError("locked source must be rejected")
    assert document.to_dict() == before
    print("[OK] locked topology rejects the edit without partial mutation")


def test_topology_command_rolls_back_if_recompute_fails() -> None:
    document = CadDocument.create()
    stack = CommandStack(document)
    source = LineEntity(Point2D(0, 0), Point2D(20, 0))
    cutter = LineEntity(Point2D(10, -10), Point2D(10, 10))
    document.active_sketch._add_entity(source)
    document.active_sketch._add_entity(cutter)
    before = document.to_dict()
    command = ApplyTopologyEditCommand(document.active_sketch_id, split_curve(source, cutter, Point2D(10, 0)))
    with patch("cad.commands._solve_and_apply", side_effect=RuntimeError("forced recompute failure")):
        try:
            stack.execute(command)
        except RuntimeError as exc:
            assert "forced" in str(exc)
        else:
            raise AssertionError("forced recompute error must propagate")
    assert document.to_dict() == before
    print("[OK] recompute failure rolls back every topology mutation")


def test_workspace_split_uses_cursor_pick_and_one_undo_step() -> None:
    QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    source = LineEntity(Point2D(0, 0), Point2D(20, 0))
    cutter = LineEntity(Point2D(10, -10), Point2D(10, 10))
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, source))
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, cutter))
    dialog.selection_model.set((source.id, cutter.id))
    dialog.selection_model.preselected_id = source.id
    dialog.canvas._last_cursor_model = Point2D(12, 0)
    before = len(dialog.command_stack.undo_commands)
    dialog._split_selected_curve()
    assert len(dialog.command_stack.undo_commands) == before + 1
    assert len(dialog.document.active_sketch.entities) == 3
    assert len(dialog.selection_model.selected_ids) == 2
    assert "wynik zawiera 2 krzywe" in dialog.status_label.text()
    assert dialog.command_stack.undo()
    assert set(dialog.document.active_sketch.entities) == {source.id, cutter.id}
    dialog.close()
    print("[OK] workspace split follows preselection/cursor and is one undo step")


if __name__ == "__main__":
    test_analytic_intersections_and_split_preserve_source_uuid()
    test_trim_uses_pick_point_and_extend_uses_selected_endpoint()
    test_topology_command_is_atomic_and_removes_stale_constraints_reversibly()
    test_locked_geometry_is_rejected_without_partial_mutation()
    test_topology_command_rolls_back_if_recompute_fails()
    test_workspace_split_uses_cursor_pick_and_one_undo_step()
    print("CAD EDITING TESTS OK")

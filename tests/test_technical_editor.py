from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QGraphicsItem, QInputDialog

from app.technical_editor import LineSpecificationDialog, ParameterEditorDialog, ParameterManagerDialog, ProfileDiagnosticsDialog, TechnicalEditorDialog
from cad.commands import AddConstraintCommand, AddEntityCommand, AddParameterCommand, AddPatternCommand, EditParameterCommand, ReplaceConstraintCommand
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint, SketchSolveStatus
from cad.io import load_document, save_document
from cad.model import LineEntity, Point2D, RectangleEntity
from cad.model import CircleEntity
from cad.patterns import create_linear_circle_pattern
from cad.parameter_engine import bind_constraint_expression, make_parameter
from cad.profiles import ProfileIssueType, ProfileTolerance, analyze_profile
from cad.snapping import SnapCandidate, SnapKind


def _dialog() -> TechnicalEditorDialog:
    QApplication.instance() or QApplication([])
    return TechnicalEditorDialog()


def test_editor_renders_domain_geometry_and_uses_model_bounds() -> None:
    dialog = _dialog()
    line = LineEntity(Point2D(10, 20), Point2D(110, 20))
    rectangle = RectangleEntity(Point2D(160, 80), 40, 30)
    for entity in (line, rectangle):
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    bounds = dialog.canvas.drawing_bounds()
    assert round(bounds.width()) == 190
    assert round(bounds.height()) == 90
    assert set(dialog.canvas._entity_items) == {line.id, rectangle.id}
    assert dialog.document.active_sketch.entities[line.id] == line
    dialog.close()
    print("[OK] renderer follows the domain model and uses model bounds")


def test_renderer_items_cannot_mutate_domain_geometry_by_dragging() -> None:
    dialog = _dialog()
    entity = RectangleEntity(Point2D(0, 0), 100, 50)
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    item = dialog.canvas._entity_items[entity.id]
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    assert not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    item.setPos(500, 500)  # renderer state must never become model state
    assert dialog.document.active_sketch.entities[entity.id].origin == Point2D(0, 0)
    dialog.canvas.sync_from_model()
    assert dialog.canvas._entity_items[entity.id].pos() == QPointF(0, 0)
    dialog.close()
    print("[OK] renderer is not the source of truth")


def test_editor_undo_redo_keeps_renderer_and_selection_consistent() -> None:
    dialog = _dialog()
    line = LineEntity(Point2D(0, 0), Point2D(125, 0))
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, line))
    dialog.selection_model.set((line.id,))
    assert line.id in dialog.canvas._entity_items
    assert dialog.command_stack.undo()
    assert line.id not in dialog.document.active_sketch.entities
    assert line.id not in dialog.canvas._entity_items
    assert not dialog.selection_model.selected_ids
    assert dialog.command_stack.redo()
    assert line.id in dialog.document.active_sketch.entities
    assert line.id in dialog.canvas._entity_items
    dialog.close()
    print("[OK] undo/redo keeps model, renderer and selection consistent")


def test_editor_snapping_uses_domain_entities_and_screen_radius() -> None:
    dialog = _dialog()
    line = LineEntity(Point2D(0, 0), Point2D(100, 0))
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, line))
    snapped = dialog.canvas._snap(QPointF(101, -1))
    model_point = dialog.canvas.scene_to_model(snapped)
    assert model_point.distance_to(Point2D(100, 0)) < 0.001
    result = dialog.canvas.snap_engine.query(
        dialog.document.active_sketch,
        Point2D(50.5, 0.2),
        pixels_per_unit=4,
    )
    assert result.active is not None
    assert result.active.kind.value == "midpoint"
    dialog.close()
    print("[OK] snapping reads the model and uses a pixel-space radius")


def test_first_vertical_slice_survives_save_reopen_and_render() -> None:
    dialog = _dialog()
    entities = (
        LineEntity(Point2D(0, 0), Point2D(180, 0)),
        RectangleEntity(Point2D(-90, -50), 180, 100),
    )
    for entity in entities:
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    with tempfile.TemporaryDirectory() as directory:
        path = save_document(dialog.document, Path(directory) / "precise-plate.siekcad")
        reopened = load_document(path)
        dialog._set_document(reopened, path)
        assert reopened.active_sketch.order == [entity.id for entity in entities]
        assert set(dialog.canvas._entity_items) == {entity.id for entity in entities}
        assert not dialog.command_stack.undo_commands
        bounds = dialog.canvas.drawing_bounds()
        assert (bounds.width(), bounds.height()) == (270.0, 100.0)
    dialog.close()
    print("[OK] create -> save -> reopen -> render vertical slice")


def test_constraint_workspace_updates_solver_tree_and_undo() -> None:
    dialog = _dialog()
    line = LineEntity(Point2D(0, 0), Point2D(100, 25))
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, line))
    dialog.selection_model.set((line.id,))
    dialog._add_horizontal_constraint()
    sketch = dialog.document.active_sketch
    assert len(sketch.constraints) == 1
    constraint_id = sketch.constraint_order[0]
    solved = sketch.entities[line.id]
    assert isinstance(solved, LineEntity)
    assert abs(solved.start.y - solved.end.y) < 1e-6
    assert "DoF: 3" in dialog.solver_status.text()
    tree_tokens = []
    iterator = dialog.model_tree.invisibleRootItem()

    def collect(item):
        tree_tokens.append(str(item.data(0, 256) or ""))
        for index in range(item.childCount()):
            collect(item.child(index))

    collect(iterator)
    assert f"constraint:{constraint_id}" in tree_tokens
    dialog.selected_constraint_id = constraint_id
    dialog._toggle_selected_constraint()
    assert not sketch.constraints[constraint_id].enabled
    assert dialog.command_stack.undo()
    assert sketch.constraints[constraint_id].enabled
    dialog.close()
    print("[OK] constraint UI synchronizes solver, tree and undo")


def test_pattern_workspace_tree_and_selection_are_synchronized() -> None:
    dialog = _dialog()
    source = CircleEntity(Point2D(10, 20), 7)
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, source))
    build = create_linear_circle_pattern(dialog.document.active_sketch, source.id, 4, 30, 0)
    dialog.command_stack.execute(AddPatternCommand(dialog.document.active_sketch_id, build))
    dialog.selected_pattern_id = build.pattern.id
    dialog.selection_model.set((source.id, *build.pattern.generated_entity_ids))
    dialog._refresh_side_panels()
    assert "Liczba elementów: 4" in dialog.property_details.text()
    assert len(dialog.selection_model.selected_ids) == 4
    tokens = []

    def collect(item):
        tokens.append(str(item.data(0, 256) or ""))
        for index in range(item.childCount()):
            collect(item.child(index))

    collect(dialog.model_tree.invisibleRootItem())
    assert f"pattern:{build.pattern.id}" in tokens
    dialog._delete_selected_pattern()
    assert build.pattern.id not in dialog.document.active_sketch.patterns
    assert dialog.command_stack.undo()
    assert build.pattern.id in dialog.document.active_sketch.patterns
    dialog.close()
    print("[OK] pattern UI synchronizes tree, selection and undo")


def test_acceptance_a_can_be_built_through_workspace_commands() -> None:
    dialog = _dialog()
    plate = RectangleEntity(Point2D(-90, -50), 180, 100)
    hole = CircleEntity(Point2D(-60, 0), 7)
    for entity in (plate, hole):
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    dialog.selection_model.set((plate.id,))
    dialog._constrain_rectangle_dimensions()
    dialog._center_at_origin()
    dialog.selection_model.set((hole.id,))
    diameter = SketchConstraint(ConstraintType.DIAMETER, (GeometryReference(hole.id),), value=14)
    dialog.command_stack.execute(AddConstraintCommand(dialog.document.active_sketch_id, diameter))
    dialog._constrain_current_center()
    build = create_linear_circle_pattern(dialog.document.active_sketch, hole.id, 4, 40, 0)
    dialog.command_stack.execute(AddPatternCommand(dialog.document.active_sketch_id, build))
    assert dialog.document.active_sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
    assert dialog.document.active_sketch.degrees_of_freedom == 0
    width = next(
        constraint
        for constraint in dialog.document.active_sketch.ordered_constraints()
        if constraint.name.startswith("Szerokość")
    )
    dialog.command_stack.execute(ReplaceConstraintCommand(dialog.document.active_sketch_id, replace(width, value=200)))
    resized = dialog.document.active_sketch.entities[plate.id]
    assert isinstance(resized, RectangleEntity)
    assert abs(resized.width - 200) < 1e-6
    assert resized.center.distance_to(Point2D(0, 0)) < 1e-6
    assert dialog.document.active_sketch.solve_status == SketchSolveStatus.FULLY_CONSTRAINED
    dialog.close()
    print("[OK] acceptance A is executable through workspace commands")


def test_profile_diagnostics_selects_and_repairs_geometry_through_workspace() -> None:
    dialog = _dialog()
    lines = (
        LineEntity(Point2D(0, 0), Point2D(100, 0)),
        LineEntity(Point2D(100.05, 0), Point2D(100, 100)),
        LineEntity(Point2D(100, 100), Point2D(0, 100)),
        LineEntity(Point2D(0, 100), Point2D(0, 0)),
    )
    for entity in lines:
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    tolerance = ProfileTolerance(join=0.1)
    report = analyze_profile(dialog.document.active_sketch, tolerance)
    small_gap = next(issue for issue in report.issues if issue.issue_type == ProfileIssueType.SMALL_GAP)
    review = ProfileDiagnosticsDialog(report, dialog._select_profile_issue, dialog)
    gap_index = report.issues.index(small_gap)
    review.issue_list.setCurrentRow(gap_index)
    QApplication.processEvents()
    assert set(dialog.selection_model.selected_ids) == set(small_gap.entity_ids)
    before_undo = len(dialog.command_stack.undo_commands)
    repaired = dialog._apply_profile_repair(
        report,
        remove_duplicates=True,
        remove_micro_segments=False,
        close_small_gaps=True,
    )
    assert repaired.is_valid_surface
    assert len(dialog.command_stack.undo_commands) == before_undo + 1
    assert set(dialog.canvas._entity_items) == {line.id for line in lines}
    assert dialog.command_stack.undo()
    assert analyze_profile(dialog.document.active_sketch, tolerance).open_endpoint_count == 2
    review.close()
    dialog.close()
    print("[OK] profile diagnostics selects affected geometry and repairs in one undo step")


def test_parameter_workspace_drives_constraint_tree_manager_and_undo() -> None:
    dialog = _dialog()
    rectangle = RectangleEntity(Point2D(0, 0), 180, 100)
    dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, rectangle))
    parameter = make_parameter(dialog.document, "plateWidth", "180 mm")
    dialog.command_stack.execute(AddParameterCommand(parameter))
    raw = SketchConstraint(
        ConstraintType.DISTANCE_X,
        (GeometryReference(rectangle.id, "corner-0"), GeometryReference(rectangle.id, "corner-1")),
        value=180,
        name="Szerokość płytki",
    )
    bound = bind_constraint_expression(dialog.document, dialog.document.active_sketch_id, raw, "plateWidth")
    dialog.command_stack.execute(AddConstraintCommand(dialog.document.active_sketch_id, bound))
    editor = ParameterEditorDialog(dialog.document, parameter, dialog)
    assert "180" in editor.preview.text()
    manager = ParameterManagerDialog(dialog.document, dialog.command_stack, dialog)
    assert manager.table.rowCount() == 1
    dialog.selected_parameter_id = parameter.id
    dialog._refresh_side_panels()
    assert "plateWidth" in dialog.property_title.text()
    assert "180 mm" in dialog.property_details.text()
    tokens = []

    def collect(item):
        tokens.append(str(item.data(0, 256) or ""))
        for index in range(item.childCount()):
            collect(item.child(index))

    collect(dialog.model_tree.invisibleRootItem())
    assert f"parameter:{parameter.id}" in tokens
    dialog.command_stack.execute(EditParameterCommand(parameter.id, "plateWidth", "200 mm"))
    resized = dialog.document.active_sketch.entities[rectangle.id]
    assert isinstance(resized, RectangleEntity) and abs(resized.width - 200) < 1e-6
    assert dialog.document.active_sketch.constraints[bound.id].expression == "plateWidth"
    assert dialog.command_stack.undo()
    assert abs(dialog.document.active_sketch.entities[rectangle.id].width - 180) < 1e-6
    manager.close()
    editor.close()
    dialog.close()
    print("[OK] parameter manager, tree, expression-bound constraint and undo stay synchronized")


def test_canvas_cycles_snap_candidates_and_commits_visible_auto_constraints() -> None:
    dialog = _dialog()
    horizontal = LineEntity(Point2D(-50, 0), Point2D(50, 0))
    vertical = LineEntity(Point2D(0, -50), Point2D(0, 50))
    for entity in (horizontal, vertical):
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    result = dialog.canvas._snap_result(Point2D(0.5, 0.5))
    assert result.active is not None and len(result.candidates) > 1
    assert sum(marker.isVisible() for marker in dialog.canvas.snap_candidate_markers) >= 2
    assert dialog.canvas.snap_glyph.isVisible()
    assert "AUTO-WIĘZ" in dialog.status_label.text()
    first_active = result.active
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    dialog.canvas.keyPressEvent(event)
    assert dialog.canvas._last_snap_result.active != first_active

    target = horizontal
    start = Point2D(50, 0)
    end = Point2D(110, 0)
    start_candidate = SnapCandidate(start, SnapKind.ENDPOINT, 0, target.id, "end")
    end_candidate = SnapCandidate(end, SnapKind.HORIZONTAL, 0, suggested_constraint="horizontal")
    undo_before = len(dialog.command_stack.undo_commands)
    with patch.object(LineSpecificationDialog, "exec", return_value=LineSpecificationDialog.DialogCode.Accepted), patch.object(
        LineSpecificationDialog,
        "values",
        return_value=(start, 60.0, 0.0),
    ):
        dialog._create_geometry("line", start, end, start_candidate, end_candidate)
    assert len(dialog.command_stack.undo_commands) == undo_before + 1
    auto_constraints = [constraint for constraint in dialog.document.active_sketch.constraints.values() if constraint.name.startswith("Auto")]
    assert {constraint.constraint_type for constraint in auto_constraints} == {ConstraintType.COINCIDENT, ConstraintType.HORIZONTAL}
    assert "Dodano auto-więzy" in dialog.status_label.text()
    assert dialog.command_stack.undo()
    assert not dialog.document.active_sketch.constraints
    dialog.close()
    print("[OK] canvas shows/cycles candidates and commits previewed auto-constraints atomically")


def test_manual_extended_constraints_are_available_from_workspace() -> None:
    dialog = _dialog()
    first = LineEntity(Point2D(0, 0), Point2D(40, 0))
    second = LineEntity(Point2D(0, 20), Point2D(40, 20))
    for entity in (first, second):
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    dialog.selection_model.set((first.id, second.id))
    dialog._add_direction_pair_constraint(ConstraintType.PARALLEL, "Równoległość")
    assert any(
        constraint.constraint_type == ConstraintType.PARALLEL
        for constraint in dialog.document.active_sketch.constraints.values()
    )
    dialog.selection_model.set((first.id,))
    with patch.object(QInputDialog, "getDouble", return_value=(30.0, True)):
        dialog._add_angle_constraint()
    assert any(
        constraint.constraint_type == ConstraintType.ANGLE and constraint.value == 30.0
        for constraint in dialog.document.active_sketch.constraints.values()
    )
    dialog.close()

    dialog = _dialog()
    source = LineEntity(Point2D(0, 0), Point2D(10, 0))
    target = LineEntity(Point2D(10, -10), Point2D(10, 10))
    for entity in (source, target):
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    dialog.selection_model.set((source.id, target.id))
    dialog._add_point_on_object_constraint()
    assert any(
        constraint.constraint_type == ConstraintType.POINT_ON_OBJECT
        for constraint in dialog.document.active_sketch.constraints.values()
    )
    dialog.close()

    dialog = _dialog()
    tangent_line = LineEntity(Point2D(-20, 0), Point2D(20, 0))
    circle = CircleEntity(Point2D(0, 5), 5)
    for entity in (tangent_line, circle):
        dialog.command_stack.execute(AddEntityCommand(dialog.document.active_sketch_id, entity))
    dialog.selection_model.set((tangent_line.id, circle.id))
    dialog._add_tangent_constraint()
    assert any(
        constraint.constraint_type == ConstraintType.TANGENT
        for constraint in dialog.document.active_sketch.constraints.values()
    )
    dialog.close()
    print("[OK] manual point-on-object, parallel, tangent and angle constraints are available")


if __name__ == "__main__":
    test_editor_renders_domain_geometry_and_uses_model_bounds()
    test_renderer_items_cannot_mutate_domain_geometry_by_dragging()
    test_editor_undo_redo_keeps_renderer_and_selection_consistent()
    test_editor_snapping_uses_domain_entities_and_screen_radius()
    test_first_vertical_slice_survives_save_reopen_and_render()
    test_constraint_workspace_updates_solver_tree_and_undo()
    test_pattern_workspace_tree_and_selection_are_synchronized()
    test_acceptance_a_can_be_built_through_workspace_commands()
    test_profile_diagnostics_selects_and_repairs_geometry_through_workspace()
    test_parameter_workspace_drives_constraint_tree_manager_and_undo()
    test_canvas_cycles_snap_candidates_and_commits_visible_auto_constraints()
    test_manual_extended_constraints_are_available_from_workspace()
    print("TECHNICAL EDITOR TESTS OK")

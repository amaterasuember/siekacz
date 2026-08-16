from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from app.technical_editor import (
    CenterRectangleSpecificationDialog,
    PointSpecificationDialog,
    RegularPolygonSpecificationDialog,
    SlotSpecificationDialog,
    TechnicalEditorDialog,
)
from cad.auto_constraints import build_auto_constraints
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.model import (
    CadDocument,
    LineEntity,
    Point2D,
    PointEntity,
    PolylineEntity,
    RectangleEntity,
    RegularPolygonEntity,
    SlotEntity,
    rectangle_from_center,
    regular_polygon,
    slot_from_three_points,
)
from cad.profiles import analyze_profile
from cad.snapping import SnapCandidate, SnapEngine, SnapKind
from cad.solver import solve_and_apply


def test_native_point_center_rectangle_polygon_and_slot_round_trip() -> None:
    point = PointEntity(Point2D(3, 4), layer="MARKS")
    rectangle = rectangle_from_center(Point2D(10, 20), 40, 12)
    polygon = regular_polygon(Point2D(-10, 5), 25, 6, 30)
    slot = slot_from_three_points(Point2D(0, 0), Point2D(60, 0), Point2D(20, 8))
    assert rectangle.center == Point2D(10, 20)
    assert rectangle.origin == Point2D(-10, 14)
    assert isinstance(polygon, RegularPolygonEntity) and len(polygon.points) == 6 and polygon.sides == 6
    side_lengths = [start.distance_to(end) for start, end in polygon.segments]
    assert max(side_lengths) - min(side_lengths) < 1e-9
    assert slot.radius == 8 and slot.width == 16
    assert math.isclose(slot.area, 60 * 16 + math.pi * 8**2)
    assert len(slot.boundary_lines) == 2 and len(slot.boundary_arcs) == 2

    document = CadDocument.create()
    for entity in (point, rectangle, polygon, slot):
        document.active_sketch._add_entity(entity)
    restored = CadDocument.from_dict(document.to_dict())
    assert restored.active_sketch.entities == document.active_sketch.entities
    assert isinstance(restored.active_sketch.entities[point.id], PointEntity)
    assert isinstance(restored.active_sketch.entities[slot.id], SlotEntity)
    print("[OK] point, center rectangle, regular polygon and analytic slot serialize exactly")


def test_slot_solver_snapping_intersections_and_profile() -> None:
    document = CadDocument.create()
    slot = SlotEntity(Point2D(0, 0), Point2D(40, 1), 6)
    point = PointEntity(Point2D(20, 6))
    document.active_sketch._add_entity(slot)
    document.active_sketch._add_entity(point)
    document.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.HORIZONTAL,
        (GeometryReference(slot.id, "axis"),),
    ))
    document.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.RADIUS,
        (GeometryReference(slot.id),),
        value=8,
    ))
    document.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.POINT_ON_OBJECT,
        (GeometryReference(point.id, "point"), GeometryReference(slot.id)),
    ))
    result = solve_and_apply(document.active_sketch)
    solved_slot = document.active_sketch.entities[slot.id]
    solved_point = document.active_sketch.entities[point.id]
    assert result.solved and isinstance(solved_slot, SlotEntity) and isinstance(solved_point, PointEntity)
    assert abs(solved_slot.axis_start.y - solved_slot.axis_end.y) < 1e-7
    assert abs(solved_slot.radius - 8) < 1e-7

    profile_document = CadDocument.create()
    profile_slot = SlotEntity(Point2D(0, 0), Point2D(40, 0), 5)
    crossing = LineEntity(Point2D(20, -10), Point2D(20, 10))
    profile_document.active_sketch._add_entity(profile_slot)
    profile_document.active_sketch._add_entity(crossing, 1)
    report = analyze_profile(profile_document.active_sketch)
    assert not report.is_valid_surface  # the crossing line intentionally pierces the slot

    clean_profile = CadDocument.create()
    clean_profile.active_sketch._add_entity(profile_slot)
    clean_report = analyze_profile(clean_profile.active_sketch)
    assert clean_report.is_valid_surface and len(clean_report.loops) == 1 and not clean_report.issues

    engine = SnapEngine(radius_pixels=16)
    center = engine.query(clean_profile.active_sketch, Point2D(20.1, 0.1), pixels_per_unit=4)
    assert center.active is not None and center.active.kind == SnapKind.CENTER
    boundary = engine.query(clean_profile.active_sketch, Point2D(20, 5.1), pixels_per_unit=4)
    assert any(candidate.kind == SnapKind.NEAREST for candidate in boundary.candidates)
    intersection = engine.query(profile_document.active_sketch, Point2D(20, 5), pixels_per_unit=4)
    assert any(candidate.kind == SnapKind.INTERSECTION for candidate in intersection.candidates)
    print("[OK] analytic slot participates in solver, snapping and closed-profile diagnostics")


def test_regular_polygon_solver_preserves_semantics_and_foremness() -> None:
    document = CadDocument.create()
    polygon = regular_polygon(Point2D(4, 7), 20, 7, 12)
    document.active_sketch._add_entity(polygon)
    first_start, first_end = polygon.segments[0]
    side_angle = math.degrees(math.atan2(first_end.y - first_start.y, first_end.x - first_start.x))
    for constraint in (
        SketchConstraint(ConstraintType.X_COORDINATE, (GeometryReference(polygon.id, "center"),), value=0),
        SketchConstraint(ConstraintType.Y_COORDINATE, (GeometryReference(polygon.id, "center"),), value=0),
        SketchConstraint(ConstraintType.RADIUS, (GeometryReference(polygon.id),), value=25),
        SketchConstraint(ConstraintType.ANGLE, (GeometryReference(polygon.id, "segment-0"),), value=side_angle),
    ):
        document.active_sketch._add_constraint(constraint)
    result = solve_and_apply(document.active_sketch)
    solved = document.active_sketch.entities[polygon.id]
    assert result.solved and result.degrees_of_freedom == 0
    assert isinstance(solved, RegularPolygonEntity) and solved.sides == 7
    assert solved.center.distance_to(Point2D(0, 0)) < 1e-7 and abs(solved.radius - 25) < 1e-7
    lengths = [start.distance_to(end) for start, end in solved.segments]
    assert max(lengths) - min(lengths) < 1e-9
    print("[OK] polygon center/radius/rotation solve without losing native side-count semantics")


def test_point_and_slot_auto_constraints_are_real_solver_constraints() -> None:
    document = CadDocument.create()
    target = LineEntity(Point2D(-20, 0), Point2D(0, 0))
    document.active_sketch._add_entity(target)
    point = PointEntity(Point2D(0, 0))
    endpoint = SnapCandidate(Point2D(0, 0), SnapKind.ENDPOINT, 0, target.id, "end")
    point_proposal = build_auto_constraints(
        document.active_sketch,
        point,
        start_point=point.point,
        end_point=point.point,
        start_candidate=endpoint,
        end_candidate=endpoint,
    )
    assert len(point_proposal.constraints) == 1
    assert point_proposal.constraints[0].constraint_type == ConstraintType.COINCIDENT
    assert point_proposal.constraints[0].references[0].element == "point"

    slot = SlotEntity(Point2D(0, 0), Point2D(30, 0), 4)
    horizontal = SnapCandidate(Point2D(30, 0), SnapKind.HORIZONTAL, 0, suggested_constraint="horizontal")
    slot_proposal = build_auto_constraints(
        document.active_sketch,
        slot,
        start_point=slot.axis_start,
        end_point=slot.axis_end,
        start_candidate=endpoint,
        end_candidate=horizontal,
    )
    kinds = {constraint.constraint_type for constraint in slot_proposal.constraints}
    assert kinds == {ConstraintType.COINCIDENT, ConstraintType.HORIZONTAL}
    assert any(constraint.references[0].element == "axis" for constraint in slot_proposal.constraints)
    print("[OK] point and slot snap suggestions become real, deduplicated constraints")


def _click_model(dialog: TechnicalEditorDialog, point: Point2D) -> None:
    viewport_point = dialog.canvas.mapFromScene(dialog.canvas.model_to_scene(point))
    QTest.mouseClick(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=viewport_point)


def _drag_model(dialog: TechnicalEditorDialog, start: Point2D, end: Point2D) -> None:
    start_view = dialog.canvas.mapFromScene(dialog.canvas.model_to_scene(start))
    end_view = dialog.canvas.mapFromScene(dialog.canvas.model_to_scene(end))
    QTest.mousePress(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start_view)
    QTest.mouseMove(dialog.canvas.viewport(), end_view)
    QTest.mouseRelease(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end_view)


def test_workspace_tools_commit_precise_shapes_and_keep_tool_active() -> None:
    QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    dialog.show()
    QApplication.processEvents()
    dialog.canvas.fit_board()
    QApplication.processEvents()

    dialog._select_tool("point")
    with patch.object(PointSpecificationDialog, "exec", return_value=PointSpecificationDialog.DialogCode.Accepted), patch.object(
        PointSpecificationDialog, "values", return_value=Point2D(100, 100)
    ):
        _click_model(dialog, Point2D(100, 100))
    assert any(isinstance(entity, PointEntity) for entity in dialog.document.active_sketch.entities.values())
    assert dialog.canvas.tool == "point"

    dialog._select_tool("rect_center")
    with patch.object(CenterRectangleSpecificationDialog, "exec", return_value=CenterRectangleSpecificationDialog.DialogCode.Accepted), patch.object(
        CenterRectangleSpecificationDialog, "values", return_value=(Point2D(300, 300), 120.0, 80.0)
    ):
        _drag_model(dialog, Point2D(300, 300), Point2D(360, 340))
    rectangles = [entity for entity in dialog.document.active_sketch.entities.values() if isinstance(entity, RectangleEntity)]
    assert len(rectangles) == 1 and rectangles[0].center == Point2D(300, 300)

    dialog._select_tool("polygon")
    with patch.object(RegularPolygonSpecificationDialog, "exec", return_value=RegularPolygonSpecificationDialog.DialogCode.Accepted), patch.object(
        RegularPolygonSpecificationDialog, "values", return_value=(Point2D(500, 300), 50.0, 5, 0.0)
    ):
        _drag_model(dialog, Point2D(500, 300), Point2D(550, 300))
    assert any(isinstance(entity, RegularPolygonEntity) and entity.sides == 5 for entity in dialog.document.active_sketch.entities.values())

    slot_points = (Point2D(150, 600), Point2D(350, 600), Point2D(250, 625))
    dialog._select_tool("slot")
    with patch.object(SlotSpecificationDialog, "exec", return_value=SlotSpecificationDialog.DialogCode.Accepted), patch.object(
        SlotSpecificationDialog, "values", return_value=(slot_points[0], slot_points[1], 50.0)
    ):
        for point in slot_points:
            _click_model(dialog, point)
    slots = [entity for entity in dialog.document.active_sketch.entities.values() if isinstance(entity, SlotEntity)]
    assert len(slots) == 1 and slots[0].width == 50
    assert not dialog.canvas._multi_points and dialog.canvas.tool == "slot"
    assert len(dialog.command_stack.undo_commands) == 4
    for _ in range(4):
        assert dialog.command_stack.undo()
    assert not dialog.document.active_sketch.entities
    for _ in range(4):
        assert dialog.command_stack.redo()
    assert len(dialog.document.active_sketch.entities) == 4
    dialog.close()
    print("[OK] real Qt gestures create point, center rectangle, polygon and slot atomically")


def test_dxf_point_and_slot_export_keep_exact_primitive_curves() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "point.dxf"
        export_path = Path(directory) / "shapes.dxf"
        source = ezdxf.new("R2010")
        source.layers.add("FEATURES")
        source.modelspace().add_point((12, 34), dxfattribs={"layer": "FEATURES"})
        source.saveas(source_path)
        dialog = TechnicalEditorDialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source_path), "DXF")):
            dialog._import_dxf()
        imported = next(iter(dialog.document.active_sketch.entities.values()))
        assert isinstance(imported, PointEntity) and imported.layer == "FEATURES"
        slot = SlotEntity(Point2D(0, 0), Point2D(40, 0), 5, layer="FEATURES")
        dialog.document.active_sketch._add_entity(slot)
        polygon = RegularPolygonEntity(Point2D(80, 20), 12, 5, 18, layer="FEATURES")
        dialog.document.active_sketch._add_entity(polygon)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(export_path), "DXF")):
            dialog._export_dxf()
        reopened = ezdxf.readfile(export_path)
        assert len(list(reopened.modelspace().query("POINT"))) == 1
        assert len(list(reopened.modelspace().query("LINE"))) == 2
        arcs = list(reopened.modelspace().query("ARC"))
        assert len(arcs) == 2 and all(abs(float(arc.dxf.radius) - 5) < 1e-9 for arc in arcs)
        polygons = list(reopened.modelspace().query("LWPOLYLINE"))
        assert len(polygons) == 1 and polygons[0].closed and len(list(polygons[0].get_points("xy"))) == 5
        dialog.close()
    print("[OK] DXF keeps POINT and exact LINE/ARC slot boundary primitives")


if __name__ == "__main__":
    test_native_point_center_rectangle_polygon_and_slot_round_trip()
    test_slot_solver_snapping_intersections_and_profile()
    test_regular_polygon_solver_preserves_semantics_and_foremness()
    test_point_and_slot_auto_constraints_are_real_solver_constraints()
    test_workspace_tools_commit_precise_shapes_and_keep_tool_active()
    test_dxf_point_and_slot_export_keep_exact_primitive_curves()
    print("CAD SHAPES TESTS OK")

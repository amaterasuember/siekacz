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
from PySide6.QtWidgets import QApplication, QFileDialog, QGraphicsPathItem

from app.technical_editor import (
    EllipseSpecificationDialog,
    EllipticalArcSpecificationDialog,
    TechnicalEditorDialog,
)
from cad.auto_constraints import build_ellipse_auto_constraints, build_elliptical_arc_auto_constraints
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.model import (
    CadDocument,
    CircleEntity,
    EllipseEntity,
    EllipticalArcEntity,
    LineEntity,
    Point2D,
    PointEntity,
    ellipse_from_three_points,
    elliptical_arc_from_five_points,
)
from cad.profiles import analyze_profile
from cad.snapping import SnapCandidate, SnapEngine, SnapKind
from cad.solver import fixed_constraint, solve_and_apply


def test_analytic_ellipse_and_arc_geometry_round_trip() -> None:
    ellipse = EllipseEntity(Point2D(5, -3), 30, 12, 27, layer="OVALS")
    assert math.isclose(ellipse.area, math.pi * 30 * 12)
    assert ellipse.major_positive.distance_to(ellipse.center) == 30
    assert ellipse.minor_positive.distance_to(ellipse.center) == 12
    left, bottom, right, top = ellipse.bounds()
    samples = [ellipse.point_at_parameter(index * 0.5) for index in range(720)]
    assert all(left - 1e-9 <= point.x <= right + 1e-9 and bottom - 1e-9 <= point.y <= top + 1e-9 for point in samples)
    nearest = ellipse.nearest_point(Point2D(50, 20))
    parameter = math.radians(ellipse.parameter_for_point(nearest))
    assert abs((30 * math.cos(parameter) / 30) ** 2 + (12 * math.sin(parameter) / 12) ** 2 - 1) < 1e-9

    constructed = ellipse_from_three_points(Point2D(0, 0), Point2D(20, 0), Point2D(3, 8))
    assert constructed.major_radius == 20 and constructed.minor_radius == 8
    arc = elliptical_arc_from_five_points(
        Point2D(0, 0), Point2D(20, 0), Point2D(0, 10), Point2D(20, 0), Point2D(-20, 0)
    )
    assert arc.start.distance_to(Point2D(20, 0)) < 1e-9
    assert arc.end.distance_to(Point2D(-20, 0)) < 1e-9
    assert abs(arc.sweep_parameter_deg - 180) < 1e-9 and arc.length > 40

    document = CadDocument.create()
    document.active_sketch._add_entity(ellipse)
    document.active_sketch._add_entity(arc)
    restored = CadDocument.from_dict(document.to_dict()).active_sketch
    assert restored.entities[ellipse.id] == ellipse and isinstance(restored.entities[ellipse.id], EllipseEntity)
    assert restored.entities[arc.id] == arc and isinstance(restored.entities[arc.id], EllipticalArcEntity)
    print("[OK] analytic ellipse and elliptical arc geometry survive native serialization")


def test_ellipse_solver_dof_point_on_object_and_arc_length() -> None:
    document = CadDocument.create()
    ellipse = EllipseEntity(Point2D(3, 4), 20, 8, 12)
    document.active_sketch._add_entity(ellipse)
    for constraint in (
        SketchConstraint(ConstraintType.X_COORDINATE, (GeometryReference(ellipse.id, "center"),), value=0),
        SketchConstraint(ConstraintType.Y_COORDINATE, (GeometryReference(ellipse.id, "center"),), value=0),
        SketchConstraint(ConstraintType.RADIUS, (GeometryReference(ellipse.id),), value=25),
        SketchConstraint(ConstraintType.RADIUS, (GeometryReference(ellipse.id, "minor-radius"),), value=10),
        SketchConstraint(ConstraintType.ANGLE, (GeometryReference(ellipse.id, "major-axis"),), value=30),
    ):
        document.active_sketch._add_constraint(constraint)
    result = solve_and_apply(document.active_sketch)
    solved = document.active_sketch.entities[ellipse.id]
    assert result.solved and result.degrees_of_freedom == 0
    assert isinstance(solved, EllipseEntity)
    assert solved.center.distance_to(Point2D(0, 0)) < 1e-7
    assert abs(solved.major_radius - 25) < 1e-7 and abs(solved.minor_radius - 10) < 1e-7
    assert abs(((solved.rotation_deg - 30 + 180) % 360) - 180) < 1e-5

    point_document = CadDocument.create()
    target = EllipseEntity(Point2D(0, 0), 20, 10, 0)
    point = PointEntity(Point2D(0, 12))
    point_document.active_sketch._add_entity(target)
    point_document.active_sketch._add_entity(point)
    point_document.active_sketch._add_constraint(fixed_constraint(point_document.active_sketch, target.id))
    point_document.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.POINT_ON_OBJECT,
        (GeometryReference(point.id, "point"), GeometryReference(target.id)),
    ))
    point_result = solve_and_apply(point_document.active_sketch)
    solved_point = point_document.active_sketch.entities[point.id]
    assert point_result.solved and isinstance(solved_point, PointEntity)
    assert abs((solved_point.point.x / 20) ** 2 + (solved_point.point.y / 10) ** 2 - 1) < 1e-7

    arc_document = CadDocument.create()
    arc = EllipticalArcEntity(Point2D(0, 0), 20, 10, 0, 0, 120)
    arc_document.active_sketch._add_entity(arc)
    arc_document.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.ARC_LENGTH,
        (GeometryReference(arc.id),),
        value=arc.length,
        driving=False,
    ))
    arc_result = solve_and_apply(arc_document.active_sketch)
    assert arc_result.measured_values and abs(arc_result.measured_values[next(iter(arc_document.active_sketch.constraints))] - arc.length) < 1e-7
    print("[OK] ellipse has real solver parameters, DoF, point-on-object and arc-length measurement")


def test_ellipse_snapping_intersections_profiles_and_auto_constraints() -> None:
    document = CadDocument.create()
    ellipse = EllipseEntity(Point2D(0, 0), 20, 10, 0)
    crossing = LineEntity(Point2D(-30, 0), Point2D(30, 0))
    document.active_sketch._add_entity(ellipse)
    document.active_sketch._add_entity(crossing)
    engine = SnapEngine(radius_pixels=12)
    intersection = engine.query(document.active_sketch, Point2D(20.1, 0), pixels_per_unit=5)
    assert any(candidate.kind == SnapKind.INTERSECTION and candidate.point.distance_to(Point2D(20, 0)) < 1e-8 for candidate in intersection.candidates)
    nearest = engine.query(document.active_sketch, Point2D(0, 10.2), pixels_per_unit=5)
    assert any(candidate.kind in {SnapKind.ENDPOINT, SnapKind.NEAREST} and candidate.entity_id == ellipse.id for candidate in nearest.candidates)

    curved = CadDocument.create()
    first_ellipse = EllipseEntity(Point2D(0, 0), 20, 10, 0)
    second_ellipse = EllipseEntity(Point2D(0, 0), 20, 10, 90)
    circle = CircleEntity(Point2D(0, 0), 15)
    curved.active_sketch._add_entity(first_ellipse)
    curved.active_sketch._add_entity(second_ellipse)
    curved.active_sketch._add_entity(circle)
    ellipse_crossing = engine.query(curved.active_sketch, Point2D(8.95, 8.95), pixels_per_unit=5)
    assert any(candidate.kind == SnapKind.INTERSECTION for candidate in ellipse_crossing.candidates)
    circle_crossing = engine.query(curved.active_sketch, Point2D(12.91, 7.64), pixels_per_unit=5)
    assert any(candidate.kind == SnapKind.INTERSECTION for candidate in circle_crossing.candidates)

    closed = CadDocument.create()
    closed.active_sketch._add_entity(ellipse)
    report = analyze_profile(closed.active_sketch)
    assert report.is_valid_surface and len(report.loops) == 1 and not report.issues
    arc = EllipticalArcEntity(Point2D(0, 0), 20, 10, 0, 0, 180)
    chord_document = CadDocument.create()
    chord_document.active_sketch._add_entity(arc)
    chord_document.active_sketch._add_entity(LineEntity(arc.end, arc.start))
    chord_report = analyze_profile(chord_document.active_sketch)
    assert chord_report.is_valid_surface and len(chord_report.loops) == 1

    target = LineEntity(Point2D(-10, 0), Point2D(0, 0))
    endpoint = SnapCandidate(Point2D(0, 0), SnapKind.ENDPOINT, 0, target.id, "end")
    major = SnapCandidate(Point2D(20, 0), SnapKind.GRID, 0)
    minor = SnapCandidate(Point2D(0, 10), SnapKind.GRID, 0)
    proposal = build_ellipse_auto_constraints(ellipse, (endpoint, major, minor))
    assert any(constraint.constraint_type == ConstraintType.COINCIDENT for constraint in proposal.constraints)
    arc_candidates = (None, None, None, major, SnapCandidate(Point2D(-20, 0), SnapKind.GRID, 0))
    assert isinstance(build_elliptical_arc_auto_constraints(arc, arc_candidates).constraints, tuple)
    print("[OK] ellipse snapping, analytic line intersections, profiles and auto-constraints work")


def _click_model(dialog: TechnicalEditorDialog, point: Point2D) -> None:
    viewport = dialog.canvas.mapFromScene(dialog.canvas.model_to_scene(point))
    QTest.mouseClick(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=viewport)


def test_workspace_multistage_ellipse_and_arc_are_atomic() -> None:
    QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    dialog.show()
    QApplication.processEvents()
    dialog.canvas.fit_board()
    QApplication.processEvents()

    ellipse_points = (Point2D(200, 300), Point2D(300, 300), Point2D(200, 350))
    dialog._select_tool("ellipse")
    with patch.object(EllipseSpecificationDialog, "exec", return_value=EllipseSpecificationDialog.DialogCode.Accepted), patch.object(
        EllipseSpecificationDialog, "values", return_value=(ellipse_points[0], 100.0, 50.0, 0.0)
    ):
        for point in ellipse_points:
            _click_model(dialog, point)
    ellipses = [entity for entity in dialog.document.active_sketch.entities.values() if isinstance(entity, EllipseEntity)]
    assert len(ellipses) == 1 and isinstance(dialog.canvas._entity_items[ellipses[0].id], QGraphicsPathItem)
    assert dialog.canvas.tool == "ellipse" and not dialog.canvas._multi_points

    arc_points = (
        Point2D(500, 500), Point2D(600, 500), Point2D(500, 550), Point2D(600, 500), Point2D(400, 500)
    )
    dialog._select_tool("ellipse_arc")
    with patch.object(EllipticalArcSpecificationDialog, "exec", return_value=EllipticalArcSpecificationDialog.DialogCode.Accepted), patch.object(
        EllipticalArcSpecificationDialog,
        "values",
        return_value=(arc_points[0], 100.0, 50.0, 0.0, 0.0, 180.0),
    ):
        for point in arc_points:
            _click_model(dialog, point)
    arcs = [entity for entity in dialog.document.active_sketch.entities.values() if isinstance(entity, EllipticalArcEntity)]
    assert len(arcs) == 1 and isinstance(dialog.canvas._entity_items[arcs[0].id], QGraphicsPathItem)
    assert dialog.canvas.tool == "ellipse_arc" and not dialog.canvas._multi_points
    assert len(dialog.command_stack.undo_commands) == 2
    assert dialog.command_stack.undo() and not any(isinstance(entity, EllipticalArcEntity) for entity in dialog.document.active_sketch.entities.values())
    assert dialog.command_stack.undo() and not dialog.document.active_sketch.entities
    assert dialog.command_stack.redo() and dialog.command_stack.redo()
    dialog.close()
    print("[OK] real Qt multistage gestures commit ellipse and elliptical arc atomically")


def test_dxf_ellipse_round_trip_preserves_native_type_layer_and_parameters() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "ellipses-source.dxf"
        export_path = Path(directory) / "ellipses-export.dxf"
        source = ezdxf.new("R2010")
        source.layers.add("OVALS")
        modelspace = source.modelspace()
        rotation = math.radians(30)
        major_axis = (40 * math.cos(rotation), 40 * math.sin(rotation))
        modelspace.add_ellipse((10, 20), major_axis, ratio=0.4, dxfattribs={"layer": "OVALS"})
        modelspace.add_ellipse((100, 50), (30, 0), ratio=0.5, start_param=0.2, end_param=2.4, dxfattribs={"layer": "OVALS"})
        source.saveas(source_path)
        dialog = TechnicalEditorDialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source_path), "DXF")):
            dialog._import_dxf()
        imported = list(dialog.document.active_sketch.entities.values())
        assert len(imported) == 2
        assert isinstance(imported[0], EllipseEntity) and isinstance(imported[1], EllipticalArcEntity)
        assert imported[0].layer == imported[1].layer == "OVALS"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(export_path), "DXF")):
            dialog._export_dxf()
        reopened = ezdxf.readfile(export_path)
        entities = list(reopened.modelspace().query("ELLIPSE"))
        assert len(entities) == 2 and all(entity.dxf.layer == "OVALS" for entity in entities)
        assert abs(float(entities[0].dxf.ratio) - 0.4) < 1e-9
        assert abs(float(entities[1].dxf.start_param) - 0.2) < 1e-9
        assert abs(float(entities[1].dxf.end_param) - 2.4) < 1e-9
        assert not list(reopened.modelspace().query("LWPOLYLINE"))
        dialog.close()
    print("[OK] DXF ELLIPSE round-trip preserves native type, layer, axes and arc parameters")


if __name__ == "__main__":
    test_analytic_ellipse_and_arc_geometry_round_trip()
    test_ellipse_solver_dof_point_on_object_and_arc_length()
    test_ellipse_snapping_intersections_profiles_and_auto_constraints()
    test_workspace_multistage_ellipse_and_arc_are_atomic()
    test_dxf_ellipse_round_trip_preserves_native_type_layer_and_parameters()
    print("CAD ELLIPSE TESTS OK")

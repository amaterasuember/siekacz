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
from PySide6.QtWidgets import QApplication, QFileDialog, QGraphicsPathItem, QMessageBox

from app.technical_editor import ArcSpecificationDialog, PolylineSpecificationDialog, TechnicalEditorDialog
from cad.auto_constraints import build_polyline_auto_constraints
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.model import ArcEntity, CadDocument, CadValidationError, LineEntity, Point2D, PolylineEntity, arc_from_three_points
from cad.profiles import ProfileIssueType, analyze_profile
from cad.snapping import SnapCandidate, SnapEngine, SnapKind
from cad.solver import solve_and_apply


def test_polyline_domain_serialization_and_three_point_arc() -> None:
    polyline = PolylineEntity((Point2D(0, 0), Point2D(30, 0), Point2D(30, 40)), layer="PATH")
    assert polyline.length == 70
    assert polyline.bounds() == (0, 0, 30, 40)
    document = CadDocument.create()
    document.active_sketch._add_entity(polyline)
    restored = CadDocument.from_dict(document.to_dict()).active_sketch.entities[polyline.id]
    assert restored == polyline and isinstance(restored, PolylineEntity)

    start, through, end = Point2D(-10, 0), Point2D(0, 10), Point2D(10, 0)
    arc = arc_from_three_points(start, through, end)
    assert isinstance(arc, ArcEntity)
    assert arc.start.distance_to(start) < 1e-8 and arc.end.distance_to(end) < 1e-8
    assert arc.midpoint.distance_to(through) < 1e-8
    try:
        arc_from_three_points(Point2D(0, 0), Point2D(1, 0), Point2D(2, 0))
    except CadValidationError as exc:
        assert "współliniowe" in str(exc)
    else:
        raise AssertionError("collinear three-point arc must fail")
    print("[OK] polyline is native/serializable and three points define an analytic arc")


def test_polyline_solver_snapping_and_profile_diagnostics() -> None:
    document = CadDocument.create()
    polyline = PolylineEntity((Point2D(0, 0), Point2D(20, 1), Point2D(20, 20)))
    crossing = LineEntity(Point2D(10, -10), Point2D(10, 10))
    document.active_sketch._add_entity(polyline)
    document.active_sketch._add_entity(crossing)
    document.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.HORIZONTAL,
        (GeometryReference(polyline.id, "segment-0"),),
    ))
    solve_and_apply(document.active_sketch)
    solved = document.active_sketch.entities[polyline.id]
    assert isinstance(solved, PolylineEntity)
    assert abs(solved.points[0].y - solved.points[1].y) < 1e-7

    engine = SnapEngine(radius_pixels=15)
    endpoint = engine.query(document.active_sketch, Point2D(20.2, 20.1), pixels_per_unit=5)
    assert endpoint.active is not None and endpoint.active.kind == SnapKind.ENDPOINT
    midpoint = engine.query(document.active_sketch, Point2D(10, 0.2), pixels_per_unit=5)
    assert any(candidate.kind in {SnapKind.MIDPOINT, SnapKind.INTERSECTION} for candidate in midpoint.candidates)
    assert any(candidate.kind == SnapKind.INTERSECTION for candidate in midpoint.candidates)

    closed = CadDocument.create()
    square = PolylineEntity((Point2D(0, 0), Point2D(20, 0), Point2D(20, 20), Point2D(0, 20)), closed=True)
    closed.active_sketch._add_entity(square)
    report = analyze_profile(closed.active_sketch)
    assert report.is_valid_surface and len(report.loops) == 1
    bow = PolylineEntity((Point2D(0, 0), Point2D(20, 20), Point2D(0, 20), Point2D(20, 0)), closed=True)
    crossed = CadDocument.create()
    crossed.active_sketch._add_entity(bow)
    assert any(issue.issue_type == ProfileIssueType.SELF_INTERSECTION for issue in analyze_profile(crossed.active_sketch).issues)
    print("[OK] polyline participates in solver, snapping, intersections and profile diagnostics")


def test_polyline_auto_constraints_cover_vertices_and_segments() -> None:
    document = CadDocument.create()
    target = LineEntity(Point2D(-20, 0), Point2D(0, 0))
    document.active_sketch._add_entity(target)
    polyline = PolylineEntity((Point2D(0, 0), Point2D(30, 0), Point2D(30, 20)))
    candidates = (
        SnapCandidate(Point2D(0, 0), SnapKind.ENDPOINT, 0, target.id, "end"),
        SnapCandidate(Point2D(30, 0), SnapKind.HORIZONTAL, 0, suggested_constraint="horizontal"),
        SnapCandidate(Point2D(30, 20), SnapKind.VERTICAL, 0, suggested_constraint="vertical"),
    )
    proposal = build_polyline_auto_constraints(document.active_sketch, polyline, candidates)
    assert {constraint.constraint_type for constraint in proposal.constraints} == {
        ConstraintType.COINCIDENT,
        ConstraintType.HORIZONTAL,
        ConstraintType.VERTICAL,
    }
    assert {constraint.references[0].element for constraint in proposal.constraints} >= {"vertex-0", "segment-0", "segment-1"}
    print("[OK] polyline vertices and individual segments receive real auto-constraints")


def test_workspace_multistage_polyline_and_three_point_arc_are_atomic() -> None:
    QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    dialog.show()
    QApplication.processEvents()
    dialog.canvas.fit_board()
    QApplication.processEvents()
    points = (Point2D(100, 100), Point2D(200, 100), Point2D(200, 200))
    dialog._select_tool("polyline")
    with patch.object(PolylineSpecificationDialog, "exec", return_value=PolylineSpecificationDialog.DialogCode.Accepted), patch.object(
        PolylineSpecificationDialog, "values", return_value=(points, False, (0.0, 0.0))
    ):
        for point in points:
            viewport_point = dialog.canvas.mapFromScene(dialog.canvas.model_to_scene(point))
            QTest.mouseClick(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=viewport_point)
        assert len(dialog.canvas._multi_points) == 3
        assert not dialog.document.active_sketch.entities
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Return)
    polyline = next(iter(dialog.document.active_sketch.entities.values()))
    assert isinstance(polyline, PolylineEntity)
    assert isinstance(dialog.canvas._entity_items[polyline.id], QGraphicsPathItem)
    assert len(dialog.command_stack.undo_commands) == 1
    assert dialog.command_stack.undo() and not dialog.document.active_sketch.entities
    assert dialog.command_stack.redo() and polyline.id in dialog.document.active_sketch.entities
    assert dialog.canvas.tool == "polyline"

    arc_points = (Point2D(100, 500), Point2D(150, 900), Point2D(200, 500))
    expected = arc_from_three_points(*arc_points)
    dialog._select_tool("arc3")
    captured: list[tuple[Point2D, ...]] = []
    dialog.canvas.multi_creation_requested.disconnect(dialog._create_multi_geometry)
    dialog.canvas.multi_creation_requested.connect(lambda _tool, actual, _candidates, _closed: captured.append(actual))
    dialog.canvas.multi_creation_requested.connect(dialog._create_multi_geometry)
    with patch.object(ArcSpecificationDialog, "exec", return_value=ArcSpecificationDialog.DialogCode.Accepted), patch.object(
        ArcSpecificationDialog,
        "values",
        return_value=(expected.center, expected.radius, expected.start_angle_deg, expected.sweep_angle_deg),
    ), patch.object(QMessageBox, "warning", side_effect=lambda _parent, title, message: (_ for _ in ()).throw(AssertionError(f"{title}: {message}; captured={captured}"))):
        for point in arc_points:
            viewport_point = dialog.canvas.mapFromScene(dialog.canvas.model_to_scene(point))
            QTest.mouseClick(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=viewport_point)
    assert captured, "three canvas clicks must finish the arc3 gesture"
    assert any(isinstance(entity, ArcEntity) for entity in dialog.document.active_sketch.entities.values())
    assert not dialog.canvas._multi_points and dialog.canvas.tool == "arc3"
    dialog.close()
    print("[OK] workspace commits multistage polyline and three-point arc atomically")


def test_dxf_lwpolyline_round_trip_preserves_structure_and_layer() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "polyline-source.dxf"
        export_path = Path(directory) / "polyline-export.dxf"
        source = ezdxf.new("R2010")
        source.layers.add("ROUTE")
        source.modelspace().add_lwpolyline([(0, 0), (25, 0), (25, 15)], close=False, dxfattribs={"layer": "ROUTE"})
        source.saveas(source_path)
        dialog = TechnicalEditorDialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source_path), "DXF")):
            dialog._import_dxf()
        imported = next(iter(dialog.document.active_sketch.entities.values()))
        assert isinstance(imported, PolylineEntity) and imported.layer == "ROUTE"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(export_path), "DXF")):
            dialog._export_dxf()
        reopened = ezdxf.readfile(export_path)
        polylines = list(reopened.modelspace().query("LWPOLYLINE"))
        assert len(polylines) == 1 and polylines[0].dxf.layer == "ROUTE"
        assert len(list(polylines[0].get_points("xy"))) == 3
        assert not list(reopened.modelspace().query("LINE"))
        dialog.close()
    print("[OK] DXF LWPOLYLINE round-trip preserves one structured polyline and its layer")


if __name__ == "__main__":
    test_polyline_domain_serialization_and_three_point_arc()
    test_polyline_solver_snapping_and_profile_diagnostics()
    test_polyline_auto_constraints_cover_vertices_and_segments()
    test_workspace_multistage_polyline_and_three_point_arc_are_atomic()
    test_dxf_lwpolyline_round_trip_preserves_structure_and_layer()
    print("CAD POLYLINE TESTS OK")

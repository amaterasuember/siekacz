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

from app.technical_editor import BSplineSpecificationDialog, TechnicalEditorDialog
from cad.auto_constraints import build_bspline_auto_constraints
from cad.constraints import ConstraintType
from cad.model import BSplineEntity, CadDocument, LineEntity, Point2D
from cad.profiles import analyze_profile
from cad.snapping import SnapCandidate, SnapEngine, SnapKind
from cad.solver import fixed_constraint, solve_and_apply, solve_sketch


def test_bspline_exact_rational_geometry_and_native_round_trip() -> None:
    curve = BSplineEntity(
        (Point2D(1, 0), Point2D(1, 1), Point2D(0, 1)),
        degree=2,
        knots=(0, 0, 0, 1, 1, 1),
        weights=(1, math.sqrt(0.5), 1),
        layer="CURVES",
    )
    midpoint = curve.point_at(0.5)
    expected = math.sqrt(0.5)
    assert abs(midpoint.x - expected) < 1e-12 and abs(midpoint.y - expected) < 1e-12
    assert curve.rational and curve.start == Point2D(1, 0) and curve.end == Point2D(0, 1)
    assert abs(curve.nearest_point(Point2D(0.72, 0.72)).distance_to(Point2D(expected, expected))) < 0.02
    document = CadDocument.create()
    document.active_sketch._add_entity(curve)
    restored = CadDocument.from_dict(document.to_dict()).active_sketch.entities[curve.id]
    assert isinstance(restored, BSplineEntity) and restored == curve
    print("[OK] rational B-spline uses exact de Boor evaluation and survives .siekcad serialization")


def test_bspline_solver_references_snapping_profiles_and_auto_constraints() -> None:
    document = CadDocument.create()
    curve = BSplineEntity((Point2D(0, 0), Point2D(10, 20), Point2D(20, 0)), degree=2)
    crossing = LineEntity(Point2D(-5, 5), Point2D(25, 5))
    document.active_sketch._add_entity(curve)
    document.active_sketch._add_entity(crossing)
    assert solve_sketch(document.active_sketch).degrees_of_freedom == 10

    fixed_document = CadDocument.create()
    fixed_document.active_sketch._add_entity(curve)
    fixed_document.active_sketch._add_constraint(fixed_constraint(fixed_document.active_sketch, curve.id))
    fixed_result = solve_and_apply(fixed_document.active_sketch)
    assert fixed_result.solved and fixed_result.degrees_of_freedom == 0

    engine = SnapEngine(radius_pixels=20)
    result = engine.query(document.active_sketch, Point2D(2.93, 5), pixels_per_unit=5)
    intersections = [candidate for candidate in result.candidates if candidate.kind == SnapKind.INTERSECTION]
    assert intersections
    assert min(candidate.point.distance_to(Point2D(2.928932188, 5)) for candidate in intersections) < 1e-7
    nearest = engine.query(document.active_sketch, Point2D(10, 10.2), pixels_per_unit=5)
    assert any(candidate.kind in {SnapKind.MIDPOINT, SnapKind.NEAREST} and candidate.entity_id == curve.id for candidate in nearest.candidates)

    closed_document = CadDocument.create()
    closed = BSplineEntity(
        (Point2D(0, 0), Point2D(20, 0), Point2D(20, 20), Point2D(0, 0)),
        degree=3,
        closed=True,
    )
    closed_document.active_sketch._add_entity(closed)
    report = analyze_profile(closed_document.active_sketch)
    assert report.is_valid_surface and len(report.loops) == 1

    endpoint = SnapCandidate(Point2D(0, 0), SnapKind.ENDPOINT, 0, crossing.id, "start")
    proposal = build_bspline_auto_constraints(curve, (endpoint, None, None))
    assert any(constraint.constraint_type == ConstraintType.COINCIDENT for constraint in proposal.constraints)
    print("[OK] B-spline participates in solver DoF, stable references, snapping and profile diagnostics")


def _click_model(dialog: TechnicalEditorDialog, point: Point2D) -> None:
    viewport = dialog.canvas.mapFromScene(dialog.canvas.model_to_scene(point))
    QTest.mouseClick(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=viewport)


def test_workspace_multistage_bspline_is_atomic_and_editable() -> None:
    QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    dialog.show()
    QApplication.processEvents()
    dialog.canvas.fit_board()
    QApplication.processEvents()
    points = (Point2D(100, 100), Point2D(180, 240), Point2D(300, 100), Point2D(380, 180))
    dialog._select_tool("bspline")
    with patch.object(BSplineSpecificationDialog, "exec", return_value=BSplineSpecificationDialog.DialogCode.Accepted), patch.object(
        BSplineSpecificationDialog, "values", return_value=(points, 3)
    ):
        for point in points:
            _click_model(dialog, point)
        assert len(dialog.canvas._multi_points) == 4
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Return)
    curves = [entity for entity in dialog.document.active_sketch.entities.values() if isinstance(entity, BSplineEntity)]
    assert len(curves) == 1 and isinstance(dialog.canvas._entity_items[curves[0].id], QGraphicsPathItem)
    assert dialog.canvas.tool == "bspline" and not dialog.canvas._multi_points
    assert len(dialog.command_stack.undo_commands) == 1
    assert dialog.command_stack.undo() and not dialog.document.active_sketch.entities
    assert dialog.command_stack.redo() and curves[0].id in dialog.document.active_sketch.entities
    dialog.close()
    print("[OK] real Qt multi-click B-spline gesture commits as one undo transaction")


def test_dxf_spline_round_trip_preserves_native_definition_layer_and_fit_points() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "splines-source.dxf"
        export_path = Path(directory) / "splines-export.dxf"
        source = ezdxf.new("R2010")
        source.layers.add("CURVES")
        modelspace = source.modelspace()
        modelspace.add_rational_spline(
            [(0, 0), (20, 30), (40, 0)],
            weights=(1, 0.75, 1),
            degree=2,
            knots=(0, 0, 0, 1, 1, 1),
            dxfattribs={"layer": "CURVES"},
        )
        modelspace.add_spline([(60, 0), (80, 25), (100, 0)], degree=2, dxfattribs={"layer": "CURVES"})
        source.saveas(source_path)

        dialog = TechnicalEditorDialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source_path), "DXF")):
            dialog._import_dxf()
        imported = list(dialog.document.active_sketch.entities.values())
        assert len(imported) == 2 and all(isinstance(entity, BSplineEntity) for entity in imported)
        assert all(entity.layer == "CURVES" for entity in imported)
        assert imported[0].rational and len(imported[1].control_points) >= 3

        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(export_path), "DXF")):
            dialog._export_dxf()
        reopened = ezdxf.readfile(export_path)
        splines = list(reopened.modelspace().query("SPLINE"))
        assert len(splines) == 2 and all(spline.dxf.layer == "CURVES" for spline in splines)
        assert all(len(spline.control_points) >= 3 for spline in splines)
        assert len(splines[0].weights) == 3
        assert not list(reopened.modelspace().query("LWPOLYLINE"))
        dialog.close()
    print("[OK] DXF SPLINE round-trip preserves native curve type, layer, knots, weights and fit-point curves")


if __name__ == "__main__":
    test_bspline_exact_rational_geometry_and_native_round_trip()
    test_bspline_solver_references_snapping_profiles_and_auto_constraints()
    test_workspace_multistage_bspline_is_atomic_and_editable()
    test_dxf_spline_round_trip_preserves_native_definition_layer_and_fit_points()
    print("CAD B-SPLINE TESTS OK")

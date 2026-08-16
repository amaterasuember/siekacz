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
from PySide6.QtWidgets import QApplication, QFileDialog, QGraphicsPathItem

from app.technical_editor import PolylineSpecificationDialog, TechnicalEditorDialog
from cad.commands import AddEntityCommand
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint, SketchSolveStatus
from cad.model import ArcEntity, CadDocument, LineEntity, Point2D, PointEntity, PolylineEntity, arc_from_bulge
from cad.profiles import analyze_profile
from cad.snapping import SnapEngine, SnapKind
from cad.solver import fixed_constraint, solve_and_apply


def test_bulge_is_an_exact_arc_with_stable_segment_identity_and_serialization() -> None:
    positive = arc_from_bulge(Point2D(0, 0), Point2D(10, 0), 1.0)
    negative = arc_from_bulge(Point2D(0, 0), Point2D(10, 0), -1.0)
    major = arc_from_bulge(Point2D(0, 0), Point2D(10, 0), 2.0)
    assert positive.center == negative.center == Point2D(5, 0)
    assert positive.sweep_angle_deg == 180 and negative.sweep_angle_deg == -180
    assert positive.midpoint.distance_to(Point2D(5, -5)) < 1e-9
    assert negative.midpoint.distance_to(Point2D(5, 5)) < 1e-9
    assert major.sweep_angle_deg > 180 and major.start.distance_to(Point2D(0, 0)) < 1e-9
    assert major.end.distance_to(Point2D(10, 0)) < 1e-9

    polyline = PolylineEntity(
        (Point2D(0, 0), Point2D(10, 0), Point2D(10, 10)),
        False,
        (1.0, 0.0),
        layer="MIXED",
    )
    first_segments = polyline.segment_entities
    second_segments = polyline.segment_entities
    assert isinstance(first_segments[0], ArcEntity) and isinstance(first_segments[1], LineEntity)
    assert [segment.id for segment in first_segments] == [segment.id for segment in second_segments]
    assert math.isclose(polyline.length, math.pi * 5 + 10, rel_tol=1e-12)
    assert polyline.bounds() == (0.0, -5.0, 10.0, 10.0)

    document = CadDocument.create()
    document.active_sketch._add_entity(polyline)
    restored = CadDocument.from_dict(document.to_dict()).active_sketch.entities[polyline.id]
    assert isinstance(restored, PolylineEntity) and restored == polyline
    print("[OK] DXF bulge maps to an exact stable circular segment and survives .siekcad")


def test_bulged_polyline_solver_snapping_and_profile_use_the_arc_not_chord() -> None:
    document = CadDocument.create()
    polyline = PolylineEntity((Point2D(0, 0), Point2D(10, 0)), False, (1.0,))
    vertical = LineEntity(Point2D(5, -10), Point2D(5, 2))
    document.active_sketch._add_entity(polyline)
    document.active_sketch._add_entity(vertical)
    engine = SnapEngine(radius_pixels=20)

    midpoint = engine.query(document.active_sketch, Point2D(5, -5.1), pixels_per_unit=5)
    assert any(
        candidate.kind == SnapKind.MIDPOINT
        and candidate.entity_id == polyline.id
        and candidate.point.distance_to(Point2D(5, -5)) < 1e-9
        for candidate in midpoint.candidates
    )
    intersections = engine.query(document.active_sketch, Point2D(5, -5.1), pixels_per_unit=5)
    assert any(
        candidate.kind == SnapKind.INTERSECTION and candidate.point.distance_to(Point2D(5, -5)) < 1e-8
        for candidate in intersections.candidates
    )
    nearest = engine.query(document.active_sketch, Point2D(5, -5.2), pixels_per_unit=5)
    assert any(candidate.kind in {SnapKind.MIDPOINT, SnapKind.NEAREST} and candidate.entity_id == polyline.id for candidate in nearest.candidates)

    constrained = CadDocument.create()
    target = PolylineEntity((Point2D(0, 0), Point2D(10, 0)), False, (1.0,))
    point = PointEntity(Point2D(5, -6))
    constrained.active_sketch._add_entity(target)
    constrained.active_sketch._add_entity(point)
    constrained.active_sketch._add_constraint(fixed_constraint(constrained.active_sketch, target.id))
    constrained.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.POINT_ON_OBJECT,
        (GeometryReference(point.id, "point"), GeometryReference(target.id)),
    ))
    result = solve_and_apply(constrained.active_sketch)
    solved_point = constrained.active_sketch.entities[point.id]
    assert result.solved and isinstance(solved_point, PointEntity)
    assert solved_point.point.distance_to(target.segment_entities[0].nearest_point(solved_point.point)) < 1e-7
    assert solved_point.point.y < -4.9  # the chord is y=0 and must not satisfy this constraint

    invalid_direction = CadDocument.create()
    invalid_direction.active_sketch._add_entity(target)
    invalid_direction.active_sketch._add_constraint(SketchConstraint(
        ConstraintType.HORIZONTAL,
        (GeometryReference(target.id, "segment-0"),),
    ))
    direction_result = solve_and_apply(invalid_direction.active_sketch)
    assert direction_result.status == SketchSolveStatus.UNSOLVABLE
    assert direction_result.broken_reference_ids

    closed = CadDocument.create()
    mixed_loop = PolylineEntity(
        (Point2D(0, 0), Point2D(10, 0), Point2D(10, 10)),
        True,
        (1.0, 0.0, 0.0),
    )
    closed.active_sketch._add_entity(mixed_loop)
    report = analyze_profile(closed.active_sketch)
    assert report.is_valid_surface and len(report.loops) == 1 and not report.error_count
    print("[OK] solver, snapping and profile diagnostics use analytic bulge arcs instead of chords")


def test_workspace_renders_and_edits_bulges_without_losing_definition() -> None:
    QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    polyline = PolylineEntity(
        (Point2D(100, 300), Point2D(300, 300), Point2D(300, 500)),
        False,
        (1.0, 0.0),
    )
    dialog.command_stack.execute(AddEntityCommand(
        dialog.document.active_sketch_id,
        polyline,
    ))
    item = dialog.canvas._entity_items[polyline.id]
    assert isinstance(item, QGraphicsPathItem)
    assert item.path().elementCount() > len(polyline.points)
    dialog.selection_model.set((polyline.id,))
    with patch.object(PolylineSpecificationDialog, "exec", return_value=PolylineSpecificationDialog.DialogCode.Accepted), patch.object(
        PolylineSpecificationDialog,
        "values",
        return_value=(polyline.points, False, (-1.0, 0.0)),
    ):
        dialog._edit_entity(polyline.id)
    edited = dialog.document.active_sketch.entities[polyline.id]
    assert isinstance(edited, PolylineEntity) and edited.id == polyline.id and edited.bulges == (-1.0, 0.0)
    assert dialog.command_stack.undo()
    restored = dialog.document.active_sketch.entities[polyline.id]
    assert isinstance(restored, PolylineEntity) and restored.bulges == (1.0, 0.0)
    dialog.close()
    print("[OK] Qt renderer and precise edit preserve mixed polyline UUID and bulges")


def test_dxf_bulge_round_trip_preserves_lwpolyline_layer_and_values() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "bulge-source.dxf"
        export_path = Path(directory) / "bulge-export.dxf"
        source = ezdxf.new("R2010")
        source.layers.add("MIXED")
        source.modelspace().add_lwpolyline(
            [(0, 0, 1.0), (20, 0, -0.5), (30, 15, 0.0)],
            format="xyb",
            close=False,
            dxfattribs={"layer": "MIXED"},
        )
        source.modelspace().add_polyline2d(
            [(50, 0, 0.25), (70, 0, 0.0)],
            format="xyb",
            close=False,
            dxfattribs={"layer": "MIXED"},
        )
        source.saveas(source_path)

        dialog = TechnicalEditorDialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source_path), "DXF")):
            dialog._import_dxf()
        imported = list(dialog.document.active_sketch.entities.values())
        assert len(imported) == 2 and all(isinstance(entity, PolylineEntity) for entity in imported)
        assert all(entity.layer == "MIXED" for entity in imported)
        assert imported[0].bulges == (1.0, -0.5) and imported[1].bulges == (0.25,)
        assert all(isinstance(segment, ArcEntity) for segment in imported[0].segment_entities)
        assert isinstance(imported[1].segment_entities[0], ArcEntity)

        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(export_path), "DXF")):
            dialog._export_dxf()
        reopened = ezdxf.readfile(export_path)
        polylines = list(reopened.modelspace().query("LWPOLYLINE"))
        assert len(polylines) == 2 and all(polyline.dxf.layer == "MIXED" for polyline in polylines)
        bulge_sets = [
            [round(float(point[2]), 12) for point in polyline.get_points("xyb")]
            for polyline in polylines
        ]
        assert bulge_sets == [[1.0, -0.5, 0.0], [0.25, 0.0]]
        assert not list(reopened.modelspace().query("ARC"))
        assert not list(reopened.modelspace().query("LINE"))
        dialog.close()
    print("[OK] DXF LWPOLYLINE bulges round-trip natively without ARC/LINE flattening")


if __name__ == "__main__":
    test_bulge_is_an_exact_arc_with_stable_segment_identity_and_serialization()
    test_bulged_polyline_solver_snapping_and_profile_use_the_arc_not_chord()
    test_workspace_renders_and_edits_bulges_without_losing_definition()
    test_dxf_bulge_round_trip_preserves_lwpolyline_layer_and_values()
    print("CAD BULGE TESTS OK")

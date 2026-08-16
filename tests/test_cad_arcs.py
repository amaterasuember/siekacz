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

from app.technical_editor import ArcSpecificationDialog, TechnicalEditorDialog
from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.model import ArcEntity, CadDocument, Point2D, LineEntity
from cad.snapping import SnapEngine, SnapKind
from cad.solver import solve_and_apply
from cad.profiles import analyze_profile


def test_arc_is_analytic_and_round_trips_in_native_document() -> None:
    arc = ArcEntity(Point2D(10, 20), 25, 30, 210, layer="CUT")
    assert arc.start.distance_to(arc.point_at(0)) < 1e-12
    assert arc.end.distance_to(arc.point_at(1)) < 1e-12
    assert abs(arc.length - 25 * math.radians(210)) < 1e-10
    left, bottom, right, top = arc.bounds()
    assert left == -15 and top == 45
    assert right > 31 and bottom < 7.5
    document = CadDocument.create()
    document.active_sketch._add_entity(arc)
    restored = CadDocument.from_dict(document.to_dict())
    loaded = restored.active_sketch.entities[arc.id]
    assert loaded == arc and isinstance(loaded, ArcEntity)
    print("[OK] analytic arc geometry and UUID survive native serialization")


def test_arc_participates_in_solver_and_snapping() -> None:
    document = CadDocument.create()
    arc = ArcEntity(Point2D(0, 0), 9, 0, 160)
    crossing = LineEntity(Point2D(0, -20), Point2D(0, 20))
    document.active_sketch._add_entity(arc)
    document.active_sketch._add_entity(crossing)
    for constraint in (
        SketchConstraint(ConstraintType.X_COORDINATE, (GeometryReference(arc.id, "center"),), value=0),
        SketchConstraint(ConstraintType.Y_COORDINATE, (GeometryReference(arc.id, "center"),), value=0),
        SketchConstraint(ConstraintType.RADIUS, (GeometryReference(arc.id),), value=10),
        SketchConstraint(ConstraintType.ARC_LENGTH, (GeometryReference(arc.id),), value=math.pi * 10),
    ):
        document.active_sketch._add_constraint(constraint)
    solve_and_apply(document.active_sketch)
    solved = document.active_sketch.entities[arc.id]
    assert isinstance(solved, ArcEntity) and abs(solved.radius - 10) < 1e-6
    assert abs(solved.sweep_angle_deg - 180) < 1e-5

    result = SnapEngine(radius_pixels=12).query(
        document.active_sketch,
        Point2D(0.3, 9.8),
        pixels_per_unit=5,
    )
    assert result.active is not None
    assert result.active.kind == SnapKind.INTERSECTION
    assert result.active.point.distance_to(Point2D(0, 10)) < 1e-6
    nearest = SnapEngine(radius_pixels=12).query(
        document.active_sketch,
        Point2D(7.2, 7.2),
        pixels_per_unit=4,
    )
    assert any(candidate.kind == SnapKind.NEAREST and candidate.entity_id == arc.id for candidate in nearest.candidates)
    print("[OK] arc radius solves and arc intersections/nearest points snap analytically")


def test_arc_workspace_creation_editing_and_rendering() -> None:
    QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    center = Point2D(25, 30)
    endpoint = Point2D(25, 70)
    with patch.object(ArcSpecificationDialog, "exec", return_value=ArcSpecificationDialog.DialogCode.Accepted), patch.object(
        ArcSpecificationDialog,
        "values",
        return_value=(center, 40.0, 0.0, 90.0),
    ):
        dialog._create_geometry("arc", center, endpoint)
    entity = next(iter(dialog.document.active_sketch.entities.values()))
    assert isinstance(entity, ArcEntity)
    assert isinstance(dialog.canvas._entity_items[entity.id], QGraphicsPathItem)
    dialog.selection_model.set((entity.id,))
    dialog._refresh_side_panels()
    assert "Łuk kołowy" in dialog.property_title.text()
    assert "Rozwarcie: 90.000°" in dialog.property_details.text()
    assert dialog.command_stack.undo() and entity.id not in dialog.document.active_sketch.entities
    assert dialog.command_stack.redo() and entity.id in dialog.document.active_sketch.entities
    dialog.close()
    print("[OK] workspace creates, renders, describes and undo/redoes a native arc")


def test_arc_and_chord_form_a_closed_diagnostic_profile() -> None:
    document = CadDocument.create()
    arc = ArcEntity(Point2D(0, 0), 20, 0, 180)
    chord = LineEntity(arc.end, arc.start)
    document.active_sketch._add_entity(arc)
    document.active_sketch._add_entity(chord)
    report = analyze_profile(document.active_sketch)
    assert report.is_valid_surface
    assert report.open_endpoint_count == 0
    assert len(report.loops) == 1
    assert abs(abs(report.loops[0].signed_area) - math.pi * 20**2 / 2) < 2.0
    print("[OK] profile diagnostics recognizes a closed analytic arc-and-chord loop")


def test_dxf_arc_import_export_round_trip_preserves_curve_type() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "source.dxf"
        exported_path = Path(directory) / "exported.dxf"
        source = ezdxf.new("R2010")
        source.layers.add("ARC_LAYER")
        source.modelspace().add_arc((12, -8), 35, 25, 220, dxfattribs={"layer": "ARC_LAYER"})
        source.saveas(source_path)

        dialog = TechnicalEditorDialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source_path), "Plik DXF (*.dxf)")):
            dialog._import_dxf()
        imported = next(iter(dialog.document.active_sketch.entities.values()))
        assert isinstance(imported, ArcEntity)
        assert imported.layer == "ARC_LAYER"
        assert abs(imported.start_angle_deg - 25) < 1e-9
        assert abs(imported.sweep_angle_deg - 195) < 1e-9

        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(exported_path), "Plik DXF (*.dxf)")):
            dialog._export_dxf()
        reopened = ezdxf.readfile(exported_path)
        arcs = list(reopened.modelspace().query("ARC"))
        assert len(arcs) == 1
        assert arcs[0].dxf.layer == "ARC_LAYER"
        assert abs(float(arcs[0].dxf.radius) - 35) < 1e-9
        assert not list(reopened.modelspace().query("LWPOLYLINE"))
        dialog.close()
    print("[OK] DXF ARC round-trip preserves an ARC and its layer without tessellation")


if __name__ == "__main__":
    test_arc_is_analytic_and_round_trips_in_native_document()
    test_arc_participates_in_solver_and_snapping()
    test_arc_workspace_creation_editing_and_rendering()
    test_arc_and_chord_form_a_closed_diagnostic_profile()
    test_dxf_arc_import_export_round_trip_preserves_curve_type()
    print("CAD ARC TESTS OK")

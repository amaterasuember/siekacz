from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from app.cad_viewer import CadInspectionCanvas, CadInspectionDialog, CadViewCube
from app.simple_window import SimpleCutWindow
from cad.inspection import CadInspectionError, load_cad_inspection


def _ascii_stl() -> str:
    return """solid rectangle
facet normal 0 0 1
  outer loop
    vertex 0 0 0
    vertex 100 0 0
    vertex 100 50 0
  endloop
endfacet
facet normal 0 0 1
  outer loop
    vertex 0 0 0
    vertex 100 50 0
    vertex 0 50 0
  endloop
endfacet
endsolid rectangle
"""


def _step_wire() -> str:
    return """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('SIEKACZ TEST'),'2;1');
ENDSEC;
DATA;
#1=CARTESIAN_POINT('',(0.,0.,0.));
#2=CARTESIAN_POINT('',(100.,0.,0.));
#3=CARTESIAN_POINT('',(100.,40.,0.));
#4=VERTEX_POINT('',#1);
#5=VERTEX_POINT('',#2);
#6=VERTEX_POINT('',#3);
#7=DIRECTION('',(1.,0.,0.));
#8=VECTOR('',#7,1.);
#9=LINE('',#1,#8);
#10=EDGE_CURVE('',#4,#5,#9,.T.);
#11=EDGE_CURVE('',#5,#6,#9,.T.);
#12=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));
ENDSEC;
END-ISO-10303-21;
"""


def test_dxf_loads_real_edges_and_units() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "detail.dxf"
        document = ezdxf.new("R2010")
        document.header["$INSUNITS"] = 4
        modelspace = document.modelspace()
        line = modelspace.add_line((0, 0, 0), (125, 0, 0))
        modelspace.add_circle((30, 20), 10)
        document.saveas(source)

        model = load_cad_inspection(source)
        exact = next(index for index, edge in enumerate(model.edges) if edge.source_id == line.dxf.handle)
        assert model.source_format == "DXF"
        assert model.source_units == "mm"
        assert abs(model.edge_length(exact) - 125.0) < 1e-9
        assert len(model.edges) > 2  # the circle is actual selectable geometry, not a bounding rectangle
    print("[OK] DXF keeps real selectable geometry and normalises units")


def test_ascii_and_binary_stl_load_mesh_geometry() -> None:
    with tempfile.TemporaryDirectory() as directory:
        directory_path = Path(directory)
        ascii_path = directory_path / "rectangle.stl"
        ascii_path.write_text(_ascii_stl(), encoding="utf-8")
        ascii_model = load_cad_inspection(ascii_path)
        assert ascii_model.source_format == "STL"
        assert ascii_model.dimensions == (100.0, 50.0, 0.0)
        assert len(ascii_model.faces) == 2
        assert len(ascii_model.edges) == 5  # shared triangle side is deduplicated
        assert all(not edge.source_id for edge in ascii_model.edges)
        assert any(abs(ascii_model.edge_length(index) - 100.0) < 1e-9 for index in range(len(ascii_model.edges)))

        binary_path = directory_path / "triangle.stl"
        header = b"SIEKACZ STL".ljust(80, b"\0")
        facet = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 30, 0, 0, 0, 40, 0, 0)
        binary_path.write_bytes(header + struct.pack("<I", 1) + facet)
        binary_model = load_cad_inspection(binary_path)
        assert len(binary_model.faces) == 1
        assert sorted(round(binary_model.edge_length(index), 6) for index in range(3)) == [30.0, 40.0, 50.0]
    print("[OK] ASCII and binary STL expose mesh sides for measurement")


def test_step_loads_brep_edges_and_millimetres() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "wire.step"
        source.write_text(_step_wire(), encoding="utf-8")
        model = load_cad_inspection(source)
        assert model.source_format == "STEP"
        assert model.source_units == "mm"
        assert model.dimensions == (100.0, 40.0, 0.0)
        assert sorted(round(model.edge_length(index), 6) for index in range(2)) == [40.0, 100.0]

        broken = Path(directory) / "empty.step"
        broken.write_text("ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;", encoding="utf-8")
        try:
            load_cad_inspection(broken)
        except CadInspectionError as exc:
            assert "B-Rep" in str(exc)
        else:
            raise AssertionError("STEP without topology must be rejected with an actionable error")
    print("[OK] STEP uses B-Rep endpoints and rejects files without measurable topology")


def test_canvas_rotates_and_measures_edge_and_two_points() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "rectangle.stl"
        source.write_text(_ascii_stl(), encoding="utf-8")
        model = load_cad_inspection(source)
        canvas = CadInspectionCanvas()
        canvas.resize(800, 600)
        messages: list[str] = []
        canvas.measurement_changed.connect(messages.append)
        canvas.set_model(model)
        canvas.set_top_view()
        before = [(point.x(), point.y()) for point in canvas._projected]
        canvas.rotate_axis("z")
        after = [(point.x(), point.y()) for point in canvas._projected]
        assert before != after

        canvas.set_top_view()
        edge = next(index for index in range(len(model.edges)) if abs(model.edge_length(index) - 100.0) < 1e-9)
        first = canvas._projected[model.edges[edge].start]
        second = canvas._projected[model.edges[edge].end]
        canvas._select_at(QPointF((first.x() + second.x()) / 2, (first.y() + second.y()) / 2))
        assert canvas.selected_edge == edge
        assert messages[-1].endswith("100.000 mm")

        canvas.set_two_point_mode(True)
        canvas._select_at(canvas._projected[model.edges[edge].start])
        canvas._select_at(canvas._projected[model.edges[edge].end])
        assert messages[-1] == "Odległość dwóch punktów: 100.000 mm"
        canvas.close()
    print("[OK] CAD canvas rotates and supports edge and two-point measurement")


def test_dialog_opens_each_supported_format_and_updates_summary() -> None:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory, patch("app.cad_viewer.repositories.set_setting"):
        directory_path = Path(directory)
        stl_path = directory_path / "rectangle.stl"
        step_path = directory_path / "wire.step"
        stl_path.write_text(_ascii_stl(), encoding="utf-8")
        step_path.write_text(_step_wire(), encoding="utf-8")
        dialog = CadInspectionDialog()
        dialog.open_file(stl_path)
        assert dialog.model is not None and dialog.model.source_format == "STL"
        assert "100.000 × 50.000" in dialog.dimension_label.text()
        dialog.open_file(step_path)
        assert dialog.model is not None and dialog.model.source_format == "STEP"
        assert dialog.file_label.text() == "wire.step"
        dialog.close()
    print("[OK] integrated dialog opens supported 3D formats and refreshes metadata")


def test_view_cube_replaces_axis_buttons_and_snaps_to_faces_and_corners() -> None:
    QApplication.instance() or QApplication([])
    dialog = CadInspectionDialog()
    try:
        assert isinstance(dialog.view_cube, CadViewCube)
        axis_buttons = [
            button.text()
            for button in dialog.findChildren(QToolButton)
            if button.text() in {"X +90°", "Y +90°", "Z +90°"}
        ]
        assert axis_buttons == []
        QTest.mouseClick(dialog.view_cube, Qt.MouseButton.LeftButton, pos=QPoint(41, 16))
        assert (dialog.canvas.yaw, dialog.canvas.pitch, dialog.canvas.roll) == (0.0, 0.0, 0.0)
        QTest.mouseClick(dialog.view_cube, Qt.MouseButton.LeftButton, pos=QPoint(65, 18))
        assert (dialog.canvas.yaw, dialog.canvas.pitch) == (-35.0, 25.0)
    finally:
        dialog.close()
    print("[OK] clickable CAD cube selects face/corner views and replaces axis buttons")


def test_main_window_exposes_cad_inspection_next_to_parts() -> None:
    QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        buttons = [button for button in window.findChildren(QToolButton) if button.text() == "CAD"]
        assert len(buttons) == 1
        assert "DXF, STEP lub STL" in buttons[0].toolTip()
        with patch("app.cad_viewer.CadInspectionDialog") as dialog_type:
            buttons[0].click()
            dialog_type.assert_called_once_with(window)
            dialog_type.return_value.exec.assert_called_once()
    finally:
        window.close()
    print("[OK] main formatki workflow exposes the CAD inspection window")


if __name__ == "__main__":
    test_dxf_loads_real_edges_and_units()
    test_ascii_and_binary_stl_load_mesh_geometry()
    test_step_loads_brep_edges_and_millimetres()
    test_canvas_rotates_and_measures_edge_and_two_points()
    test_dialog_opens_each_supported_format_and_updates_summary()
    test_view_cube_replaces_axis_buttons_and_snaps_to_faces_and_corners()
    test_main_window_exposes_cad_inspection_next_to_parts()
    print("CAD INSPECTION TESTS OK")

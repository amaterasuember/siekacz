from __future__ import annotations
import math
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
from PySide6.QtWidgets import QApplication, QLineEdit, QStyleOptionViewItem
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPointF
from app.simple_window import SimpleCutWindow, PART_QUANTITY_COLUMN, PART_HEIGHT_COLUMN, PART_WIDTH_COLUMN
from app.cad_viewer import CadInspectionCanvas
from cad.inspection import load_dxf
from import_export.dxf_io import import_dxf_parts
from core.cad_contours import contours_from_notes
from core.models import Project

app = QApplication.instance() or QApplication([])
w = SimpleCutWindow()
w._material_catalog = []
w.parts.setRowCount(0)
w.stock_table.setRowCount(0)
w.add_part_row([5, 100, 50, 1, "PA6"])
w.add_part_row([3, 80, 40, 1, "PE"])
w.parts.clearSelection()
w.parts.selectRow(0)
w.remove_selected_rows()
assert w.stock_table.rowCount() == 1
assert w._stock_cell_text(0, 0) == "PE"
w._undo_parts()
assert w.stock_table.rowCount() == 2
w._redo_parts()
assert w.stock_table.rowCount() == 1
# Clearing all geometry releases the automatic board.
w.parts.item(0, PART_HEIGHT_COLUMN).setText("")
w.parts.item(0, PART_WIDTH_COLUMN).setText("")
assert w.stock_table.rowCount() == 0
w.parts.item(0, PART_HEIGHT_COLUMN).setText("40")
w.parts.item(0, PART_WIDTH_COLUMN).setText("80")
assert w.stock_table.rowCount() == 1
# Exercise real delegate event/commit ordering, without starting an optimizer.
w.parts_delegate.calculation_requested.disconnect()
calculated = []
w.parts_delegate.calculation_requested.connect(lambda: calculated.append(w.parts.item(0, PART_QUANTITY_COLUMN).text()))
index = w.parts.model().index(0, PART_QUANTITY_COLUMN)
editor = w.parts_delegate.createEditor(w.parts.viewport(), QStyleOptionViewItem(), index)
assert isinstance(editor, QLineEdit)
w.parts_delegate.commitData.connect(lambda e: w.parts_delegate.setModelData(e, w.parts.model(), index))
editor.setText("17")
QTest.keyClick(editor, Qt.Key.Key_Return)
app.processEvents()
assert calculated == ["17"] and w.parts.rowCount() == 1
w.add_part_row([7, "", "", 1, "PA6"])
w.add_part_row([7, "", "", 1, "PA6"])
w._remove_empty_part_drafts()
assert w.parts.rowCount() == 1
w._undo_parts()
assert w.parts.rowCount() == 3
w._redo_parts()
assert w.parts.rowCount() == 1
w.close()
# A material menu must follow its item after an earlier row is removed.
from app.material_catalog import MaterialCatalogEntry, catalog_family_label
menu_window = SimpleCutWindow()
entry = MaterialCatalogEntry("PVC", 3, 100, 123, "PVC PŁYTA SZARA", width=1000, height=2000)
menu_window._material_catalog = [entry]
menu_window.parts.setRowCount(0)
menu_window.stock_table.setRowCount(0)
for _ in range(2):
    menu_window.add_part_row([3, 100, 50, 1, catalog_family_label(entry)])
menu_window.parts.selectRow(0)
menu_window.remove_selected_rows()
selected_rows = []
menu_window._choose_part_thickness = lambda row, value, menu=None: selected_rows.append(row)
menu = menu_window.parts.cellWidget(0, 0).menu()
next(a for a in menu.actions() if a.isCheckable()).trigger()
assert selected_rows == [0]
menu_window.close()
with tempfile.TemporaryDirectory() as folder:
    source = Path(folder)/"rounded.dxf"
    d = ezdxf.new()
    d.header["$INSUNITS"] = 4
    m = d.modelspace()
    m.add_lwpolyline([(0,0), (100,0), (100,40)], close=False)
    m.add_arc((80,40),20,0,90)
    m.add_lwpolyline([(-100,-100),(200,-100),(200,200),(-100,200)],close=True)
    d.saveas(source)
    model = load_dxf(source)
    arc = next(i for i,e in enumerate(model.edges) if e.radius)
    group = model.edges[arc].source_id
    assert math.isclose(sum(model.edge_length(i) for i,e in enumerate(model.edges) if e.source_id == group), math.pi*10)
    canvas = CadInspectionCanvas()
    canvas.resize(800,600)
    canvas.set_model(model)
    messages=[]
    canvas.measurement_changed.connect(messages.append)
    edge=model.edges[arc]
    canvas._select_at((canvas._projected[edge.start]+canvas._projected[edge.end])/2)
    assert "R 20.000" in messages[-1]
    canvas.set_two_point_mode(True)
    first=canvas._project_point((50,0,0))
    canvas._select_at(first)
    canvas._select_at(canvas._project_point((100,40,0)))
    assert math.isclose(math.dist(*canvas.measurement_points),math.hypot(50,40))
    part=import_dxf_parts(source)[0]
    from app.cad_viewer import CadInspectionDialog
    owner = SimpleCutWindow()
    owner._material_catalog = []
    owner.parts.setRowCount(0)
    owner.stock_table.setRowCount(0)
    dialog = CadInspectionDialog(owner)
    dialog.open_file(source)
    dialog.canvas.selected_edge = next(i for i,e in enumerate(dialog.model.edges) if e.radius)
    dialog.isolate_contour()
    assert dialog.model.dimensions[0] == 100 and dialog.model.dimensions[1] == 60
    dialog.add_contour_to_parts()
    collected = owner._collect_parts()
    assert len(collected) == 1 and collected[0].width == 100 and collected[0].height == 60
    assert contours_from_notes(collected[0].notes)
    dialog.close()
    owner.close()
    source.unlink()
    restored=Project.from_dict(Project(sheet_parts=[part]).to_dict()).sheet_parts[0]
    assert len(contours_from_notes(restored.notes)) > 4
print("PASS: Enter commits before calculation; deleted/cleared parts release boards; undo; exact arc R and length; side snap; portable contours")

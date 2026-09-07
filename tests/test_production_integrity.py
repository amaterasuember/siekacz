"""Independent output-boundary, persistence and PNG regressions."""
from __future__ import annotations

import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QGraphicsScene

from algorithms.guillotine_technology import validate_guillotine_feasibility
from core.atomic_file import atomic_write_text
from core.layout_validation import assert_sheet_result, layout_geometry_errors
from core.models import OptimizationResult, OptimizationSettings, PlacedSheetPart, Project, SheetLayout, SheetPart, SheetStock
from import_export.image_export import export_scene_png
from workers.optimizer_worker import optimize_sheet_project


def project() -> Project:
    return Project(
        sheet_stock=[SheetStock("M", 18, 100, 100, 2, allow_rotation=False)],
        sheet_parts=[SheetPart("A", 100, 100, 1, "M", 18)],
        settings=OptimizationSettings(kerf=5, kerf_tolerance=0, min_reusable_offcut_size=0, multi_core=False),
    )


def rejected(call, error=ValueError) -> None:
    try:
        call()
    except error:
        return
    raise AssertionError("Invalid operation was accepted")


def test_geometry_boundary() -> None:
    a, b = SheetPart("A", 40, 60, 1, "M", 18), SheetPart("B", 40, 30, 1, "M", 18)
    layout = SheetLayout(project().sheet_stock[0], 1, [PlacedSheetPart(a, 0, 0, 40, 60), PlacedSheetPart(b, 42, 15, 40, 30)])
    # Different heights and Y positions used to bypass same-row kerf checks.
    assert layout_geometry_errors(layout, 5)
    assert not validate_guillotine_feasibility(layout, 5)[0]
    layout.parts[1].x = 45
    assert not layout_geometry_errors(layout, 5)
    for field, value in [("x", float("nan")), ("y", float("inf")), ("width", 0), ("height", -1)]:
        broken = deepcopy(layout)
        setattr(broken.parts[0], field, value)
        assert not validate_guillotine_feasibility(broken, 5)[0]


def test_order_boundary() -> None:
    p = project()
    result = optimize_sheet_project(p)
    assert_sheet_result(result, p.sheet_parts, 5)
    for mutate in [
        lambda r: r.sheet_layouts[0].parts.clear(),
        lambda r: r.sheet_layouts[0].parts.append(deepcopy(r.sheet_layouts[0].parts[0])),
        lambda r: setattr(r.sheet_layouts[0].parts[0], "width", 99),
        lambda r: setattr(r.sheet_layouts[0].parts[0], "rotated", True),
        lambda r: setattr(r, "utilization", 12),
        lambda r: setattr(r, "waste", float("nan")),
    ]:
        broken = deepcopy(result)
        mutate(broken)
        rejected(lambda: assert_sheet_result(broken, p.sheet_parts, 5), RuntimeError)
    # Duplicate names with different specifications must not cancel each other.
    wrong = deepcopy(p.sheet_parts[0]); wrong.width = 50
    rejected(lambda: assert_sheet_result(result, [wrong], 5), RuntimeError)


def test_input_boundary() -> None:
    for field in ("kerf", "kerf_tolerance", "margin", "sheet_allowance", "min_reusable_offcut_size", "saw_feed_m_per_min"):
        for value in (float("nan"), float("inf"), -1, None, True):
            p = project(); setattr(p.settings, field, value)
            rejected(lambda: optimize_sheet_project(p))
    for value in (0, -1, 1.0000001, True, "", float("nan")):
        p = project(); p.sheet_parts[0].quantity = value
        rejected(lambda: optimize_sheet_project(p))
    for field in ("price", "nominal_width", "nominal_height"):
        p = project(); setattr(p.sheet_stock[0], field, float("nan"))
        rejected(lambda: optimize_sheet_project(p))


def test_edge_layouts() -> None:
    for mode in ("comfort", "sport"):
        for kerf in (0, 5, 30):
            p = project(); p.settings.optimization_mode = mode; p.settings.kerf = kerf
            p.sheet_parts = [SheetPart("A", (100 - kerf) / 2, 100, 2, "M", 18, allow_rotation=False)]
            before = p.to_dict()
            result = optimize_sheet_project(p)
            assert len(result.sheet_layouts) == 1 and not result.missing_sheet_layouts
            assert_sheet_result(result, p.sheet_parts, kerf)
            assert result.sheet_layouts[0].used_width == 100
            assert p.to_dict() == before
            assert result == optimize_sheet_project(p)
        p = project(); p.sheet_parts[0].quantity = 3
        result = optimize_sheet_project(p)
        assert len(result.sheet_layouts) == 2 and len(result.missing_sheet_layouts) == 1
        p.sheet_parts[0].width = 101
        result = optimize_sheet_project(p)
        assert len(result.unplaced_sheet_parts) == 3



def test_final_statistics_and_feed() -> None:
    from math import isclose
    from algorithms.cut_metrics import compute_cut_summary
    from workers.optimizer_worker import optimize_sheet_order
    p = project()
    p.sheet_stock[0].price = 50
    p.sheet_parts[0].width = 40
    p.sheet_parts[0].quantity = 3
    p.settings.saw_feed_m_per_min = 6
    slow = optimize_sheet_project(p)
    p.settings.saw_feed_m_per_min = 24
    fast = optimize_sheet_project(p)
    assert fast.total_estimated_cut_time_s < slow.total_estimated_cut_time_s
    for result in (slow, fast):
        assert isclose(result.total_estimated_cut_time_s, compute_cut_summary(result).total_time_s)
        assert isclose(sum(l.estimated_cut_time_s for l in result.sheet_layouts + result.missing_sheet_layouts), result.total_estimated_cut_time_s)
    merged = optimize_sheet_order([p, deepcopy(p)])
    assert merged.total_cost == fast.total_cost * 2
    assert isclose(merged.waste, fast.waste * 2)
    assert isclose(merged.total_reusable_offcut_area, fast.total_reusable_offcut_area * 2)
    assert len(merged.reusable_offcuts) == len(fast.reusable_offcuts) * 2
    assert isclose(merged.utilization, fast.utilization)
    assert isclose(merged.total_estimated_cut_time_s, compute_cut_summary(merged).total_time_s)
    p.sheet_stock[0].stack_size = 2
    p.sheet_parts[0].quantity = 4
    stacked = optimize_sheet_project(p)
    assert isclose(stacked.total_estimated_cut_time_s, compute_cut_summary(stacked).total_time_s)


def test_atomic_files_and_history() -> None:
    from app import project_history as history
    from app.state import AppState
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); path = root / "project.json"
        path.write_text("original", encoding="utf-8")
        with patch("core.atomic_file.os.replace", side_effect=OSError("disk failure")):
            rejected(lambda: atomic_write_text(path, "replacement"), OSError)
        assert path.read_text() == "original" and not list(root.glob("*.tmp"))
        with patch("app.state.repositories.record_project"):
            state = AppState(); state.project = project(); state.save(path)
            restored = AppState(); restored.load(path)
            assert restored.project == state.project
        with patch.object(history, "APP_DIR", root), patch.object(history, "HISTORY_PATH", root / "history.json"), patch.object(history, "HISTORY_LOCK_PATH", root / "history.lock"):
            for content in ('[{"id":"keep"}, 17]', '{broken'):
                history.HISTORY_PATH.write_text(content, encoding="utf-8")
                rejected(lambda: history.save_project_record({"id": "new"}), history.ProjectHistoryReadError)
                assert history.HISTORY_PATH.read_text(encoding="utf-8") == content
            history.HISTORY_PATH.write_bytes(b"\xff\xfe")
            rejected(lambda: history.save_project_record({"id": "new"}), history.ProjectHistoryReadError)


def test_png_export() -> None:
    app = QApplication.instance() or QApplication([])
    scene = QGraphicsScene(); scene.addRect(0, 0, 500, 200)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for width in (320, 1200, 2400):
            target = root / f"{width}.png"
            export_scene_png(scene, str(target), width=width, header_text="Klient: test")
            image = QImage(str(target))
            assert not image.isNull() and image.width() == width
        rejected(lambda: export_scene_png(scene, str(root / "absent" / "fail.png")), OSError)
        for width in (0, -1, 1_000_000, True):
            rejected(lambda: export_scene_png(scene, str(root / "invalid.png"), width=width))
        scene.addRect(0, 0, 1, 100_000)
        rejected(lambda: export_scene_png(scene, str(root / "huge.png")))
    assert app is not None



def test_table_and_history_input() -> None:
    from collections import Counter
    from PySide6.QtCore import QItemSelectionModel, Qt
    from app.material_catalog import catalog_from_dicts
    from app.simple_window import _history_summary, FIXED_SHEET_PRESETS
    from ui.stock_panel import EditableTable, _float, _int

    app = QApplication.instance() or QApplication([])
    table = EditableTable(["name", "quantity"], ["", 1])
    table.add_row(["A", 11]); table.add_row(["B", 22]); table.add_row(["C", 33])
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    selection = table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    selection.select(table.model().index(0, 0), flags)
    selection.select(table.model().index(1, 0), flags)
    table.duplicate_selected()
    assert Counter((r["name"], r["quantity"]) for r in table.rows_as_dicts()) == Counter({("A", "11"): 2, ("B", "22"): 2, ("C", "33"): 1})
    table.setCurrentCell(0, 0)
    QApplication.clipboard().setText("Z\t99\nY\t88")
    table.paste()
    rows = table.rows_as_dicts()
    assert {("Z", "99"), ("Y", "88")} <= {(r["name"], r["quantity"]) for r in rows}
    assert table.isSortingEnabled()
    table.setSortingEnabled(False); table.add_row(["D", 44])
    assert not table.isSortingEnabled()
    for value in ("nan", "inf", "", "garbage"):
        assert _float(value, -1) == -1 and _int(value, -1) == -1
    assert _int("1.5", -1) == -1
    for summary in (None, 17, [], {"used_sheets": "nan", "utilization": "bad"}):
        parsed = _history_summary({"summary": summary})
        assert parsed["used_sheets"] == 0 and parsed["utilization"] == 0
    valid = {"material": "M", "thickness": 18, "net_price_m2": 12}
    for value in (float("nan"), float("inf"), -1, "bad"):
        assert catalog_from_dicts([{**valid, "thickness": value}]) == []
    assert len(catalog_from_dicts([valid])) == 1
    assert catalog_from_dicts([None, 17, "broken", valid]) == catalog_from_dicts([valid])
    assert (1300.0, 1400.0) in FIXED_SHEET_PRESETS
    table.deleteLater()
    assert app is not None


def test_settings_and_small_window() -> None:
    from app.simple_window import AlgorithmSettingsDialog, SimpleCutWindow
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    with patch("app.simple_window.repositories.set_setting"):
        window = SimpleCutWindow()
        try:
            p = project()
            window._load_project_inputs(p)
            assert window.min_reusable_offcut.value() == 0
            dialog = AlgorithmSettingsDialog(window, window._algo_settings)
            dialog._theme_choice.setCurrentIndex(dialog._theme_choice.findData("light"))
            dialog._apply_and_close()
            assert dialog.result_settings()["theme"] == "light"
            window.resize(1280, 760)
            window.show()
            for theme in ("dark", "light", "dark"):
                window._set_theme_combo(theme)
                QTest.qWait(250)
                assert window.layout_view.theme == theme
                assert window.centralWidget().isVisible()
                assert window._main_splitter.sizes()[0] >= 600
            dialog.deleteLater()
        finally:
            window.close()
    assert app is not None


if __name__ == "__main__":
    for test in (test_geometry_boundary, test_order_boundary, test_input_boundary, test_edge_layouts, test_final_statistics_and_feed, test_atomic_files_and_history, test_png_export, test_table_and_history_input, test_settings_and_small_window):
        test()
        print(f"[OK] {test.__name__}", flush=True)

"""Physical grain survives optimization, missing boards, projects and UI undo."""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.grain import part_orientations
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_project


def test_physical_alignment() -> None:
    for mode in ("comfort", "sport"):
        for board_axis in ("x", "y"):
            for part_axis in ("x", "y"):
                stock = SheetStock("Wood", 18, 400, 600, 1, grain_direction=board_axis)
                part = SheetPart("Grain", 350, 150, 5, "Wood", 18, grain_direction=part_axis)
                project = Project(sheet_stock=[stock], sheet_parts=[part], settings=OptimizationSettings(
                    optimization_mode=mode, kerf=5, kerf_tolerance=0, multi_core=False))
                result = optimize_sheet_project(project)
                placed = [p for layout in [*result.sheet_layouts, *result.missing_sheet_layouts] for p in layout.parts if not p.part.is_waste_fill]
                assert len(placed) == 5 and not result.unplaced_sheet_parts
                assert all(p.rotated == (board_axis != part_axis) for p in placed)
                assert all((p.width, p.height) == ((150, 350) if p.rotated else (350, 150)) for p in placed)
                assert Project.from_dict(project.to_dict()).sheet_parts[0].grain_direction == part_axis
    square = SheetPart("Square", 50, 50, 1, "Wood", 18, grain_direction="x")
    board = SheetStock("Wood", 18, 100, 100, 1, grain_direction="y")
    assert part_orientations(square, board) == ((50, 50, True),)
    assert not part_orientations(replace(square, allow_rotation=False), board)
    assert not part_orientations(square, replace(board, grain_direction="none"))
    assert part_orientations(replace(square, grain_direction="length"), board) == ((50, 50, False),)


def test_all_engines_and_import() -> None:
    from algorithms.two_d_guillotine import optimize_2d_guillotine
    from algorithms.two_d_maxrects import optimize_2d_maxrects
    from algorithms.two_d_skyline import optimize_2d_skyline
    from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
    from import_export.batch_workbook import parse_clipboard_rows
    from core.validation import validateProjectInput
    stock = SheetStock("Wood", 18, 180, 380, 1, grain_direction="y")
    part = SheetPart("Required turn", 350, 150, 1, "Wood", 18, grain_direction="x")
    for engine in (optimize_2d_guillotine, optimize_2d_maxrects, optimize_2d_skyline, optimize_2d_vertical_segmented):
        result = engine([stock], [part], kerf=5)
        assert not result.unplaced_sheet_parts, engine.__name__
        assert result.sheet_layouts[0].parts[0].rotated, engine.__name__
    p = Project(sheet_stock=[replace(stock, grain_direction="none"), stock], sheet_parts=[part],
                settings=OptimizationSettings(multi_core=False))
    assert optimize_sheet_project(p).sheet_layouts[0].stock.grain_direction == "y"
    p.sheet_stock = [replace(stock, grain_direction="none")]
    assert any("gwiazdk" in e for e in validateProjectInput(p))
    cut_stock = SheetStock("Wood", 18, 400, 600, 1, grain_direction="y", preferred_cut_axis="x")
    result = optimize_2d_vertical_segmented([cut_stock], [part], kerf=5)
    assert result.sheet_layouts[0].stock.grain_direction == "x"
    assert not result.sheet_layouts[0].parts[0].rotated
    rows = parse_clipboard_rows("Materiał\tGrubość\tWysokość\tSzerokość\tIlość\tSłoje\nWood\t18\t150\t350\t1\tszerokość", "parts")
    assert rows[0]["grain_direction"] == "x"


def test_ui_roundtrip() -> None:
    from database.db import init_db
    from PySide6.QtWidgets import QApplication
    from app.simple_window import SimpleCutWindow
    from ui.grain_delegate import GRAIN_ROLE
    init_db()
    app = QApplication.instance() or QApplication([])
    w = SimpleCutWindow()
    p = Project(sheet_stock=[SheetStock("Wood", 18, 400, 600, 2, grain_direction="y")],
                sheet_parts=[SheetPart("A", 350, 150, 3, "Wood", 18, grain_direction="x")])
    w._load_project_inputs(p)
    assert w._collect_stock()[0].grain_direction == "y"
    assert w._collect_parts()[0].grain_direction == "x"
    assert w.stock_table.property("grainWidthColumn") == 2
    assert w.parts.property("grainWidthColumn") == 3
    snapshot = w._snapshot_parts()
    w.parts.item(0, 0).setData(GRAIN_ROLE, "y")
    w._apply_parts_snapshot(snapshot)
    assert w._collect_parts()[0].grain_direction == "x"
    assert w._collect_parts()[0].width == 350
    w.close()
    app.processEvents()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="siekacz-grain-") as profile:
        os.environ.update(USERPROFILE=profile, HOME=profile)
        test_physical_alignment()
        test_all_engines_and_import()
        test_ui_roundtrip()
    print("PASS: physical side alignment, missing stock, square grain, rotation lock, persistence and UI")

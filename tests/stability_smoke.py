from __future__ import annotations

import math
import os
import random
import tempfile
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from app.simple_window import MAX_TOTAL_PARTS, SimpleCutWindow
from core.models import OptimizationResult, OptimizationSettings, Project, SheetLayout, SheetPart, SheetStock
from import_export.image_export import export_scene_png
from ui.layout_view import LayoutView
from workers.optimizer_worker import optimize_sheet_project


EPS = 0.001


def make_project(
    stock_width: float,
    stock_height: float,
    quantity: int,
    kerf: float,
    parts: list[tuple[float, float, int]],
    mode: str = "comfort",
) -> Project:
    project = Project()
    project.sheet_stock = [
        SheetStock(
            "standard",
            1,
            stock_width,
            stock_height,
            quantity,
            allow_rotation=True,
            min_offcut_width=80,
            min_offcut_height=80,
        )
    ]
    project.sheet_parts = [
        SheetPart(f"{width:g} x {height:g}", width, height, qty, "standard", 1, allow_rotation=True)
        for width, height, qty in parts
    ]
    project.settings = OptimizationSettings(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        kerf=kerf,
        margin=0,
        cutting_mode="hybrid",
        optimization_mode=mode,
        display_orientation="horizontal",
        min_reusable_offcut_size=80,
    )
    return project


def _all_layouts(result: OptimizationResult) -> list[SheetLayout]:
    return list(result.sheet_layouts) + list(result.missing_sheet_layouts)


def _requested_counts(project: Project) -> Counter[str]:
    return Counter({part.name: part.quantity for part in project.sheet_parts})


def _placed_counts(result: OptimizationResult) -> Counter[str]:
    counts: Counter[str] = Counter()
    for layout in _all_layouts(result):
        for placement in layout.parts:
            if getattr(placement.part, "is_waste_fill", False):
                continue
            counts[placement.part.name] += 1
    return counts


def assert_layout_invariants(project: Project, result: OptimizationResult, expect_all_placed: bool = False) -> None:
    stock_limit = sum(max(0, stock.quantity) for stock in project.sheet_stock)
    assert len(result.sheet_layouts) <= stock_limit
    assert math.isfinite(result.utilization)
    assert 0 <= result.utilization <= 100 + EPS
    assert result.waste >= -EPS

    requested = _requested_counts(project)
    placed = _placed_counts(result)
    for name, count in placed.items():
        assert count <= requested[name], f"{name} duplicated: {count} > {requested[name]}"
    if expect_all_placed:
        assert placed == requested
        assert not result.unplaced_sheet_parts

    for layout in _all_layouts(result):
        assert 0 <= layout.sheet_utilization <= 100 + EPS
        assert 0 <= layout.utilization <= 100 + EPS
        if layout.parts:
            assert layout.is_guillotine_feasible
            assert layout.cut_tree
        for placement in layout.parts:
            assert math.isfinite(placement.x)
            assert math.isfinite(placement.y)
            assert math.isfinite(placement.width)
            assert math.isfinite(placement.height)
            assert placement.x >= -EPS
            assert placement.y >= -EPS
            assert placement.width > 0
            assert placement.height > 0
            assert placement.x + placement.width <= layout.stock.width + EPS
            assert placement.y + placement.height <= layout.stock.height + EPS

        kerf = project.settings.kerf
        for index, first in enumerate(layout.parts):
            for second in layout.parts[index + 1 :]:
                x_overlap = first.x < second.x + second.width - EPS and second.x < first.x + first.width - EPS
                y_overlap = first.y < second.y + second.height - EPS and second.y < first.y + first.height - EPS
                assert not (x_overlap and y_overlap), f"{first.part.name} overlaps {second.part.name}"
                if y_overlap:
                    gap = max(second.x - (first.x + first.width), first.x - (second.x + second.width))
                    assert gap + EPS >= kerf, f"x kerf gap too small: {gap}"
                if x_overlap:
                    gap = max(second.y - (first.y + first.height), first.y - (second.y + second.height))
                    assert gap + EPS >= kerf, f"y kerf gap too small: {gap}"


def run_case(name: str, project: Project, expect_all_placed: bool = True) -> OptimizationResult:
    start = time.perf_counter()
    result = optimize_sheet_project(project)
    elapsed = time.perf_counter() - start
    assert elapsed < 50, f"{name} too slow: {elapsed:.2f}s"
    assert_layout_invariants(project, result, expect_all_placed=expect_all_placed)
    print(f"[OK] {name}: {elapsed:.3f}s, sheets={len(result.sheet_layouts)}, missing={len(result.missing_sheet_layouts)}")
    return result


def test_regression_cases() -> None:
    run_case("simple 500x500", make_project(2000, 1000, 1, 5, [(500, 500, 2)]))
    too_large = run_case("too large part", make_project(1000, 1000, 1, 5, [(4000, 4000, 1)]), expect_all_placed=False)
    assert too_large.unplaced_sheet_parts or not _placed_counts(too_large)
    run_case("many small parts", make_project(2000, 1000, 1, 5, [(100, 100, 100)]))
    run_case("extreme kerf", make_project(1000, 1000, 1, 100, [(200, 200, 5)]))
    run_case("sport rotation mix", make_project(2000, 1000, 2, 5, [(1200, 400, 1), (900, 500, 1), (300, 700, 1)], "sport"))


def test_previous_customer_regressions() -> None:
    filler = run_case("comfort fills free strip tails", make_project(2000, 1000, 1, 5, [(50, 50, 100), (200, 300, 15)]))
    assert _placed_counts(filler) == Counter({"50 x 50": 100, "200 x 300": 15})
    assert any(
        round(part.width) == 50 and part.y >= 900 and part.x < 1025
        for part in filler.sheet_layouts[0].parts
    )

    strip = run_case(
        "2050x3050 reusable second sheet",
        make_project(2050, 3050, 2, 5, [(1000, 1280, 3), (1000, 1450, 1), (1450, 1150, 1)]),
    )
    counts_per_sheet = [Counter(part.part.name for part in layout.parts) for layout in strip.sheet_layouts]
    assert Counter({"1000 x 1280": 3, "1000 x 1450": 1}) in counts_per_sheet
    assert Counter({"1450 x 1150": 1}) in counts_per_sheet

    column_remainder = run_case("comfort concentrates remainder in last strip", make_project(2000, 1000, 1, 5, [(100, 103, 23)]))
    strip_counts: list[int] = []
    for segment in column_remainder.sheet_layouts[0].vertical_segments:
        left = float(segment["x"])
        right = float(segment["right"])
        count = sum(1 for part in column_remainder.sheet_layouts[0].parts if left - EPS <= part.x < right - EPS)
        strip_counts.append(count)
    assert strip_counts == [9, 9, 5], strip_counts


def test_fuzz_random_inputs() -> None:
    random.seed(9000)
    for index in range(80):
        stock_width = random.randint(450, 2600)
        stock_height = random.randint(450, 3200)
        quantity = random.randint(1, 3)
        kerf = random.choice([0, 1, 3, 5, 8, 12, 25])
        groups = random.randint(1, 8)
        parts: list[tuple[float, float, int]] = []
        for _ in range(groups):
            width = random.randint(20, max(25, int(stock_width * 0.85)))
            height = random.randint(20, max(25, int(stock_height * 0.85)))
            qty = random.randint(1, 18)
            parts.append((width, height, qty))
        mode = random.choice(["comfort", "sport"])
        result = optimize_sheet_project(make_project(stock_width, stock_height, quantity, kerf, parts, mode))
        assert_layout_invariants(make_project(stock_width, stock_height, quantity, kerf, parts, mode), result)
    print("[OK] fuzz random inputs: 80 cases")


def test_performance_sanity() -> None:
    for qty in (10, 50, 100, 250, 500):
        run_case(f"performance {qty} parts", make_project(2000, 1000, 20, 5, [(50, 50, qty)]))


def _process_events(milliseconds: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()

from app.simple_window import (
    MAX_TOTAL_PARTS,
    STOCK_HEIGHT_COLUMN,
    STOCK_MATERIAL_COLUMN,
    STOCK_QUANTITY_COLUMN,
    STOCK_WIDTH_COLUMN,
    SimpleCutWindow,
)
from core.models import OptimizationResult, OptimizationSettings, Project, SheetLayout, SheetPart, SheetStock
from import_export.image_export import export_scene_png
from ui.layout_view import LayoutView
from workers.optimizer_worker import optimize_sheet_project


EPS = 0.001


def make_project(
    stock_width: float,
    stock_height: float,
    quantity: int,
    kerf: float,
    parts: list[tuple[float, float, int]],
    mode: str = "comfort",
) -> Project:
    project = Project()
    project.sheet_stock = [
        SheetStock(
            "standard",
            1,
            stock_width,
            stock_height,
            quantity,
            allow_rotation=True,
            min_offcut_width=80,
            min_offcut_height=80,
        )
    ]
    project.sheet_parts = [
        SheetPart(f"{width:g} x {height:g}", width, height, qty, "standard", 1, allow_rotation=True)
        for width, height, qty in parts
    ]
    project.settings = OptimizationSettings(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        kerf=kerf,
        margin=0,
        cutting_mode="hybrid",
        optimization_mode=mode,
        display_orientation="horizontal",
        min_reusable_offcut_size=80,
    )
    return project


def _all_layouts(result: OptimizationResult) -> list[SheetLayout]:
    return list(result.sheet_layouts) + list(result.missing_sheet_layouts)


def _requested_counts(project: Project) -> Counter[str]:
    return Counter({part.name: part.quantity for part in project.sheet_parts})


def _placed_counts(result: OptimizationResult) -> Counter[str]:
    counts: Counter[str] = Counter()
    for layout in _all_layouts(result):
        for placement in layout.parts:
            if getattr(placement.part, "is_waste_fill", False):
                continue
            counts[placement.part.name] += 1
    return counts


def assert_layout_invariants(project: Project, result: OptimizationResult, expect_all_placed: bool = False) -> None:
    stock_limit = sum(max(0, stock.quantity) for stock in project.sheet_stock)
    assert len(result.sheet_layouts) <= stock_limit
    assert math.isfinite(result.utilization)
    assert 0 <= result.utilization <= 100 + EPS
    assert result.waste >= -EPS

    requested = _requested_counts(project)
    placed = _placed_counts(result)
    for name, count in placed.items():
        assert count <= requested[name], f"{name} duplicated: {count} > {requested[name]}"
    if expect_all_placed:
        assert placed == requested
        assert not result.unplaced_sheet_parts

    for layout in _all_layouts(result):
        assert 0 <= layout.sheet_utilization <= 100 + EPS
        assert 0 <= layout.utilization <= 100 + EPS
        if layout.parts:
            assert layout.is_guillotine_feasible
            assert layout.cut_tree
        for placement in layout.parts:
            assert math.isfinite(placement.x)
            assert math.isfinite(placement.y)
            assert math.isfinite(placement.width)
            assert math.isfinite(placement.height)
            assert placement.x >= -EPS
            assert placement.y >= -EPS
            assert placement.width > 0
            assert placement.height > 0
            assert placement.x + placement.width <= layout.stock.width + EPS
            assert placement.y + placement.height <= layout.stock.height + EPS

        kerf = project.settings.kerf
        for index, first in enumerate(layout.parts):
            for second in layout.parts[index + 1 :]:
                x_overlap = first.x < second.x + second.width - EPS and second.x < first.x + first.width - EPS
                y_overlap = first.y < second.y + second.height - EPS and second.y < first.y + first.height - EPS
                assert not (x_overlap and y_overlap), f"{first.part.name} overlaps {second.part.name}"
                if y_overlap:
                    gap = max(second.x - (first.x + first.width), first.x - (second.x + second.width))
                    assert gap + EPS >= kerf, f"x kerf gap too small: {gap}"
                if x_overlap:
                    gap = max(second.y - (first.y + first.height), first.y - (second.y + second.height))
                    assert gap + EPS >= kerf, f"y kerf gap too small: {gap}"


def run_case(name: str, project: Project, expect_all_placed: bool = True) -> OptimizationResult:
    start = time.perf_counter()
    result = optimize_sheet_project(project)
    elapsed = time.perf_counter() - start
    assert elapsed < 50, f"{name} too slow: {elapsed:.2f}s"
    assert_layout_invariants(project, result, expect_all_placed=expect_all_placed)
    print(f"[OK] {name}: {elapsed:.3f}s, sheets={len(result.sheet_layouts)}, missing={len(result.missing_sheet_layouts)}")
    return result


def test_regression_cases() -> None:
    run_case("simple 500x500", make_project(2000, 1000, 1, 5, [(500, 500, 2)]))
    too_large = run_case("too large part", make_project(1000, 1000, 1, 5, [(4000, 4000, 1)]), expect_all_placed=False)
    assert too_large.unplaced_sheet_parts or not _placed_counts(too_large)
    run_case("many small parts", make_project(2000, 1000, 1, 5, [(100, 100, 100)]))
    run_case("extreme kerf", make_project(1000, 1000, 1, 100, [(200, 200, 5)]))
    run_case("sport rotation mix", make_project(2000, 1000, 2, 5, [(1200, 400, 1), (900, 500, 1), (300, 700, 1)], "sport"))


def test_previous_customer_regressions() -> None:
    filler = run_case("comfort fills free strip tails", make_project(2000, 1000, 1, 5, [(50, 50, 100), (200, 300, 15)]))
    assert _placed_counts(filler) == Counter({"50 x 50": 100, "200 x 300": 15})
    assert any(
        round(part.width) == 50 and part.y >= 900 and part.x < 1025
        for part in filler.sheet_layouts[0].parts
    )

    strip = run_case(
        "2050x3050 reusable second sheet",
        make_project(2050, 3050, 2, 5, [(1000, 1280, 3), (1000, 1450, 1), (1450, 1150, 1)]),
    )
    counts_per_sheet = [Counter(part.part.name for part in layout.parts) for layout in strip.sheet_layouts]
    assert Counter({"1000 x 1280": 3, "1000 x 1450": 1}) in counts_per_sheet
    assert Counter({"1450 x 1150": 1}) in counts_per_sheet

    column_remainder = run_case("comfort concentrates remainder in last strip", make_project(2000, 1000, 1, 5, [(100, 103, 23)]))
    strip_counts: list[int] = []
    for segment in column_remainder.sheet_layouts[0].vertical_segments:
        left = float(segment["x"])
        right = float(segment["right"])
        count = sum(1 for part in column_remainder.sheet_layouts[0].parts if left - EPS <= part.x < right - EPS)
        strip_counts.append(count)
    assert strip_counts == [9, 9, 5], strip_counts


def test_fuzz_random_inputs() -> None:
    random.seed(9000)
    for index in range(80):
        stock_width = random.randint(450, 2600)
        stock_height = random.randint(450, 3200)
        quantity = random.randint(1, 3)
        kerf = random.choice([0, 1, 3, 5, 8, 12, 25])
        groups = random.randint(1, 8)
        parts: list[tuple[float, float, int]] = []
        for _ in range(groups):
            width = random.randint(20, max(25, int(stock_width * 0.85)))
            height = random.randint(20, max(25, int(stock_height * 0.85)))
            qty = random.randint(1, 18)
            parts.append((width, height, qty))
        mode = random.choice(["comfort", "sport"])
        result = optimize_sheet_project(make_project(stock_width, stock_height, quantity, kerf, parts, mode))
        assert_layout_invariants(make_project(stock_width, stock_height, quantity, kerf, parts, mode), result)
    print("[OK] fuzz random inputs: 80 cases")


def test_performance_sanity() -> None:
    for qty in (10, 50, 100, 250, 500):
        run_case(f"performance {qty} parts", make_project(2000, 1000, 20, 5, [(50, 50, qty)]))


def _process_events(milliseconds: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def test_ui_validation_loader_and_export() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    # The loading-overlay check needs a valid job. An empty default row opens a
    # modal validation message in offscreen Qt and cannot exercise the worker.
    window.parts.setRowCount(0)
    window.add_part_row(["500", "500", 1])
    window.calculate()
    app.processEvents()
    assert window._is_calculating or window.last_result is not None
    _process_events(8000)
    app.processEvents()
    assert not window._is_calculating
    assert window.last_result is not None
    
    assert window.stock_table.cellWidget(0, STOCK_MATERIAL_COLUMN) is not None
    assert window.stock_table.editTriggers() != QTableWidget.EditTrigger.NoEditTriggers
    for column in (STOCK_HEIGHT_COLUMN, STOCK_WIDTH_COLUMN, STOCK_QUANTITY_COLUMN):
        assert window.stock_table.cellWidget(0, column) is None
        item = window.stock_table.item(0, column)
        assert item is not None
        assert item.flags() & Qt.ItemFlag.ItemIsEditable

    window.stock_table.item(0, STOCK_HEIGHT_COLUMN).setText("")
    try:
        window._collect_stock()
    except ValueError as exc:
        assert "płyt" in str(exc).lower()
    else:
        raise AssertionError("blank stock width should be rejected calmly")

    window.parts.setRowCount(0)
    try:
        window._collect_parts()
    except ValueError as exc:
        assert "format" in str(exc).lower()
    else:
        raise AssertionError("empty parts should be rejected calmly")

    window.add_part_row(["nan", "50", 1])
    try:
        window._collect_parts()
    except ValueError as exc:
        assert "poprawne liczby" in str(exc).lower()
    else:
        raise AssertionError("NaN width should be rejected")

    window.parts.setRowCount(0)
    window.add_part_row(["50", "50", "1,5"])
    try:
        window._collect_parts()
    except ValueError:
        pass
    else:
        raise AssertionError("decimal quantity should be rejected")

    window.parts.setRowCount(0)
    window.add_part_row(["50", "50", MAX_TOTAL_PARTS + 1])
    # Keep this smoke check non-interactive: the real UI asks for the PIN.
    window._authorize_part_limit_override = lambda _total: False
    try:
        window._collect_parts()
    except ValueError as exc:
        message = str(exc).lower()
        assert "15 000" in message and "format" in message
    else:
        raise AssertionError("excessive quantity should be rejected")

    window._part_limit_override_authorized = True
    assert window._collect_parts()[0].quantity == MAX_TOTAL_PARTS + 1

    window.parts.setRowCount(0)
    for row_values in (["500", "500", 2], ["200", "300", 5]):
        window.add_part_row(row_values)
    window._set_stock_cell_text(0, 1, "2000")
    window._set_stock_cell_text(0, 2, "1000")
    window._set_stock_cell_text(0, 3, "1")

    window._set_theme_combo("light")
    window.apply_selected_theme(0)
    window._set_theme_combo("dark")
    window.apply_selected_theme(0)
    assert window.current_theme() == "dark"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "export.png"
        current_theme = window.layout_view.theme
        current_print_mode = window.layout_view.print_mode
        try:
            window.layout_view.set_theme("light")
            window.layout_view.set_print_mode(True)
            window.layout_view.show_result(window.last_result)
            export_scene_png(window.layout_view.scene, str(path), background="#ffffff")
        finally:
            window.layout_view.set_print_mode(current_print_mode)
            window.layout_view.set_theme(current_theme)
            window.layout_view.show_result(window.last_result)
        assert path.exists()
        assert path.stat().st_size > 10_000
        image = QImage(str(path))
        assert not image.isNull()
        assert image.height() > 126

    view = LayoutView()
    view.resize(320, 220)
    view.show_result(window.last_result)
    app.processEvents()
    assert view.scene.items()
    window.close()
    view.deleteLater()
    window.deleteLater()


def main() -> None:
    tests = [
        test_regression_cases,
        test_previous_customer_regressions,
        test_fuzz_random_inputs,
        test_performance_sanity,
        test_ui_validation_loader_and_export,
    ]
    for test in tests:
        test()
    print("STABILITY SMOKE: OK")


if __name__ == "__main__":
    main()

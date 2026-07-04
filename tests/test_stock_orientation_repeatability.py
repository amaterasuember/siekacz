from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_project


def _placed_count(result) -> int:
    return sum(len(layout.parts) for layout in result.sheet_layouts)


def _assert_no_overlaps(layout, kerf: float) -> None:
    for index, first in enumerate(layout.parts):
        for second in layout.parts[index + 1 :]:
            separated = (
                first.x + first.width + kerf <= second.x + 0.001
                or second.x + second.width + kerf <= first.x + 0.001
                or first.y + first.height + kerf <= second.y + 0.001
                or second.y + second.height + kerf <= first.y + 0.001
            )
            assert separated, (first, second)


def _run_direct(width: float, height: float):
    return optimize_2d_vertical_segmented(
        [SheetStock("standard", 1, width, height, 1, allow_rotation=True)],
        [SheetPart("A", 40, 900, 55, "standard", 1, allow_rotation=True)],
        kerf=5,
        margin=0,
        optimization_mode="comfort",
    )


def test_stock_width_height_swap_produces_repeatable_cut_count() -> None:
    normal = _run_direct(2000, 1000)
    swapped = _run_direct(1000, 2000)

    assert _placed_count(normal) == _placed_count(swapped) == 48
    assert len(normal.unplaced_sheet_parts) == len(swapped.unplaced_sheet_parts) == 7
    assert len(normal.sheet_layouts) == len(swapped.sheet_layouts) == 1

    normal_layout = normal.sheet_layouts[0]
    swapped_layout = swapped.sheet_layouts[0]
    assert (round(normal_layout.stock.width), round(normal_layout.stock.height)) == (2000, 1000)
    assert (round(swapped_layout.stock.width), round(swapped_layout.stock.height)) == (2000, 1000)
    assert round(normal_layout.used_width, 3) == round(swapped_layout.used_width, 3)
    assert round(normal_layout.used_height, 3) == round(swapped_layout.used_height, 3)
    assert normal_layout.is_guillotine_feasible
    assert swapped_layout.is_guillotine_feasible
    _assert_no_overlaps(normal_layout, 5)
    _assert_no_overlaps(swapped_layout, 5)


def test_missing_sheets_use_same_deterministic_orientation_logic() -> None:
    for width, height in ((2000, 1000), (1000, 2000)):
        project = Project(
            sheet_stock=[SheetStock("standard", 1, width, height, 1, allow_rotation=True)],
            sheet_parts=[SheetPart("A", 40, 900, 55, "standard", 1, allow_rotation=True)],
            settings=OptimizationSettings(kerf=5, optimization_mode="comfort"),
        )
        result = optimize_sheet_project(project)

        assert _placed_count(result) == 48
        # `unplaced_sheet_parts` only counts parts that DIDN'T fit even on
        # virtual missing sheets — the 7 leftover 40×900 parts fit on the
        # missing sheet, so they shouldn't appear here anymore.
        assert len(result.unplaced_sheet_parts) == 0
        assert len(result.missing_sheet_layouts) == 1
        missing = result.missing_sheet_layouts[0]
        assert (round(missing.stock.width), round(missing.stock.height)) == (2000, 1000)
        assert len(missing.parts) == 7
        assert missing.is_guillotine_feasible
        _assert_no_overlaps(missing, 5.2)


if __name__ == "__main__":
    test_stock_width_height_swap_produces_repeatable_cut_count()
    test_missing_sheets_use_same_deterministic_orientation_logic()
    print("stock orientation repeatability test: OK")

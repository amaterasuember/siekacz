from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import SheetPart, SheetStock


def test_comfort_and_sport_compact_rotated_long_parts_across_sheets() -> None:
    stock = [SheetStock("standard", 1, 2000, 1000, 2, allow_rotation=True)]
    parts = [SheetPart("50 x 900", 50, 900, 55, "standard", 1, allow_rotation=True)]

    comfort = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, optimization_mode="comfort")
    sport = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, optimization_mode="sport")

    for result in (comfort, sport):
        assert not result.unplaced_sheet_parts
        assert len(result.sheet_layouts) == 2
        assert any(part.rotated for part in result.sheet_layouts[0].parts)
        assert all(layout.is_guillotine_feasible for layout in result.sheet_layouts)

        second_sheet = result.sheet_layouts[1]
        assert second_sheet.stock.width == 2000
        assert second_sheet.stock.height == 1000
        assert second_sheet.parts
        # The optimizer may choose a uniform second board instead of the old
        # bottom-strip picture when it saves more material across both boards.
        assert second_sheet.used_width <= 1050
        assert second_sheet.used_height <= 1000
        assert not any(part.rotated for part in second_sheet.parts)
        assert sum(layout.used_width for layout in result.sheet_layouts) <= 2850


def test_comfort_can_rotate_stock_orientation_when_it_reduces_sheet_count() -> None:
    stock = [SheetStock("standard", 1, 2000, 1000, 2, allow_rotation=True)]
    parts = [
        SheetPart("A", 600, 850, 3, "standard", 1, allow_rotation=True),
        SheetPart("B", 100, 500, 3, "standard", 1, allow_rotation=True),
    ]

    comfort = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, optimization_mode="comfort")
    sport = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, optimization_mode="sport")

    assert not comfort.unplaced_sheet_parts
    assert len(comfort.sheet_layouts) == 1
    assert len(comfort.sheet_layouts[0].parts) == 6
    assert comfort.sheet_layouts[0].is_guillotine_feasible

    assert not sport.unplaced_sheet_parts
    assert len(sport.sheet_layouts) == 1
    assert sport.sheet_layouts[0].is_guillotine_feasible


if __name__ == "__main__":
    test_comfort_and_sport_compact_rotated_long_parts_across_sheets()
    test_comfort_can_rotate_stock_orientation_when_it_reduces_sheet_count()
    print("comfort/sport bottom strip rotation test: OK")

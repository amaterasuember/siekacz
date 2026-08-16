from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import SheetPart, SheetStock


def _no_overlaps(layout) -> bool:
    for index, first in enumerate(layout.parts):
        for second in layout.parts[index + 1 :]:
            x_overlap = first.x < second.x + second.width - 0.001 and second.x < first.x + first.width - 0.001
            y_overlap = first.y < second.y + second.height - 0.001 and second.y < first.y + first.height - 0.001
            if x_overlap and y_overlap:
                return False
    return True


def test_hybrid_keeps_2050_case_as_two_guillotine_strip_sheets() -> None:
    stock = [SheetStock("standard", 1, 2050, 3050, 2, allow_rotation=True, min_offcut_width=200, min_offcut_height=200)]
    parts = [
        SheetPart("1000 x 1280", 1000, 1280, 3, "standard", 1, allow_rotation=True),
        SheetPart("1000 x 1450", 1000, 1450, 1, "standard", 1, allow_rotation=True),
        SheetPart("1450 x 1150", 1450, 1150, 1, "standard", 1, allow_rotation=True),
    ]

    result = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, min_reusable_size=200, cutting_mode="hybrid")

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 2
    assert all(layout.is_guillotine_feasible for layout in result.sheet_layouts)
    assert all(layout.cut_tree for layout in result.sheet_layouts)
    assert all(layout.cut_operations for layout in result.sheet_layouts)
    assert all(_no_overlaps(layout) for layout in result.sheet_layouts)

    first = result.sheet_layouts[0]
    first_counts = {}
    for placement in first.parts:
        first_counts[placement.part.name] = first_counts.get(placement.part.name, 0) + 1
    assert first_counts == {"1000 x 1280": 3, "1000 x 1450": 1}
    assert first.strip_count == 2

    second = result.sheet_layouts[1]
    assert len(second.parts) == 1
    assert second.parts[0].part.name == "1450 x 1150"
    assert second.largest_reusable_offcut_area > 3_500_000


def test_hybrid_repeated_small_parts_are_exported_as_strip_plan_not_free_tetris() -> None:
    stock = [SheetStock("standard", 1, 1973, 1015, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)]
    parts = [
        SheetPart("80 x 252", 80, 252, 21, "standard", 1, allow_rotation=True),
        SheetPart("80 x 150", 80, 150, 45, "standard", 1, allow_rotation=True),
        SheetPart("150 x 80", 150, 80, 9, "standard", 1, allow_rotation=True),
        SheetPart("120 x 57", 120, 57, 5, "standard", 1, allow_rotation=True),
        SheetPart("57 x 120", 57, 120, 30, "standard", 1, allow_rotation=True),
        SheetPart("50 x 50", 50, 50, 48, "standard", 1, allow_rotation=True),
        SheetPart("40 x 120", 40, 120, 18, "standard", 1, allow_rotation=True),
    ]

    result = optimize_2d_vertical_segmented(stock, parts, kerf=3, margin=0, min_reusable_size=80, cutting_mode="hybrid")

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 1
    layout = result.sheet_layouts[0]
    assert layout.is_guillotine_feasible
    assert layout.cut_tree
    assert layout.cut_operations
    assert _no_overlaps(layout)
    widths = [round(segment["width"]) for segment in layout.vertical_segments]
    # The exact strip widths are heuristic-dependent; the production contract is
    # a complete, gilotynowy plan made of several readable strips, not a frozen
    # sequence of 80 mm columns.
    assert len(widths) >= 4
    assert all(width > 0 for width in widths)


if __name__ == "__main__":
    test_hybrid_keeps_2050_case_as_two_guillotine_strip_sheets()
    test_hybrid_repeated_small_parts_are_exported_as_strip_plan_not_free_tetris()
    print("test_guillotine_technology: OK")

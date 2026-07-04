from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import SheetPart, SheetStock


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


def test_repeated_parts_can_mix_rotated_and_original_orientation_for_compact_bbox() -> None:
    stock = [SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True, min_offcut_width=100, min_offcut_height=100)]
    parts = [
        SheetPart("A", 100, 600, 10, "standard", 1, allow_rotation=True),
        SheetPart("B", 100, 100, 10, "standard", 1, allow_rotation=True),
    ]

    result = optimize_2d_vertical_segmented(
        stock,
        parts,
        kerf=5,
        margin=0,
        min_reusable_size=100,
        cutting_mode="hybrid",
        optimization_mode="comfort",
    )

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 1
    layout = result.sheet_layouts[0]
    a_parts = [placement for placement in layout.parts if placement.part.name == "A"]

    assert any(placement.rotated for placement in a_parts)
    assert any(not placement.rotated for placement in a_parts)
    assert layout.used_width <= 850
    assert layout.used_width < 1045
    assert layout.used_height <= 1000
    assert layout.is_guillotine_feasible
    assert layout.cut_tree
    _assert_no_overlaps(layout, kerf=5)


if __name__ == "__main__":
    test_repeated_parts_can_mix_rotated_and_original_orientation_for_compact_bbox()
    print("mixed orientation compaction test: OK")

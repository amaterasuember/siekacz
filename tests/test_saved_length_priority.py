from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import _saved_used_length, optimize_2d_vertical_segmented
from core.models import SheetPart, SheetStock


def test_long_board_uses_the_short_side_before_consuming_its_length() -> None:
    stock = [SheetStock("POM C PŁYTA NATURALNA", 45, 2300, 1000, 2, allow_rotation=True)]
    parts = [SheetPart("B", 122, 11, 122, "POM C PŁYTA NATURALNA", 45, allow_rotation=True)]

    result = optimize_2d_vertical_segmented(
        stock,
        parts,
        kerf=5.5,
        margin=0,
        min_reusable_size=200,
        optimization_mode="comfort",
    )

    assert len(result.sheet_layouts) == 1
    assert not result.unplaced_sheet_parts
    layout = result.sheet_layouts[0]
    assert layout.is_guillotine_feasible
    # 122 x 11 elements should occupy the 1000 mm side first and consume only
    # a short strip of the 2300 mm board, not run along its whole length.
    assert _saved_used_length(layout) < 400
    assert layout.used_height > 800


if __name__ == "__main__":
    test_long_board_uses_the_short_side_before_consuming_its_length()
    print("test_saved_length_priority: OK")

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import SheetPart, SheetStock


def _small_repeat_case() -> tuple[list[SheetStock], list[SheetPart]]:
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
    return stock, parts


def test_comfort_and_sport_are_separate_feasible_modes() -> None:
    stock, parts = _small_repeat_case()
    comfort = optimize_2d_vertical_segmented(
        stock,
        parts,
        kerf=3,
        margin=0,
        min_reusable_size=80,
        cutting_mode="hybrid",
        optimization_mode="comfort",
    )
    sport = optimize_2d_vertical_segmented(
        stock,
        parts,
        kerf=3,
        margin=0,
        min_reusable_size=80,
        cutting_mode="hybrid",
        optimization_mode="sport",
    )

    assert comfort.algorithm.endswith("COMFORT")
    assert sport.algorithm.endswith("SPORT")
    assert all(layout.is_guillotine_feasible for layout in comfort.sheet_layouts)
    assert all(layout.is_guillotine_feasible for layout in sport.sheet_layouts)
    assert all(layout.cut_tree for layout in comfort.sheet_layouts)
    assert all(layout.cut_tree for layout in sport.sheet_layouts)
    assert len(sport.sheet_layouts) <= len(comfort.sheet_layouts)


if __name__ == "__main__":
    test_comfort_and_sport_are_separate_feasible_modes()
    print("test_comfort_sport_modes: OK")

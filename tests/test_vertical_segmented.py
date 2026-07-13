from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import SheetPart, SheetStock


def _crossings(layout) -> list[str]:
    boundaries = sorted({segment["x"] for segment in layout.vertical_segments} | {segment["right"] for segment in layout.vertical_segments})
    errors: list[str] = []
    for placement in layout.parts:
        left = placement.x
        right = placement.x + placement.width
        for boundary in boundaries:
            if left + 0.001 < boundary < right - 0.001:
                errors.append(f"{placement.part.name} crosses {boundary}")
    return errors


def test_acceptance_case() -> None:
    stock = [
        SheetStock(
            material="standard",
            thickness=1,
            width=2000,
            height=1000,
            quantity=1,
            allow_rotation=True,
            min_offcut_width=50,
            min_offcut_height=50,
        )
    ]
    parts = [
        SheetPart("200 x 700", 200, 700, 3, "standard", 1, allow_rotation=True),
        SheetPart("90 x 150", 90, 150, 3, "standard", 1, allow_rotation=True),
        SheetPart("50 x 50", 50, 50, 200, "standard", 1, allow_rotation=True),
    ]

    result = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0)

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 1
    layout = result.sheet_layouts[0]
    assert layout.vertical_segments
    assert all(segment["width"] > 0 for segment in layout.vertical_segments)
    assert layout.used_width <= 1200
    assert layout.is_guillotine_feasible
    assert not _crossings(layout)

    rotated_medium_parts = [
        placement
        for placement in layout.parts
        if placement.part.name == "90 x 150" and round(placement.width) == 150 and round(placement.height) == 90
    ]
    assert len(rotated_medium_parts) == 3

    for message in result.messages:
        print(message)


if __name__ == "__main__":
    test_acceptance_case()
    print("vertical_segmented acceptance test: OK")

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import (
    _canonicalize_stock_orientation,
    _saved_axis,
    _saved_used_length,
    _stock_orientation_sets,
    optimize_2d_vertical_segmented,
)
from core.models import Project, SheetPart, SheetStock


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


def test_selected_cut_side_becomes_the_vertical_rip_direction() -> None:
    stock = SheetStock(
        "POM C",
        10,
        610,
        1000,
        1,
        allow_rotation=True,
        preferred_cut_axis="x",
    )
    oriented = _canonicalize_stock_orientation(stock)
    assert (oriented.width, oriented.height) == (1000, 610)
    assert oriented.preferred_cut_axis == "y"
    assert _saved_axis(oriented) == "x"
    orientation_sets = _stock_orientation_sets([stock])
    assert len(orientation_sets) == 1
    assert orientation_sets[0][0] == "stock selected cut direction"


def test_selected_cut_side_survives_project_serialization() -> None:
    project = Project(
        sheet_stock=[
            SheetStock("POM C", 10, 610, 1000, 1, preferred_cut_axis="x")
        ]
    )
    restored = Project.from_dict(project.to_dict())
    assert restored.sheet_stock[0].preferred_cut_axis == "x"


if __name__ == "__main__":
    test_long_board_uses_the_short_side_before_consuming_its_length()
    test_selected_cut_side_becomes_the_vertical_rip_direction()
    test_selected_cut_side_survives_project_serialization()
    print("test_saved_length_priority: OK")

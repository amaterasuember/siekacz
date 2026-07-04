"""Regression: stock orientation must be deterministic for mixed parts.

The old behaviour was that typing (2000, 1000) vs (1000, 2000) for the same
physical sheet produced *different* layouts because the candidate generator
`_build_result` consumed the raw stock list, not the canonical-orientation
view. Fixed by canonicalizing the stock list at the top of
optimize_2d_vertical_segmented.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_project


def _mixed_parts() -> list[SheetPart]:
    """Exact user-reported case from screenshots."""
    return [
        SheetPart("A_10x10", 10, 10, 30, "standard", 1, allow_rotation=True),
        SheetPart("B_20x30", 20, 30, 200, "standard", 1, allow_rotation=True),
        SheetPart("C_100x100", 100, 100, 15, "standard", 1, allow_rotation=True),
        SheetPart("D_40x900", 40, 900, 55, "standard", 1, allow_rotation=True),
    ]


def _run_direct(width: float, height: float):
    return optimize_2d_vertical_segmented(
        [SheetStock("standard", 1, width, height, 1, allow_rotation=True,
                    min_offcut_width=80, min_offcut_height=80)],
        _mixed_parts(),
        kerf=5, margin=0, min_reusable_size=80,
        optimization_mode="comfort",
    )


def _run_worker(width: float, height: float):
    project = Project()
    project.sheet_stock = [SheetStock("standard", 1, width, height, 1,
                                       allow_rotation=True, min_offcut_width=80,
                                       min_offcut_height=80)]
    project.sheet_parts = _mixed_parts()
    project.settings = OptimizationSettings(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        kerf=5.0, margin=0,
        cutting_mode="hybrid", optimization_mode="comfort",
        display_orientation="horizontal", min_reusable_offcut_size=80,
    )
    return optimize_sheet_project(project)


def _counts(result) -> Counter:
    total = Counter()
    for layout in list(result.sheet_layouts) + list(result.missing_sheet_layouts):
        for placement in layout.parts:
            total[placement.part.name] += 1
    return total


def test_direct_repeatable_for_mixed_parts() -> None:
    normal = _run_direct(2000, 1000)
    swapped = _run_direct(1000, 2000)

    assert _counts(normal) == _counts(swapped), \
        f"placement counts differ: {_counts(normal)} vs {_counts(swapped)}"
    assert len(normal.sheet_layouts) == len(swapped.sheet_layouts)
    assert len(normal.unplaced_sheet_parts) == len(swapped.unplaced_sheet_parts)

    n_layout = normal.sheet_layouts[0]
    s_layout = swapped.sheet_layouts[0]
    # After canonicalization both should report the canonical stock (long side as width).
    assert (round(n_layout.stock.width), round(n_layout.stock.height)) == (2000, 1000)
    assert (round(s_layout.stock.width), round(s_layout.stock.height)) == (2000, 1000)
    assert round(n_layout.used_width, 1) == round(s_layout.used_width, 1)
    assert round(n_layout.used_height, 1) == round(s_layout.used_height, 1)
    assert round(n_layout.utilization, 1) == round(s_layout.utilization, 1)
    print(f"[OK] direct repeatable: {_counts(normal)}, util={n_layout.utilization:.1f}%")


def test_worker_repeatable_for_mixed_parts() -> None:
    normal = _run_worker(2000, 1000)
    swapped = _run_worker(1000, 2000)

    assert _counts(normal) == _counts(swapped), \
        f"placement counts differ: {_counts(normal)} vs {_counts(swapped)}"
    assert len(normal.sheet_layouts) == len(swapped.sheet_layouts)
    assert len(normal.missing_sheet_layouts) == len(swapped.missing_sheet_layouts)
    assert len(normal.unplaced_sheet_parts) == len(swapped.unplaced_sheet_parts)

    # Verify the visible (main) layout is canonical.
    n_main = normal.sheet_layouts[0]
    s_main = swapped.sheet_layouts[0]
    assert (round(n_main.stock.width), round(n_main.stock.height)) == (2000, 1000)
    assert (round(s_main.stock.width), round(s_main.stock.height)) == (2000, 1000)
    assert round(n_main.utilization, 1) == round(s_main.utilization, 1)
    print(f"[OK] worker repeatable: {_counts(normal)}, util={n_main.utilization:.1f}%")


if __name__ == "__main__":
    test_direct_repeatable_for_mixed_parts()
    test_worker_repeatable_for_mixed_parts()
    print("\ntest_stock_orientation_mixed_parts: OK")

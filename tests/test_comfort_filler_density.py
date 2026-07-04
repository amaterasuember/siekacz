"""Regression: comfort mode must not pick a badly under-filled overflow sheet.

Reproduces a real report where 55× 40×900 + 60× 20×30 + 30× 200×300 on a single
2000×1000 sheet produced a second (missing) sheet at only 38% utilisation — the
200×300 panels were spread into a single row while 50 tiny 20×30 fillers were
allowed to claim the prime area below them, pushing the rest of the panels onto a
third sheet.

Root cause: comfort selection hard-restricted the candidate pool to clean
"strip" layouts, discarding a far denser feasible candidate (the one sport mode
picked, ~88%).  The fix lets comfort fall back to the denser candidate when it is
clearly better on the dominant objectives.  This test pins that behaviour.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import Counter

from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_project


def _project() -> Project:
    project = Project()
    project.sheet_stock = [
        SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True,
                   min_offcut_width=80, min_offcut_height=80)
    ]
    project.sheet_parts = [
        SheetPart("A", 40, 900, 55, "standard", 1, allow_rotation=True),
        SheetPart("B", 20, 30, 60, "standard", 1, allow_rotation=True),
        SheetPart("C", 200, 300, 30, "standard", 1, allow_rotation=True),
    ]
    project.settings = OptimizationSettings(
        job_type="sheet", algorithm="Vertical Segmented Guillotine", kerf=5,
        cutting_mode="hybrid", optimization_mode="comfort",
        display_orientation="horizontal", min_reusable_offcut_size=80,
    )
    return project


def test_comfort_does_not_strand_a_near_empty_sheet() -> None:
    result = optimize_sheet_project(_project())
    all_layouts = list(result.sheet_layouts) + list(result.missing_sheet_layouts)
    used = [lay for lay in all_layouts if lay.parts]
    assert used, "expected at least one populated sheet"

    # Every populated sheet must be reasonably filled — the bug produced a 38%
    # sheet.  75% is comfortably above the old pathology yet below what the fix
    # actually achieves (~87-89%), so it is a stable guardrail.
    worst = min(lay.utilization for lay in used)
    assert worst >= 75.0, (
        "comfort produced an under-filled sheet: "
        + ", ".join(f"{lay.utilization:.1f}%" for lay in used)
    )

    # Every populated sheet must remain guillotine-feasible.
    for lay in used:
        assert lay.is_guillotine_feasible, "winning layout is not guillotine-feasible"

    # All required parts must be placed (none stranded as unplaced).
    assert not result.unplaced_sheet_parts, result.unplaced_sheet_parts
    placed = Counter()
    for lay in all_layouts:
        for p in lay.parts:
            placed[p.part.name] += 1
    assert placed["A"] == 55 and placed["B"] == 60 and placed["C"] == 30, placed
    print("[OK] comfort packs densely (worst sheet "
          f"{worst:.1f}%), no near-empty overflow sheet")


if __name__ == "__main__":
    test_comfort_does_not_strand_a_near_empty_sheet()
    print("\ntest_comfort_filler_density: OK")

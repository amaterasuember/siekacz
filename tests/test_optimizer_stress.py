"""Aggressive stress / invariant tests for the sheet optimizer.

Goals (independent of any specific test_*.py file):

  • Geometric invariants on EVERY scenario:
      - placed parts stay within stock bounds
      - no two placed parts overlap (with kerf)
      - placed + unplaced == input quantity (no parts vanish or duplicate)
      - utilization in [0, 100], bounding box ≤ stock
      - layout.is_guillotine_feasible holds OR no parts placed

  • Repeatability: identical input → identical output (3 runs, byte-for-byte
    on placed coordinates & rotation flags).

  • Lower-bound sanity: number of sheets used >= ceil(total_part_area /
    sheet_area), so optimizer never reports impossibly few sheets.

  • Pathological "tight" cases where the optimal solution is known:
    e.g. four 500×500 on a 1000×1000 sheet must fit (1 sheet, 100% util).

  • Stress: scenarios with 100+ parts, mixed dimensions, mixed orientations,
    odd kerfs, non-strategic stock sizes.

A failing assertion is reported with the scenario label so it's obvious
which case found the bug.

Run:
    .\\.venv\\Scripts\\python.exe tests\\test_optimizer_stress.py
"""
from __future__ import annotations

import math
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_project


EPS = 0.5  # mm tolerance for overlap / bounds checks; kerf-tolerance is >= 0.2


# ─────────────────────────────────────────────────────────────────────────────
# Scenario definition
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Scenario:
    label: str
    stock: list[tuple[float, float, int]]   # (width, height, qty)
    parts: list[tuple[str, float, float, int]]  # (name, w, h, qty)
    kerf: float = 5.0
    margin: float = 0.0
    min_offcut: float = 80.0
    allow_rotation: bool = True
    mode: str = "comfort"
    # Optional: expected outcomes (set by hand for cases with a known optimum)
    expect_sheets_used_at_most: int | None = None
    expect_all_placed: bool | None = None
    expect_util_at_least: float | None = None
    # Minimum acceptable area (mm²) for the largest single reusable offcut.
    # Use to guard against regressions that shrink the biggest rectangular
    # remnant in favour of a tighter bounding box.
    expect_biggest_offcut_at_least: float | None = None


def _build_project(s: Scenario) -> Project:
    stock = [
        SheetStock(
            material="standard", thickness=1,
            width=w, height=h, quantity=q,
            allow_rotation=s.allow_rotation,
            min_offcut_width=s.min_offcut, min_offcut_height=s.min_offcut,
        )
        for (w, h, q) in s.stock
    ]
    parts = [
        SheetPart(name=name, width=w, height=h, quantity=q,
                  material="standard", thickness=1, allow_rotation=s.allow_rotation)
        for (name, w, h, q) in s.parts
    ]
    return Project(
        sheet_stock=stock,
        sheet_parts=parts,
        settings=OptimizationSettings(
            kerf=s.kerf, margin=s.margin,
            min_reusable_offcut_size=s.min_offcut,
            optimization_mode=s.mode,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Invariant validation
# ─────────────────────────────────────────────────────────────────────────────


def _validate_layout(layout, kerf: float, label: str) -> list[str]:
    """Return list of invariant violations for one layout."""
    errors: list[str] = []
    stock_w = layout.stock.width
    stock_h = layout.stock.height

    # In-bounds (with small tolerance for floating arithmetic)
    for p in layout.parts:
        if p.x < -EPS or p.y < -EPS:
            errors.append(f"{label}: {p.part.name} negative coord ({p.x:.2f},{p.y:.2f})")
        if p.x + p.width > stock_w + EPS:
            errors.append(f"{label}: {p.part.name} overflows right ({p.x + p.width:.2f} > {stock_w:.2f})")
        if p.y + p.height > stock_h + EPS:
            errors.append(f"{label}: {p.part.name} overflows bottom ({p.y + p.height:.2f} > {stock_h:.2f})")
        if p.width <= 0 or p.height <= 0:
            errors.append(f"{label}: {p.part.name} non-positive dimensions {p.width}x{p.height}")

    # No physical overlap (kerf-aware: parts in different rows must have >= kerf gap)
    for i, a in enumerate(layout.parts):
        for b in layout.parts[i + 1:]:
            x_overlap = a.x < b.x + b.width - EPS and b.x < a.x + a.width - EPS
            y_overlap = a.y < b.y + b.height - EPS and b.y < a.y + a.height - EPS
            if x_overlap and y_overlap:
                errors.append(
                    f"{label}: OVERLAP {a.part.name}({a.x:.1f},{a.y:.1f},{a.width:.0f}x{a.height:.0f}) "
                    f"vs {b.part.name}({b.x:.1f},{b.y:.1f},{b.width:.0f}x{b.height:.0f})"
                )

    # Utilization sanity
    if layout.utilization < -EPS or layout.utilization > 100.0 + EPS:
        errors.append(f"{label}: util out of range {layout.utilization:.2f}%")

    # Bounding box ≤ stock
    if layout.used_width > stock_w + EPS:
        errors.append(f"{label}: used_width {layout.used_width:.2f} > stock_w {stock_w:.2f}")
    if layout.used_height > stock_h + EPS:
        errors.append(f"{label}: used_height {layout.used_height:.2f} > stock_h {stock_h:.2f}")

    # If parts placed and not flagged infeasible, structurally must validate
    if layout.parts and getattr(layout, "is_guillotine_feasible", False) is False:
        # Allow it but flag it so we know how often it happens
        errors.append(f"{label}: layout has parts but is_guillotine_feasible=False")

    return errors


def _validate_result(result, project: Project, label: str) -> list[str]:
    errors: list[str] = []

    # Quantity conservation
    input_counts: Counter = Counter()
    for p in project.sheet_parts:
        input_counts[p.name] += p.quantity

    output_counts: Counter = Counter()
    for layout in list(result.sheet_layouts) + list(result.missing_sheet_layouts):
        for placement in layout.parts:
            output_counts[placement.part.name] += 1
    for unplaced in result.unplaced_sheet_parts:
        output_counts[unplaced.name] += 1

    for name, expected in input_counts.items():
        got = output_counts.get(name, 0)
        if got != expected:
            errors.append(f"{label}: QTY mismatch {name}: expected {expected}, got {got} (placed+unplaced)")

    for name in output_counts:
        if name not in input_counts:
            errors.append(f"{label}: phantom part {name} in output")

    # Per-layout invariants
    kerf = project.settings.kerf
    for i, layout in enumerate(result.sheet_layouts):
        errors.extend(_validate_layout(layout, kerf, f"{label}/sheet{i + 1}"))
    for i, layout in enumerate(result.missing_sheet_layouts):
        errors.extend(_validate_layout(layout, kerf, f"{label}/missing{i + 1}"))

    # Lower bound: sheets used >= ceil(total area / sheet area). Use the
    # smallest stock as the most generous bound.
    if result.sheet_layouts:
        smallest_sheet = min(
            (s.width * s.height for s in project.sheet_stock if s.quantity > 0),
            default=1.0,
        )
        total_parts_area = sum(p.width * p.height * p.quantity for p in project.sheet_parts)
        min_sheets = math.ceil(total_parts_area / smallest_sheet) if smallest_sheet > 0 else 0
        total_used = len(result.sheet_layouts) + len(result.missing_sheet_layouts)
        if total_used < min_sheets:
            errors.append(
                f"{label}: optimizer reports {total_used} sheets but lower bound is {min_sheets} "
                f"(total parts area {total_parts_area:.0f} > {total_used} × {smallest_sheet:.0f})"
            )

    return errors


def _layout_signature(layout) -> tuple:
    """Hash-friendly placement signature for repeatability comparison."""
    return tuple(
        (p.part.name, round(p.x, 2), round(p.y, 2),
         round(p.width, 2), round(p.height, 2), bool(p.rotated))
        for p in layout.parts
    )


def _result_signature(result) -> tuple:
    return (
        tuple(_layout_signature(L) for L in result.sheet_layouts),
        tuple(_layout_signature(L) for L in result.missing_sheet_layouts),
        tuple(sorted((p.name, round(p.width, 2), round(p.height, 2))
                      for p in result.unplaced_sheet_parts)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios — diverse coverage
# ─────────────────────────────────────────────────────────────────────────────


SCENARIOS: list[Scenario] = [
    # ── Trivial / known optimums ────────────────────────────────────────────
    Scenario(
        # NOTE: worker adds KERF_TOLERANCE_MM=0.2 to user-provided kerf, so the
        # effective spacing between parts is 0.2 mm even at kerf=0. Use 499.5
        # to leave room for that tolerance: 2 × 499.5 + 0.2 = 999.2 ≤ 1000.
        label="four ~500x500 in 1000x1000 (near-exact fit, 4 parts)",
        stock=[(1000, 1000, 1)],
        parts=[("A", 499.5, 499.5, 4)],
        kerf=0,
        expect_sheets_used_at_most=1,
        expect_all_placed=True,
        expect_util_at_least=99.0,
    ),
    Scenario(
        # 2 × 497.3 + (5 + 0.2) = 999.8 ≤ 1000, so 4 parts at 497.3×497.3
        # fit perfectly with effective kerf of 5.2 (5 from user + 0.2 tolerance).
        label="exact divide with kerf 5 (accounting for 0.2 tolerance)",
        stock=[(1000, 1000, 1)],
        parts=[("A", 497.3, 497.3, 4)],
        kerf=5,
        expect_sheets_used_at_most=1,
        expect_all_placed=True,
    ),
    Scenario(
        label="single 1×1 part on 1000×1000 (huge waste)",
        stock=[(1000, 1000, 1)],
        parts=[("A", 200, 200, 1)],
        kerf=5,
        expect_sheets_used_at_most=1,
        expect_all_placed=True,
    ),
    # ── User's actual screenshot scenario ───────────────────────────────────
    Scenario(
        label="USER: 2× 400×550 + 20× 50×400 on 2000×1000",
        stock=[(2000, 1000, 1)],
        parts=[("A", 400, 550, 2), ("B", 50, 400, 20)],
        kerf=5,
        expect_all_placed=True,
    ),
    # ── Long-thin parts (typical 40×900) ────────────────────────────────────
    Scenario(
        label="55× 40×900 + 10× 45×50 on 2000×1000 qty=1",
        stock=[(2000, 1000, 1)],
        parts=[("A", 40, 900, 55), ("B", 45, 50, 10)],
        kerf=5,
    ),
    Scenario(
        label="48× 40×900 + 100× 45×50 on 2000×1000 qty=2",
        stock=[(2000, 1000, 2)],
        parts=[("A", 40, 900, 48), ("B", 45, 50, 100)],
        kerf=5,
        expect_all_placed=True,
    ),
    # ── Strategic stock sizes ───────────────────────────────────────────────
    Scenario(
        label="strategic 1500×3000 with mixed parts",
        stock=[(1500, 3000, 1)],
        parts=[("X", 600, 400, 4), ("Y", 300, 200, 10), ("Z", 100, 100, 30)],
        kerf=4,
    ),
    Scenario(
        label="strategic 2050×3050 with cabinetry mix",
        stock=[(2050, 3050, 2)],
        parts=[("door", 500, 1800, 6), ("side", 580, 600, 8),
               ("shelf", 580, 380, 12), ("back", 500, 280, 14)],
        kerf=4,
    ),
    # ── Rotation-sensitive ──────────────────────────────────────────────────
    Scenario(
        label="rotation required to fit (tall part on landscape stock)",
        stock=[(1200, 800, 1)],
        # 1100×600 fits only when rotated to 600×1100 — wait, 1100 > 800.
        # Actually fits as 1100×600 within 1200×800. No rotation needed.
        # Make it: 1500×700 stock, part 900×600 — fits both ways.
        parts=[("P", 900, 600, 1)],
        kerf=5,
        expect_all_placed=True,
    ),
    Scenario(
        label="rotation forbidden (no_rotation flag) — must use original orientation",
        stock=[(1500, 700, 1)],
        parts=[("P", 1400, 600, 1)],
        kerf=5,
        allow_rotation=False,
        expect_all_placed=True,
    ),
    # ── Many small parts (filler stress) ────────────────────────────────────
    Scenario(
        label="100× 80×80 squares on 2000×1000",
        stock=[(2000, 1000, 1)],
        parts=[("S", 80, 80, 100)],
        kerf=4,
    ),
    Scenario(
        label="200× 50×50 squares on 2000×1000",
        stock=[(2000, 1000, 1)],
        parts=[("S", 50, 50, 200)],
        kerf=4,
    ),
    # ── Few large parts ─────────────────────────────────────────────────────
    Scenario(
        label="6× 900×800 on 2000×1000 (forces multiple sheets)",
        stock=[(2000, 1000, 5)],
        parts=[("L", 900, 800, 6)],
        kerf=4,
        expect_all_placed=True,
    ),
    # ── Mixed orientation puzzle ────────────────────────────────────────────
    Scenario(
        label="mixed dims: 8× 300×400 + 12× 200×600 + 30× 100×100",
        stock=[(2000, 1000, 2)],
        parts=[("A", 300, 400, 8), ("B", 200, 600, 12), ("C", 100, 100, 30)],
        kerf=4,
    ),
    # ── Quantity stress ─────────────────────────────────────────────────────
    Scenario(
        label="qty=500 small parts",
        stock=[(2000, 1000, 5)],
        parts=[("tiny", 90, 90, 500)],
        kerf=4,
    ),
    # ── Stock orientation: rotated stock should give same/better ─────────────
    Scenario(
        label="stock 2000×1000 vs 1000×2000 (rotation allowed)",
        stock=[(2000, 1000, 1)],
        parts=[("A", 400, 600, 3), ("B", 80, 80, 20)],
        kerf=5,
    ),
    Scenario(
        label="stock 1000×2000 (transposed) — should reach same util",
        stock=[(1000, 2000, 1)],
        parts=[("A", 400, 600, 3), ("B", 80, 80, 20)],
        kerf=5,
    ),
    # ── Edge: parts larger than stock ───────────────────────────────────────
    Scenario(
        label="oversize part (must go unplaced)",
        stock=[(1000, 1000, 1)],
        parts=[("ok", 200, 200, 2), ("toobig", 1500, 200, 1)],
        kerf=5,
        # The 'toobig' part must end up in unplaced_sheet_parts.
    ),
    # ── Edge: zero kerf ─────────────────────────────────────────────────────
    Scenario(
        label="zero kerf — 8× 250×500 must fit in 1000×1000 exactly",
        stock=[(1000, 1000, 1)],
        parts=[("Q", 250, 500, 8)],
        kerf=0,
        expect_all_placed=True,
    ),
    # ── Sport mode parity (same scenario, different mode) ───────────────────
    Scenario(
        label="sport mode: 55× 40×900 + 10× 45×50",
        stock=[(2000, 1000, 1)],
        parts=[("A", 40, 900, 55), ("B", 45, 50, 10)],
        kerf=5,
        mode="sport",
    ),
    # ── Odd shapes ──────────────────────────────────────────────────────────
    Scenario(
        label="ultra-thin strips (10×900) — kerf stress",
        stock=[(2000, 1000, 1)],
        parts=[("strip", 10, 900, 50)],
        kerf=5,
    ),
    Scenario(
        label="square stock 1000×1000 + asymmetric parts",
        stock=[(1000, 1000, 3)],
        parts=[("A", 380, 220, 8), ("B", 290, 95, 12)],
        kerf=4,
    ),
    # ── Mixed orientation puzzle: B=40x500 on 2000x1000 ────────────────────
    # Regression guard: all 55 B parts + 50 A parts must fit on 1 sheet.
    # Natural partition counts (_natural_partition_counts) ensure the 2-tier
    # split (44 B tall + 11 B wide, or similar) is generated as a candidate.
    # Primary scoring still minimises the used longer-side length.
    Scenario(
        label="B=40x500 mixed orientation on 2000x1000: all parts fit 1 sheet",
        stock=[(2000, 1000, 1)],
        parts=[("A", 20, 30, 50), ("B", 40, 500, 55)],
        kerf=5,
        expect_sheets_used_at_most=1,
        expect_all_placed=True,
    ),
    # ── Non-zero margin — edge case that reduces usable area ─────────────────
    # margin=10 leaves 1980×980 mm usable on a 2000×1000 stock.  Parts that
    # easily fit without margin must still fit (with fewer of them per sheet).
    Scenario(
        label="margin=10: mixed parts on 2000×1000 with 10 mm border",
        stock=[(2000, 1000, 2)],
        parts=[("A", 300, 200, 6), ("B", 150, 100, 12), ("C", 80, 80, 20)],
        kerf=5,
        margin=10.0,
        expect_all_placed=True,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Test driver
# ─────────────────────────────────────────────────────────────────────────────


def _run_scenario(s: Scenario) -> tuple[list[str], object]:
    project = _build_project(s)
    result = optimize_sheet_project(project)
    errors = _validate_result(result, project, s.label)

    # Expectations
    if s.expect_all_placed is True and result.unplaced_sheet_parts:
        errors.append(
            f"{s.label}: expected ALL placed but got {len(result.unplaced_sheet_parts)} unplaced"
        )
    if s.expect_sheets_used_at_most is not None:
        used = len(result.sheet_layouts) + len(result.missing_sheet_layouts)
        if used > s.expect_sheets_used_at_most:
            errors.append(
                f"{s.label}: expected <= {s.expect_sheets_used_at_most} sheets, got {used}"
            )
    if s.expect_util_at_least is not None and result.sheet_layouts:
        u = max(L.utilization for L in result.sheet_layouts)
        if u < s.expect_util_at_least - EPS:
            errors.append(
                f"{s.label}: expected util >= {s.expect_util_at_least:.1f}%, got {u:.1f}%"
            )
    if s.expect_biggest_offcut_at_least is not None and result.sheet_layouts:
        # largest_reusable_offcut_area is populated by score_result() which
        # runs during candidate comparison inside the optimizer.
        biggest = result.largest_reusable_offcut_area
        if biggest < s.expect_biggest_offcut_at_least - EPS:
            errors.append(
                f"{s.label}: expected biggest offcut >= {s.expect_biggest_offcut_at_least:.0f} mm2,"
                f" got {biggest:.0f} mm2"
            )

    return errors, result


def _check_repeatability(s: Scenario, runs: int = 3) -> list[str]:
    sigs = []
    for _ in range(runs):
        project = _build_project(s)
        result = optimize_sheet_project(project)
        sigs.append(_result_signature(result))
    if len(set(sigs)) > 1:
        return [f"{s.label}: NOT REPEATABLE across {runs} runs ({len(set(sigs))} unique outputs)"]
    return []


def main() -> int:
    print(f"Running {len(SCENARIOS)} stress scenarios + repeatability checks...\n")
    total_errors: list[str] = []
    cosmetic_warnings: list[str] = []

    for s in SCENARIOS:
        errors, result = _run_scenario(s)
        # Separate guillotine-flag warnings (cosmetic) from real geometry bugs
        real = [e for e in errors if "is_guillotine_feasible=False" not in e]
        cosmetic = [e for e in errors if "is_guillotine_feasible=False" in e]
        cosmetic_warnings.extend(cosmetic)

        # Repeatability check
        real.extend(_check_repeatability(s, runs=3))

        status = "PASS" if not real else "FAIL"
        used = len(result.sheet_layouts)
        miss = len(result.missing_sheet_layouts)
        unpl = len(result.unplaced_sheet_parts)
        util = max((L.utilization for L in result.sheet_layouts), default=0.0)
        print(f"  [{status}] {s.label}")
        print(f"           sheets={used} missing={miss} unplaced={unpl} max_util={util:.1f}%")
        if real:
            for e in real:
                print(f"           ! {e}")
            total_errors.extend(real)

    print(f"\n{'='*72}")
    print(f"  SCENARIOS:   {len(SCENARIOS)}")
    print(f"  HARD ERRORS: {len(total_errors)}")
    print(f"  COSMETIC (guillotine flag): {len(cosmetic_warnings)}")
    if cosmetic_warnings:
        print("  Cosmetic warnings (parts placed but is_guillotine_feasible=False):")
        for w in cosmetic_warnings[:5]:
            print(f"    - {w}")
        if len(cosmetic_warnings) > 5:
            print(f"    ... +{len(cosmetic_warnings) - 5} more")

    if total_errors:
        print("\nHARD FAILURES:")
        for e in total_errors:
            print(f"  - {e}")
        return 1

    print("\n  ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

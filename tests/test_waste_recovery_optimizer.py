from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from algorithms.two_d_vertical_segmented import (
    _build_right_residual_region,
    _part_difficulty,
    _saved_axis,
    _saved_used_length,
    _strategic_dimension,
    optimize_2d_vertical_segmented,
)
from core.models import OptimizationSettings, PlacedSheetPart, Project, SheetLayout, SheetPart, SheetStock
from ui.layout_view import LayoutView
from workers.optimizer_worker import optimize_sheet_project


EPS = 0.001


def _parts_by_name(layout: SheetLayout, name: str) -> list[PlacedSheetPart]:
    return [placement for placement in layout.parts if placement.part.name == name]


def _counts(layout: SheetLayout) -> Counter[str]:
    return Counter(placement.part.name for placement in layout.parts)


def _counts_required(layout: SheetLayout) -> Counter[str]:
    """Count only required (non-waste-fill) parts — excludes is_waste_fill bonus cuts."""
    return Counter(p.part.name for p in layout.parts if not p.part.is_waste_fill)


def _assert_layout_clean(layout: SheetLayout, kerf: float) -> None:
    assert layout.is_guillotine_feasible
    for placement in layout.parts:
        assert placement.x >= -EPS
        assert placement.y >= -EPS
        assert placement.x + placement.width <= layout.stock.width + EPS
        assert placement.y + placement.height <= layout.stock.height + EPS

    for index, first in enumerate(layout.parts):
        for second in layout.parts[index + 1 :]:
            x_overlap = first.x < second.x + second.width - EPS and second.x < first.x + first.width - EPS
            y_overlap = first.y < second.y + second.height - EPS and second.y < first.y + first.height - EPS
            assert not (x_overlap and y_overlap)
            if y_overlap:
                gap = max(second.x - (first.x + first.width), first.x - (second.x + second.width))
                assert gap + EPS >= kerf
            if x_overlap:
                gap = max(second.y - (first.y + first.height), first.y - (second.y + second.height))
                assert gap + EPS >= kerf


def test_long_parts_use_rotated_residual_strip() -> None:
    stock = [SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)]
    parts = [SheetPart("A", 40, 900, 55, "standard", 1, allow_rotation=True)]

    result = optimize_2d_vertical_segmented(stock, parts, kerf=5.2, margin=0, min_reusable_size=80, optimization_mode="comfort")

    assert len(result.sheet_layouts) == 1
    assert len(result.unplaced_sheet_parts) == 7
    layout = result.sheet_layouts[0]
    assert _counts(layout) == Counter({"A": 48})
    assert sum(placement.rotated for placement in _parts_by_name(layout, "A")) == 4
    assert layout.used_height >= 999
    _assert_layout_clean(layout, 5.2)


def test_right_residual_strip_reserves_the_kerf_before_a_rotated_part() -> None:
    """A nominal 155 mm tail is only 150 mm usable after its separating cut."""
    stock = SheetStock("standard", 5, 2000, 1000, 1, allow_rotation=True)
    part = SheetPart("A", 180, 155, 1, "standard", 5, allow_rotation=True)
    layout = SheetLayout(
        stock=stock,
        sheet_index=1,
        parts=[PlacedSheetPart(part, x=1665, y=0, width=180, height=155)],
        vertical_segments=[{"index": 1, "x": 0, "width": 1845, "right": 1845}],
    )

    region = _build_right_residual_region(
        layout,
        [part],
        kerf=5,
        margin=0,
        min_reusable_size=80,
    )

    assert region is not None
    assert region.width == 150
    assert region.fitting_part_names == ()


def test_small_parts_are_fillers_after_waste_recovery() -> None:
    stock = [SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)]
    base = optimize_2d_vertical_segmented(
        stock,
        [SheetPart("long", 40, 900, 55, "standard", 1, allow_rotation=True)],
        kerf=5.2,
        margin=0,
        min_reusable_size=80,
        optimization_mode="comfort",
    )
    parts = [
        SheetPart("small", 10, 10, 30, "standard", 1, allow_rotation=True),
        SheetPart("long", 40, 900, 55, "standard", 1, allow_rotation=True),
    ]

    result = optimize_2d_vertical_segmented(stock, parts, kerf=5.2, margin=0, min_reusable_size=80, optimization_mode="comfort")

    assert len(result.sheet_layouts) == 1
    assert len(result.unplaced_sheet_parts) == 7
    assert {part.name for part in result.unplaced_sheet_parts} == {"long"}
    layout = result.sheet_layouts[0]
    assert _counts(layout) == Counter({"long": 48, "small": 30})
    assert sum(placement.rotated for placement in _parts_by_name(layout, "long")) == 4
    assert round(_saved_used_length(layout), 1) == round(_saved_used_length(base.sheet_layouts[0]), 1)
    assert max(placement.x + placement.width for placement in _parts_by_name(layout, "small")) <= layout.used_width + EPS
    assert min(placement.x for placement in _parts_by_name(layout, "small")) >= 1810
    assert max(placement.x + placement.width for placement in _parts_by_name(layout, "small")) < 1980
    _assert_layout_clean(layout, 5.2)


def test_tiny_parts_do_not_increase_missing_sheet_count_when_they_fit_existing_waste() -> None:
    def project(parts: list[SheetPart]) -> Project:
        return Project(
            sheet_stock=[SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)],
            sheet_parts=parts,
            settings=OptimizationSettings(kerf=5, min_reusable_offcut_size=80, optimization_mode="comfort"),
        )

    base = optimize_sheet_project(project([SheetPart("long", 40, 900, 55, "standard", 1, allow_rotation=True)]))
    with_tiny = optimize_sheet_project(
        project(
            [
                SheetPart("small", 10, 10, 30, "standard", 1, allow_rotation=True),
                SheetPart("long", 40, 900, 55, "standard", 1, allow_rotation=True),
            ]
        )
    )

    # Primary invariant: adding tiny parts must not force MORE missing sheets.
    assert len(base.missing_sheet_layouts) == 1
    assert len(with_tiny.missing_sheet_layouts) == 1
    # No truly-required parts remain unplaced.
    assert len(base.unplaced_sheet_parts) == len(with_tiny.unplaced_sheet_parts) == 0
    # Base (no tiny parts): exactly 7 required long parts on the missing sheet.
    assert len(base.missing_sheet_layouts[0].parts) == 7
    # with_tiny: exactly 7 required long parts on missing sheet; tiny parts may
    # appear as is_waste_fill bonus cuts filling leftover area on extra material.
    required_on_missing = sum(
        1 for layout in with_tiny.missing_sheet_layouts
        for p in layout.parts if not p.part.is_waste_fill
    )
    assert required_on_missing == 7
    # Only the known part types may appear (no stray names).
    assert all(
        p.part.name in {"long", "small"}
        for layout in with_tiny.missing_sheet_layouts
        for p in layout.parts
    )


def test_strategic_dimension_profiles_make_tiny_parts_fillers() -> None:
    profiles = [
        (1000, 2000, 1000),
        (1500, 3000, 1500),
        (2050, 3050, 2050),
        (1300, 1400, 1300),
    ]
    for width, height, strategic in profiles:
        stock = SheetStock("standard", 1, width, height, 1, allow_rotation=True)
        long = SheetPart("long", 40, strategic * 0.9, 1, "standard", 1, allow_rotation=True)
        tiny = SheetPart("tiny", 10, 10, 1, "standard", 1, allow_rotation=True)
        long_score = _part_difficulty(long, stock, kerf=5, margin=0)
        tiny_score = _part_difficulty(tiny, stock, kerf=5, margin=0)

        assert _strategic_dimension(stock) == strategic
        assert tiny_score.is_filler
        assert long_score.score > tiny_score.score


def test_saved_axis_uses_non_strategic_stock_side() -> None:
    cases = [
        (1000, 2000, 1000, "y"),
        (2000, 1000, 1000, "x"),
        (1500, 3000, 1500, "y"),
        (3000, 1500, 1500, "x"),
        (2050, 3050, 2050, "y"),
        (3050, 2050, 2050, "x"),
        (1000, 620, 1000, "y"),
        (620, 1000, 1000, "x"),
    ]
    for width, height, strategic, saved_axis in cases:
        stock = SheetStock("standard", 1, width, height, 1, allow_rotation=True)
        assert _strategic_dimension(stock) == strategic
        assert _saved_axis(stock) == saved_axis


def test_missing_layout_preview_uses_consumed_fragment_dimensions() -> None:
    app = QApplication.instance() or QApplication([])
    _ = app
    stock = SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True, source="missing")
    part = SheetPart("long", 40, 900, 1, "standard", 1, allow_rotation=True)
    layout = SheetLayout(stock=stock, sheet_index=2)
    x = 0.0
    for _index in range(7):
        layout.parts.append(PlacedSheetPart(part, x, 0.0, 40, 900, False))
        x += 45.2
    layout.vertical_segments = [{"index": index + 1, "x": round(index * 45.2, 3), "width": 40, "right": round(index * 45.2 + 40, 3)} for index in range(7)]

    view = LayoutView()
    display_w, display_h, rotated, _ = view._display_sheet_geometry(layout, consumed_only=True)

    assert not rotated
    assert round(display_w, 1) == round(layout.used_width, 1)
    assert display_w < stock.width
    assert display_h == stock.height
    view.deleteLater()


def test_kerf_aware_orientation_split_candidates_minimize_saved_length() -> None:
    stock = [SheetStock("standard", 1, 3000, 1500, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)]
    parts = [
        SheetPart("A", 40, 900, 55, "standard", 1, allow_rotation=True),
        SheetPart("B", 100, 100, 3, "standard", 1, allow_rotation=True),
        SheetPart("C", 20, 30, 55, "standard", 1, allow_rotation=True),
        SheetPart("D", 10, 10, 20, "standard", 1, allow_rotation=True),
    ]

    previous_debug = os.environ.get("SIEKACZ_DEBUG_CANDIDATES")
    os.environ["SIEKACZ_DEBUG_CANDIDATES"] = "1"
    try:
        result = optimize_2d_vertical_segmented(
            stock,
            parts,
            kerf=5,
            margin=0,
            min_reusable_size=80,
            optimization_mode="comfort",
        )
    finally:
        if previous_debug is None:
            os.environ.pop("SIEKACZ_DEBUG_CANDIDATES", None)
        else:
            os.environ["SIEKACZ_DEBUG_CANDIDATES"] = previous_debug

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 1
    layout = result.sheet_layouts[0]
    _assert_layout_clean(layout, 5)
    assert _counts(layout) == Counter({"A": 55, "C": 55, "D": 20, "B": 3})

    # Two kerf-aware 900 mm strips consume 900 + 5 + 900 = 1805 mm.
    # A tighter result may win, but no candidate may be selected by silently
    # using the impossible 1800 mm "zero kerf between strips" shortcut.
    saved_length = _saved_used_length(layout)
    assert saved_length <= 1805 + EPS
    assert not (1800 - EPS < saved_length < 1805 - EPS)

    filler_names = {"B", "C", "D"}
    filler_right = max(placement.x + placement.width for placement in layout.parts if placement.part.name in filler_names)
    assert filler_right <= layout.used_width + EPS

    debug_text = "\n".join(result.messages)
    assert "Candidate debug:" in debug_text
    assert "Orientation Split Candidate" in debug_text
    assert "kerf=5.00" in debug_text
    assert "usedLengthX=" in debug_text
    assert "Winner orientations A:" in debug_text
    assert "900x40=" in debug_text


def test_waste_first_refill_moves_square_fillers_under_long_columns() -> None:
    stock = [SheetStock("standard", 1, 3000, 1500, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)]
    parts = [
        SheetPart("A", 40, 900, 9, "standard", 1, allow_rotation=True),
        SheetPart("C", 100, 100, 15, "standard", 1, allow_rotation=True),
    ]

    previous_debug = os.environ.get("SIEKACZ_DEBUG_CANDIDATES")
    os.environ["SIEKACZ_DEBUG_CANDIDATES"] = "1"
    try:
        result = optimize_2d_vertical_segmented(
            stock,
            parts,
            kerf=5,
            margin=0,
            min_reusable_size=80,
            optimization_mode="comfort",
        )
    finally:
        if previous_debug is None:
            os.environ.pop("SIEKACZ_DEBUG_CANDIDATES", None)
        else:
            os.environ["SIEKACZ_DEBUG_CANDIDATES"] = previous_debug

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 1
    layout = result.sheet_layouts[0]
    _assert_layout_clean(layout, 5)
    assert _counts(layout) == Counter({"A": 9, "C": 15})

    long_block_width = 9 * 40 + 8 * 5
    assert round(_saved_used_length(layout), 3) == round(long_block_width, 3)
    assert max(placement.x + placement.width for placement in _parts_by_name(layout, "C")) <= long_block_width + EPS
    assert min(placement.y for placement in _parts_by_name(layout, "C")) >= 900 + 5 - EPS

    debug_text = "\n".join(result.messages)
    assert "bottom band across adjacent strips" in debug_text
    assert "repair pass improved" in debug_text
    assert "Optimizer debug rejection summary:" in debug_text
    assert "guillotine=" in debug_text


def test_fillers_flow_to_missing_sheet_waste_band() -> None:
    """P9: Small filler parts (A, B) should NOT occupy a dedicated strip on the stock sheet
    that inflates its width; instead they should flow to the missing sheet where the optimizer
    fills the waste bands under larger non-full-height parts (D).

    Scenario: 2000x1000 stock, 1 sheet.
    A (10x10, 30pcs) and B (20x30, 50pcs) are fillers for this stock.
    C (40x900, 55pcs) and D (400x550, 2pcs) are structural.

    Old behaviour: A+B in a dedicated left-side strip → missing sheet ~957mm wide.
    Expected: A+B placed in waste areas on stock sheet OR flow to missing sheet's waste bands
              → missing sheet ≤ 920mm wide (at least 35mm narrower than old).
    All parts must be placed across stock + missing sheets combined.
    """
    settings = OptimizationSettings(
        job_type="sheet",
        optimization_mode="comfort",
        kerf=5,
        margin=0,
        sheet_allowance=0,
        min_reusable_offcut_size=80,
        cutting_mode="hybrid",
        allow_rotation=True,
    )
    project = Project(
        sheet_stock=[SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)],
        sheet_parts=[
            SheetPart("A", 10, 10, 30, "standard", 1, allow_rotation=True),
            SheetPart("B", 20, 30, 50, "standard", 1, allow_rotation=True),
            SheetPart("C", 40, 900, 55, "standard", 1, allow_rotation=True),
            SheetPart("D", 400, 550, 2, "standard", 1, allow_rotation=True),
        ],
        settings=settings,
    )

    result = optimize_sheet_project(project)

    # All sheets must be guillotine-feasible
    for layout in result.sheet_layouts + result.missing_sheet_layouts:
        _assert_layout_clean(layout, 5)

    # Count REQUIRED placed parts across all sheets (exclude is_waste_fill bonus cuts).
    placed: Counter[str] = Counter()
    for layout in result.sheet_layouts + result.missing_sheet_layouts:
        placed += _counts_required(layout)

    assert placed["A"] == 30, f"A placed={placed['A']} expected 30"
    assert placed["B"] == 50, f"B placed={placed['B']} expected 50"
    assert placed["C"] == 55, f"C placed={placed['C']} expected 55"
    assert placed["D"] == 2, f"D placed={placed['D']} expected 2"

    # Missing sheet must be narrower than the old 957mm baseline — fillers should
    # not be forcing it to use extra structural strip width.
    assert result.missing_sheet_layouts, "Expected at least one missing sheet"
    missing_width = result.missing_sheet_layouts[0].used_width
    assert missing_width <= 920, (
        f"Missing sheet too wide ({missing_width:.1f}mm ≥ 920mm) — "
        "fillers may be occupying a dedicated strip instead of filling D-band waste"
    )


def test_fillers_fill_waste_under_c_on_missing_sheet() -> None:
    """P10: When a missing sheet contains tall structural strips (A) + a tall block (C) +
    many small fillers (B), the fillers should fill the waste area under C rather than
    occupying dedicated strips between A and C.

    Scenario: 2000x1000 stock, missing-sheet pass with A(40x900, qty=7) + B(10x10, qty=952)
    + C(400x550, qty=1).

    Old behaviour (before P10): B strips inserted between A and C → usedW ~929mm.
    Expected: B fills waste under C → usedW ≤ 750mm, all parts placed, feasible.
    """
    stock = [SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=False, min_offcut_width=80, min_offcut_height=80)]
    parts = [
        SheetPart("A", 40, 900, 7, "standard", 1, allow_rotation=True),
        SheetPart("B", 10, 10, 952, "standard", 1, allow_rotation=True),
        SheetPart("C", 400, 550, 1, "standard", 1, allow_rotation=True),
    ]

    from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented

    result = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, min_reusable_size=80, optimization_mode="comfort")

    assert result.sheet_layouts, "Expected at least one layout"
    layout = result.sheet_layouts[0]

    # Must be guillotine-feasible
    assert layout.is_guillotine_feasible, f"Layout not feasible: {layout.technology_warning}"

    # Width must be much smaller than the old 929mm — fillers fill waste instead of strips
    assert layout.used_width <= 750, (
        f"usedW={layout.used_width:.1f}mm > 750mm — "
        "B(10×10) parts may be in dedicated strips instead of waste under C(400×550)"
    )

    # A(7) and C(1) must be placed on this sheet
    placed_names = Counter(p.part.name for p in layout.parts)
    assert placed_names["A"] == 7, f"A placed={placed_names['A']} expected 7"
    assert placed_names["C"] == 1, f"C placed={placed_names['C']} expected 1"

    # Nearly all B must be on this sheet (overflow to missing sheet OK but should be few)
    total_b_placed = placed_names["B"]
    total_b_unplaced = sum(
        p.quantity for p in result.unplaced_sheet_parts if p.name == "B"
    )
    assert total_b_placed + total_b_unplaced == 952, (
        f"B accounting mismatch: placed={total_b_placed} unplaced={total_b_unplaced}"
    )
    # At least 90% of B parts should be placed on the sheet in waste areas
    assert total_b_placed >= 855, (  # 90% of 952
        f"Too few B parts placed on sheet: {total_b_placed}/952 — "
        "B should fill waste under C rather than flow to a new missing sheet"
    )


def test_bonus_fills_placed_in_missing_sheet_waste() -> None:
    """When all small fillers fit on the main sheet and B parts overflow to missing sheets,
    the missing-sheet optimiser should receive bonus-fill copies of the small fillers and
    place them in the leftover area (below B parts).

    Scenario: 2000×1000 stock, qty=1.
      Structural: 40×900 strips (fill main sheet).
      Large B:    400×800, qty=2 — can't fit alongside strips → overflow.
      Small SQ:   30×30,  qty=20 — filler, all fit on main sheet.

    Expected: missing sheet has B parts + bonus-fill SQ copies in waste; the SQ copies
    are tagged is_waste_fill=True (not counted in required BOM).
    """
    settings = OptimizationSettings(
        job_type="sheet",
        optimization_mode="comfort",
        kerf=5,
        margin=0,
        sheet_allowance=0,
        min_reusable_offcut_size=80,
        cutting_mode="hybrid",
        allow_rotation=True,
    )
    project = Project(
        sheet_stock=[SheetStock("W", 18, 2000, 1000, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)],
        sheet_parts=[
            SheetPart("strip", 40, 900, 30, "W", 18, allow_rotation=True),
            SheetPart("B",     400, 800, 2,  "W", 18, allow_rotation=True),
            SheetPart("SQ",    30,  30,  20, "W", 18, allow_rotation=True),
        ],
        settings=settings,
    )

    result = optimize_sheet_project(project)

    # Feasibility on all layouts
    for layout in result.sheet_layouts + result.missing_sheet_layouts:
        _assert_layout_clean(layout, 5)

    # Required BOM must be exact (waste fills excluded)
    placed: Counter[str] = Counter()
    for layout in result.sheet_layouts + result.missing_sheet_layouts:
        placed += _counts_required(layout)

    assert placed["strip"] == 30, f"strip placed={placed['strip']}"
    assert placed["B"] == 2,      f"B placed={placed['B']}"
    assert placed["SQ"] == 20,    f"SQ placed={placed['SQ']}"

    # At least one missing sheet must exist (B doesn't fit on main sheet alongside strips)
    assert result.missing_sheet_layouts, "Expected B parts on missing sheets"

    # Bonus-fill SQ copies should appear on at least one missing sheet
    waste_fills_on_missing = sum(
        1 for layout in result.missing_sheet_layouts
        for p in layout.parts
        if p.part.is_waste_fill
    )
    assert waste_fills_on_missing > 0, (
        "No bonus-fill parts on missing sheets — waste below B parts not utilised"
    )

    # All waste fills must be tagged correctly (is_waste_fill=True)
    for layout in result.missing_sheet_layouts:
        for p in layout.parts:
            if p.part.name == "SQ":
                assert p.part.is_waste_fill, (
                    "SQ on missing sheet should be is_waste_fill=True (bonus cut from waste)"
                )


def test_horizontal_block_candidate_saves_plate_length() -> None:
    """Parts with high aspect ratio (≥4) placed as horizontal rows save ~300 mm
    of plate length vs the default vertical-strip arrangement.

    Scenario: 8 long narrow boards (A×2 1080×200, B×2 1085×200, C×2 1122×200,
    D×2 1124×200) plus smaller parts on a 3000×1500 plate.

    Vertical-strip arrangement: 8 columns of 200 mm → 1635 mm used in X,
    total ~2125 mm with the smaller parts to the right.

    Horizontal-row arrangement: 7 rows of height 200 mm → block max width
    1124 mm, remaining parts fill right columns → total ~1824 mm used in X.

    The test asserts:
    1. All parts placed on ONE plate (no unplaced, no 2nd sheet).
    2. Guillotine feasibility holds.
    3. saved_used_length ≤ 2000 mm  (well below the 2125 mm vertical baseline).
    """
    stock = [SheetStock("std", 1, 3000, 1500, 1, allow_rotation=True,
                        min_offcut_width=80, min_offcut_height=80)]
    parts = [
        SheetPart("A", 1080, 200, 2, "std", 1, allow_rotation=True),
        SheetPart("B", 1085, 200, 2, "std", 1, allow_rotation=True),
        SheetPart("C", 1122, 200, 2, "std", 1, allow_rotation=True),
        SheetPart("D", 1124, 200, 2, "std", 1, allow_rotation=True),
        SheetPart("E", 348, 160, 2, "std", 1, allow_rotation=True),
        SheetPart("F", 321, 160, 2, "std", 1, allow_rotation=True),
        SheetPart("G", 753, 160, 2, "std", 1, allow_rotation=True),
        SheetPart("H", 719, 160, 2, "std", 1, allow_rotation=True),
    ]

    result = optimize_2d_vertical_segmented(
        stock, parts, kerf=5, margin=0,
        min_reusable_size=80, optimization_mode="comfort",
    )

    assert not result.unplaced_sheet_parts, (
        f"Unplaced parts: {[p.name for p in result.unplaced_sheet_parts]}"
    )
    assert len(result.sheet_layouts) == 1, (
        f"Expected 1 sheet, got {len(result.sheet_layouts)}"
    )
    layout = result.sheet_layouts[0]
    _assert_layout_clean(layout, 5)

    assert layout.is_guillotine_feasible, (
        "Winning layout must be guillotine-feasible"
    )

    saved_len = _saved_used_length(layout)
    assert saved_len <= 2000, (
        f"Expected saved_used_length ≤ 2000 mm (horizontal rows), got {saved_len:.1f} mm"
    )


def test_kerf_zero_places_all_parts() -> None:
    """kerf=0 (e.g. laser cutting) must not crash and must place all parts.

    With zero kerf the algorithm gets 0.2 mm tolerance added by the worker,
    but when called directly with kerf=0 there is no tolerance — all spacing
    between parts is exactly zero.  This edge case must work without
    divisions-by-zero, infinite loops or assertion errors.
    """
    stock = [SheetStock("std", 1, 2000, 1000, 1, allow_rotation=True,
                        min_offcut_width=50, min_offcut_height=50)]
    parts = [
        SheetPart("A", 400, 300, 2, "std", 1, allow_rotation=True),
        SheetPart("B", 200, 150, 4, "std", 1, allow_rotation=True),
        SheetPart("C", 100, 100, 6, "std", 1, allow_rotation=True),
    ]
    result = optimize_2d_vertical_segmented(
        stock, parts, kerf=0, margin=0, min_reusable_size=50, optimization_mode="comfort",
    )
    assert not result.unplaced_sheet_parts, (
        f"kerf=0: all parts must be placed, got {len(result.unplaced_sheet_parts)} unplaced"
    )
    assert result.sheet_layouts, "kerf=0: must produce at least one layout"
    layout = result.sheet_layouts[0]
    assert layout.is_guillotine_feasible, "kerf=0: layout must be guillotine-feasible"
    for p in layout.parts:
        assert p.x >= -0.001 and p.y >= -0.001, f"kerf=0: part {p.part.name} out of bounds"


def test_single_part_single_stock() -> None:
    """Smallest possible input: one part on one stock plate — must work cleanly."""
    stock = [SheetStock("std", 1, 1000, 500, 1, allow_rotation=False,
                        min_offcut_width=50, min_offcut_height=50)]
    parts = [SheetPart("X", 300, 200, 1, "std", 1, allow_rotation=False)]
    result = optimize_2d_vertical_segmented(
        stock, parts, kerf=5, margin=0, min_reusable_size=50, optimization_mode="comfort",
    )
    assert not result.unplaced_sheet_parts, "Single part must be placed"
    assert len(result.sheet_layouts) == 1
    layout = result.sheet_layouts[0]
    assert layout.is_guillotine_feasible
    placed = layout.parts[0]
    assert placed.width == 300 and placed.height == 200, (
        f"Part dimensions should be preserved, got {placed.width}×{placed.height}"
    )


def test_repeated_parts_prefer_uniform_grid_when_material_ties() -> None:
    stock = [
        SheetStock("std", 1, 1500, 3000, 5, allow_rotation=True, min_offcut_width=80, min_offcut_height=80),
        SheetStock("std", 1, 1000, 2000, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80),
    ]
    parts = [SheetPart("A", 497, 997, 50, "std", 1, allow_rotation=True)]
    result = optimize_2d_vertical_segmented(
        stock,
        parts,
        kerf=3,
        margin=0,
        min_reusable_size=80,
        optimization_mode="comfort",
    )

    assert len(result.sheet_layouts) == 6
    assert len(result.unplaced_sheet_parts) == 1

    large_layouts = [layout for layout in result.sheet_layouts if round(layout.stock.width) == 3000]
    assert len(large_layouts) == 5
    for layout in large_layouts:
        orientations = {
            (round(placement.width), round(placement.height), placement.rotated)
            for placement in layout.parts
        }
        assert orientations == {(997, 497, True)}
        assert layout.strip_count == 3
        assert round(layout.used_width) == 2997
        assert round(layout.used_height) == 1497


def test_worker_keeps_repeated_parts_uniform_grid_after_kerf_tolerance() -> None:
    project = Project()
    project.sheet_stock = [
        SheetStock("standard", 1, 1500, 3000, 5, allow_rotation=True, min_offcut_width=80, min_offcut_height=80),
        SheetStock("standard", 1, 1000, 2000, 1, allow_rotation=True, min_offcut_width=80, min_offcut_height=80),
    ]
    project.sheet_parts = [SheetPart("A", 497, 997, 50, "standard", 1, allow_rotation=True)]
    project.settings = OptimizationSettings(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        kerf=2.8,
        margin=0,
        min_reusable_offcut_size=80,
        cutting_mode="hybrid",
        optimization_mode="comfort",
    )

    result = optimize_sheet_project(project)
    large_layouts = [layout for layout in result.sheet_layouts if round(layout.stock.width) == 3000]

    assert len(result.sheet_layouts) == 6
    assert len(result.missing_sheet_layouts) == 1
    assert len(large_layouts) == 5
    for layout in large_layouts:
        orientations = {
            (round(placement.width), round(placement.height), placement.rotated)
            for placement in layout.parts
        }
        assert orientations == {(997, 497, True)}
        assert layout.strip_count == 3
        assert round(layout.used_width) == 2997
        assert round(layout.used_height) == 1497


def test_repeated_mixed_grid_fills_the_last_legal_side_strip() -> None:
    """A 3000x1500 sheet fits 16, not 15, pieces of 400x600 mm.

    Six vertical pieces on top, eight horizontal pieces below and two vertical
    pieces in the right strip is a legal two-strip guillotine layout.  This
    guards the candidate topology that was previously missed in the preview.
    """
    stock = [SheetStock("PVC", 20, 3000, 1500, 1, allow_rotation=True)]
    parts = [SheetPart("D", 400, 600, 16, "PVC", 20, allow_rotation=True)]

    result = optimize_2d_vertical_segmented(
        stock, parts, kerf=5.2, margin=0, min_reusable_size=80, optimization_mode="comfort",
    )

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 1
    layout = result.sheet_layouts[0]
    assert len(layout.parts) == 16
    assert layout.is_guillotine_feasible
    assert sum(1 for placed in layout.parts if placed.rotated) == 8
    assert any(placed.x > 2400 and placed.height == 600 for placed in layout.parts)


def main() -> None:
    test_long_parts_use_rotated_residual_strip()
    test_right_residual_strip_reserves_the_kerf_before_a_rotated_part()
    test_small_parts_are_fillers_after_waste_recovery()
    test_tiny_parts_do_not_increase_missing_sheet_count_when_they_fit_existing_waste()
    test_strategic_dimension_profiles_make_tiny_parts_fillers()
    test_missing_layout_preview_uses_consumed_fragment_dimensions()
    test_kerf_aware_orientation_split_candidates_minimize_saved_length()
    test_waste_first_refill_moves_square_fillers_under_long_columns()
    test_fillers_flow_to_missing_sheet_waste_band()
    test_fillers_fill_waste_under_c_on_missing_sheet()
    test_bonus_fills_placed_in_missing_sheet_waste()
    test_horizontal_block_candidate_saves_plate_length()
    test_kerf_zero_places_all_parts()
    test_single_part_single_stock()
    test_repeated_parts_prefer_uniform_grid_when_material_ties()
    test_worker_keeps_repeated_parts_uniform_grid_after_kerf_tolerance()
    test_repeated_mixed_grid_fills_the_last_legal_side_strip()
    print("waste recovery optimizer tests: OK")


if __name__ == "__main__":
    main()

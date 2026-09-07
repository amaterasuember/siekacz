"""Regression tests for side cut-order strips, identical-board grouping and
per-format cut metrics.

Run:  python tests/test_cut_strips.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QGraphicsTextItem

from core.models import (
    OptimizationResult,
    PlacedSheetPart,
    SheetLayout,
    SheetPart,
    SheetStock,
)

_app = QApplication.instance() or QApplication([])


def _grid_layout() -> SheetLayout:
    st = SheetStock("standard", 18, 3000, 1500, 1)
    parts = []
    x = 0.0
    for _ in range(9):
        parts.append(PlacedSheetPart(SheetPart("A", 40, 900, 1, "standard", 18), x, 0, 40, 900))
        x += 45  # 40 + 5 kerf
    return SheetLayout(stock=st, sheet_index=1, parts=parts)


def test_vertical_bands_clean_grid() -> None:
    from ui.layout_view import LayoutView

    view = LayoutView()
    groups = view._compute_vertical_bands(_grid_layout())
    assert len(groups) == 1, f"expected one merged block, got {groups}"
    g = groups[0]
    assert g["n"] == 9, f"expected 9 columns, got {g['n']}"
    assert g["uniform"] is True
    assert abs(g["w"] - 40) < 1.0, f"expected ~40 mm, got {g['w']}"
    print("[OK] vertical bands clean grid: 9 × 40 mm")


def test_horizontal_bands_rows() -> None:
    from ui.layout_view import LayoutView

    st = SheetStock("standard", 18, 3000, 1500, 1)
    parts = [PlacedSheetPart(SheetPart("D", 500, 300, 1, "standard", 18), 0, y, 500, 300)
             for y in (0, 305, 610)]
    view = LayoutView()
    groups = view._compute_horizontal_bands(SheetLayout(stock=st, sheet_index=1, parts=parts))
    assert len(groups) == 1 and groups[0]["n"] == 3, f"expected 3 rows, got {groups}"
    print("[OK] horizontal bands: 3 × 300 mm")


def test_mixed_size_same_part_not_merged() -> None:
    """Regression: a 997-tall piece above a 497-tall piece of the SAME formatka
    (997×497, one rotated) must show TWO cuts (997 and 497), not one '1499'."""
    from ui.layout_view import LayoutView

    st = SheetStock("standard", 18, 3000, 1500, 1)
    part = SheetPart("A", 997, 497, 1, "standard", 18)
    # Column 1: a 497-wide × 997-tall piece on top, a 997-wide × 497-tall below.
    parts = [
        PlacedSheetPart(part, 0, 0, 497, 997, rotated=True),
        PlacedSheetPart(part, 0, 1002, 997, 497, rotated=False),
    ]
    view = LayoutView()
    bands = view._compute_horizontal_bands(SheetLayout(stock=st, sheet_index=1, parts=parts))
    totals = sorted(round(b["total"]) for b in bands)
    assert totals == [497, 997], f"expected two bands 497 & 997, got {totals}"
    print("[OK] mixed-size same-part rows kept separate: 997 + 497 (not 1499)")


def test_render_both_orientations_no_crash() -> None:
    from ui.layout_view import LayoutView

    result = OptimizationResult(job_type="sheet", algorithm="test")
    result.sheet_layouts = [_grid_layout()]
    for orientation in ("horizontal", "vertical", "auto"):
        view = LayoutView()
        view.set_display_orientation(orientation)
        view.show_result(result)  # must not raise — strips drawn on swapped axes
        assert view.sheet_card_count() == 1
    print("[OK] strips render in horizontal / vertical / auto without crashing")


def test_top_keeps_band_details_and_bottom_has_one_used_length_summary() -> None:
    from ui.layout_view import LayoutView

    view = LayoutView()
    layout = _grid_layout()
    sheet_rect = QRectF(100, 100, 1260, 630)
    view._draw_segment_dimensioning(layout, sheet_rect, 0.42, False)
    labels = [
        item for item in view.graphics_scene.items()
        if isinstance(item, QGraphicsTextItem)
    ]
    detail = [item for item in labels if item.toPlainText() == "9 × 40 mm"]
    summary = [item for item in labels if item.toPlainText() == "Zużycie: 400 mm"]
    assert len(detail) == 1 and detail[0].sceneBoundingRect().bottom() < sheet_rect.top()
    assert len(summary) == 1 and summary[0].sceneBoundingRect().top() > sheet_rect.bottom()
    print("[OK] detailed bands are above; one total used-length bracket is below")


def test_identical_board_grouping() -> None:
    from algorithms.layout_grouping import group_identical_layouts

    layouts = []
    for i in range(5):
        st = SheetStock("standard", 18, 2000, 1000, 1)
        parts = [PlacedSheetPart(SheetPart("A", 500, 500, 1, "standard", 18), 0, 0, 500, 500)]
        layouts.append(SheetLayout(stock=st, sheet_index=i + 1, parts=parts))
    # One different board.
    st = SheetStock("standard", 18, 2000, 1000, 1)
    diff = SheetLayout(stock=st, sheet_index=6,
                       parts=[PlacedSheetPart(SheetPart("B", 400, 400, 1, "standard", 18), 0, 0, 400, 400)])
    layouts.append(diff)
    groups = group_identical_layouts(layouts)
    assert len(groups) == 2, f"expected 2 groups, got {len(groups)}"
    assert groups[0].count == 5, f"expected ×5, got {groups[0].count}"
    assert groups[1].count == 1
    print("[OK] identical-board grouping: ×5 + ×1")


def test_cut_metrics_invariants() -> None:
    from algorithms.cut_metrics import compute_cut_summary

    result = OptimizationResult(job_type="sheet", algorithm="test")
    result.sheet_layouts = [_grid_layout()]
    summary = compute_cut_summary(result)
    assert summary.total_pieces == 9
    assert summary.total_saw_m > 0
    assert summary.total_time_s > 0
    assert len(summary.formats) == 1 and summary.formats[0].pieces == 9
    print(f"[OK] cut metrics: {summary.total_pieces} szt., "
          f"{summary.total_saw_m:.2f} mb, t={summary.total_time_s:.0f}s")


if __name__ == "__main__":
    test_vertical_bands_clean_grid()
    test_horizontal_bands_rows()
    test_mixed_size_same_part_not_merged()
    test_render_both_orientations_no_crash()
    test_top_keeps_band_details_and_bottom_has_one_used_length_summary()
    test_identical_board_grouping()
    test_cut_metrics_invariants()
    print("\ntest_cut_strips: OK")

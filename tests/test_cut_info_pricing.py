"""Regression tests for per-format waste/time metrics, job pricing and the
CutInfoDialog.

Run:  python tests/test_cut_info_pricing.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from algorithms.cut_metrics import compute_cut_summary
from algorithms.pricing import PricingRates, compute_pricing
from core.models import (
    OptimizationResult,
    PlacedSheetPart,
    SheetLayout,
    SheetPart,
    SheetStock,
)

_app = QApplication.instance() or QApplication([])

_TOL = 1e-6


def _result_two_formats() -> OptimizationResult:
    """One 2000×1000 board with two A (500×400) and one B (300×300)."""
    st = SheetStock("standard", 18, 2000, 1000, 1)
    A = SheetPart("A", 500, 400, 1, "standard", 18)
    B = SheetPart("B", 300, 300, 1, "standard", 18)
    parts = [
        PlacedSheetPart(A, 0, 0, 500, 400),
        PlacedSheetPart(A, 510, 0, 500, 400),
        PlacedSheetPart(B, 0, 410, 300, 300),
    ]
    result = OptimizationResult(job_type="sheet", algorithm="test")
    result.sheet_layouts = [SheetLayout(stock=st, sheet_index=1, parts=parts)]
    return result


def _result_reusable_tail() -> OptimizationResult:
    """One 1000x2000 board using a clean 1000x1600 strip."""
    st = SheetStock("standard", 18, 1000, 2000, 1)
    part = SheetPart("Panel", 1000, 1600, 1, "standard", 18)
    result = OptimizationResult(job_type="sheet", algorithm="test")
    result.sheet_layouts = [
        SheetLayout(stock=st, sheet_index=1, parts=[PlacedSheetPart(part, 0, 0, 1000, 1600)])
    ]
    return result


def test_clean_tail_goes_to_warehouse_not_waste() -> None:
    s = compute_cut_summary(_result_reusable_tail())
    assert abs(s.total_area_m2 - 1.6) < _TOL, s.total_area_m2
    assert abs(s.total_stock_area_m2 - 2.0) < _TOL, s.total_stock_area_m2
    assert abs(s.total_reusable_m2 - 0.4) < _TOL, s.total_reusable_m2
    assert abs(s.total_waste_m2 - 0.0) < _TOL, s.total_waste_m2
    assert abs(s.total_board_area_m2 - 1.6) < _TOL, s.total_board_area_m2
    print("[OK] 1000x400 clean tail is warehouse stock, not waste")


def test_reusable_remnants_are_not_billed_as_scrap() -> None:
    s = compute_cut_summary(_result_two_formats())
    assert abs(s.total_area_m2 - 0.49) < _TOL, s.total_area_m2
    assert abs(s.total_stock_area_m2 - 2.0) < _TOL, s.total_stock_area_m2
    assert s.total_reusable_m2 > 0.0, s.total_reusable_m2
    assert s.total_waste_m2 < (s.total_stock_area_m2 - s.total_area_m2), s.total_waste_m2
    assert abs(s.total_board_area_m2 - (s.total_area_m2 + s.total_waste_m2)) < _TOL
    gross_sum = sum(f.gross_m2 for f in s.formats)
    assert abs(gross_sum - s.total_board_area_m2) < 1e-9, gross_sum
    waste_sum = sum(f.waste_m2 for f in s.formats)
    assert abs(waste_sum - s.total_waste_m2) < 1e-9, waste_sum
    reusable_sum = sum(f.reusable_m2 for f in s.formats)
    assert abs(reusable_sum - s.total_reusable_m2) < 1e-9, reusable_sum
    print("[OK] reusable remnants are reported separately from production scrap")


def test_per_format_time_positive() -> None:
    s = compute_cut_summary(_result_two_formats())
    for f in s.formats:
        assert f.time_s > 0, f"{f.name} time={f.time_s}"
    print("[OK] per-format time estimates positive")


def test_pricing_math_and_totals() -> None:
    s = compute_cut_summary(_result_two_formats())
    rates = PricingRates(per_m2=100.0, per_saw_m=2.0, per_hour=60.0)
    p = compute_pricing(s, rates)

    # material total = billable material, with reusable remnants excluded.
    assert abs(p.material_cost - s.total_board_area_m2 * 100.0) < 1e-6, p.material_cost
    # totals = sum of per-format
    assert abs(p.material_cost - sum(c.material_cost for c in p.formats)) < 1e-9
    assert abs(p.cut_cost - sum(c.cut_cost for c in p.formats)) < 1e-9
    assert abs(p.labor_cost - sum(c.labor_cost for c in p.formats)) < 1e-9
    assert abs(p.total - (p.material_cost + p.cut_cost + p.labor_cost)) < 1e-9
    # each format cost = material+cut+labor
    for c in p.formats:
        assert abs(c.total - (c.material_cost + c.cut_cost + c.labor_cost)) < 1e-9
    print(f"[OK] pricing: reusable remnants excluded, total {p.total:.2f} zl, per-format spojne")


def test_zero_rates_is_zero() -> None:
    s = compute_cut_summary(_result_two_formats())
    p = compute_pricing(s, PricingRates())
    assert p.total == 0.0
    assert PricingRates().is_zero
    print("[OK] zero rates -> zero cost")


def test_cut_info_dialog_renders() -> None:
    from app.simple_window import CutInfoDialog

    result = _result_two_formats()
    dlg = CutInfoDialog(None, result)
    # 2 formats + 1 totals row
    assert dlg._table.rowCount() == 3, dlg._table.rowCount()
    assert dlg._table.columnCount() == 11
    # totals row last cell shows "RAZEM"
    assert dlg._table.item(2, 0).text() == "RAZEM"
    dlg.close()
    print("[OK] CutInfoDialog renders: 2 formats + RAZEM row, 11 columns")


if __name__ == "__main__":
    test_clean_tail_goes_to_warehouse_not_waste()
    test_reusable_remnants_are_not_billed_as_scrap()
    test_per_format_time_positive()
    test_pricing_math_and_totals()
    test_zero_rates_is_zero()
    test_cut_info_dialog_renders()
    print("\ntest_cut_info_pricing: OK")

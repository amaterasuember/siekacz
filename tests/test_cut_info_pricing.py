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


def test_crosscut_tail_inside_the_consumed_strip_is_customer_waste() -> None:
    s = compute_cut_summary(_result_reusable_tail())
    assert abs(s.total_area_m2 - 1.6) < _TOL, s.total_area_m2
    assert abs(s.total_stock_area_m2 - 2.0) < _TOL, s.total_stock_area_m2
    assert abs(s.total_reusable_m2 - 0.0) < _TOL, s.total_reusable_m2
    assert abs(s.total_waste_m2 - 0.4) < _TOL, s.total_waste_m2
    assert abs(s.total_board_area_m2 - 2.0) < _TOL, s.total_board_area_m2
    print("[OK] crosscut tail inside a consumed strip is customer waste")


def test_only_the_untouched_meter_tail_is_returned_to_warehouse() -> None:
    stock = SheetStock("standard", 18, 2000, 1000, 1)
    part = SheetPart("A", 233, 323, 1, "standard", 18)
    result = OptimizationResult(
        job_type="sheet",
        algorithm="test",
        sheet_layouts=[SheetLayout(stock=stock, sheet_index=1, parts=[PlacedSheetPart(part, 0, 0, 233, 323)])],
    )
    summary = compute_cut_summary(result)
    assert abs(summary.total_board_area_m2 - 0.233) < _TOL
    assert abs(summary.total_waste_m2 - (0.233 - 233 * 323 / 1_000_000.0)) < _TOL
    assert abs(summary.total_reusable_m2 - 1.767) < _TOL
    print("[OK] only the uncut meter tail remains warehouse stock")


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


def test_saw_feed_changes_time_and_labor_cost() -> None:
    result = _result_two_formats()
    slow = compute_cut_summary(result, feed_m_per_min=6.0)
    fast = compute_cut_summary(result, feed_m_per_min=24.0)
    assert slow.total_time_s > fast.total_time_s
    slow_cost = compute_pricing(slow, PricingRates(per_hour=120.0)).labor_cost
    fast_cost = compute_pricing(fast, PricingRates(per_hour=120.0)).labor_cost
    assert slow_cost > fast_cost
    print("[OK] saw feed changes estimated time and labor cost")


def test_pricing_math_and_totals() -> None:
    s = compute_cut_summary(_result_two_formats())
    rates = PricingRates(per_m2=100.0, per_piece=2.0, per_hour=60.0)
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


def test_catalog_board_rate_overrides_an_old_material_thickness_rate() -> None:
    """The PA6G 25 mm regression: XLSX price beats a stale 1223 zł/m² setting."""
    material = "PA6G PŁYTA CZARNA"
    # 1000×2000 board at 977 zł/m² => complete board value 1954 zł.
    stock = SheetStock(material, 25, 1000, 2000, 1, price=1954.0)
    part = SheetPart("A", 300, 200, 1, material, 25)
    result = OptimizationResult(
        job_type="sheet",
        algorithm="test",
        sheet_layouts=[SheetLayout(stock=stock, sheet_index=1, parts=[PlacedSheetPart(part, 0, 0, 300, 200)])],
    )
    summary = compute_cut_summary(result)
    assert abs(summary.formats[0].catalog_price_m2 - 977.0) < _TOL

    stale_rate = PricingRates(per_m2={f"{material}|25": 1223.0})
    pricing = compute_pricing(summary, stale_rate)
    assert abs(pricing.material_cost - summary.formats[0].gross_m2 * 977.0) < _TOL

    from app.simple_window import CutInfoDialog
    dialog = CutInfoDialog(None, result)
    rate_control = dialog._rate_m2_dict[f"{material}|25"]
    assert abs(rate_control.value() - 977.0) < _TOL
    assert rate_control.isReadOnly()
    dialog.close()
    print("[OK] exact catalog board rate overrides stale material/thickness rate")


def test_zero_rates_is_zero() -> None:
    s = compute_cut_summary(_result_two_formats())
    p = compute_pricing(s, PricingRates())
    assert p.total == 0.0
    assert PricingRates().is_zero
    print("[OK] zero rates -> zero cost")


def test_identical_layouts_in_a_stack_reduce_machine_time_not_piece_count() -> None:
    layouts = []
    for index in range(4):
        stock = SheetStock("standard", 18, 1000, 1000, 1, stack_size=2)
        part = SheetPart("A", 200, 200, 1, "standard", 18)
        layouts.append(SheetLayout(stock=stock, sheet_index=index + 1, parts=[PlacedSheetPart(part, 0, 0, 200, 200)]))
    stacked = OptimizationResult(job_type="sheet", algorithm="test", sheet_layouts=layouts)

    single_layouts = [
        SheetLayout(stock=SheetStock("standard", 18, 1000, 1000, 1, stack_size=1), sheet_index=index + 1, parts=list(layout.parts))
        for index, layout in enumerate(layouts)
    ]
    unstacked = OptimizationResult(job_type="sheet", algorithm="test", sheet_layouts=single_layouts)

    stacked_summary = compute_cut_summary(stacked)
    unstacked_summary = compute_cut_summary(unstacked)
    assert stacked_summary.total_pieces == unstacked_summary.total_pieces == 4
    assert abs(stacked_summary.total_time_s * 2 - unstacked_summary.total_time_s) < 1e-6
    print("[OK] stack of two halves machine time for four identical boards")


def test_cut_info_dialog_renders() -> None:
    from app.simple_window import CutInfoDialog

    result = _result_two_formats()
    dlg = CutInfoDialog(None, result)
    # 2 formats + 1 totals row
    assert dlg._table.rowCount() == 3, dlg._table.rowCount()
    assert dlg._table.columnCount() == 11
    assert dlg.width() >= 1280
    assert dlg._table.columnWidth(10) >= 100
    assert dlg._table.horizontalHeaderItem(3).text() == "Odpad prod. [m²]"
    assert dlg._table.horizontalHeader().sectionResizeMode(10).name == "Stretch"
    # totals row last cell shows "RAZEM"
    assert dlg._table.item(2, 0).text() == "RAZEM"
    dlg.close()
    print("[OK] CutInfoDialog renders: 2 formats + RAZEM row, 11 columns")


if __name__ == "__main__":
    test_crosscut_tail_inside_the_consumed_strip_is_customer_waste()
    test_only_the_untouched_meter_tail_is_returned_to_warehouse()
    test_reusable_remnants_are_not_billed_as_scrap()
    test_per_format_time_positive()
    test_saw_feed_changes_time_and_labor_cost()
    test_pricing_math_and_totals()
    test_catalog_board_rate_overrides_an_old_material_thickness_rate()
    test_zero_rates_is_zero()
    test_identical_layouts_in_a_stack_reduce_machine_time_not_piece_count()
    test_cut_info_dialog_renders()
    print("\ntest_cut_info_pricing: OK")

"""Regression tests for multi-board order groups (feature B):
isolated per-group cutting + aggregated whole-order metrics.

Run:  python tests/test_order_groups.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from algorithms.cut_metrics import compute_cut_summary
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_order

_app = QApplication.instance() or QApplication([])


def _group(material: str, thickness: int, stock_wh, part_wh, qty) -> Project:
    p = Project()
    p.meta.material = material
    p.sheet_stock = [SheetStock(material, thickness, stock_wh[0], stock_wh[1], 5,
                                allow_rotation=True)]
    p.sheet_parts = [SheetPart("X", part_wh[0], part_wh[1], qty, material, thickness,
                               allow_rotation=True)]
    p.settings = OptimizationSettings(job_type="sheet",
                                      algorithm="Vertical Segmented Guillotine",
                                      kerf=5.0, kerf_tolerance=0.2)
    return p


def test_order_merges_two_groups() -> None:
    g1 = _group("Egger 18", 18, (2000, 1000), (500, 400), 4)
    g2 = _group("HDF 3", 3, (1500, 1000), (300, 300), 6)
    result = optimize_sheet_order([g1, g2])

    assert result.sheet_layouts, "no layouts produced"
    # boards numbered sequentially across groups
    indices = [l.sheet_index for l in result.sheet_layouts]
    assert indices == sorted(indices), indices
    # each layout tagged with its group label
    labels = {getattr(l, "group_label", None) for l in result.sheet_layouts}
    assert "Egger 18" in labels and "HDF 3" in labels, labels
    print(f"[OK] order merges 2 groups: labels={labels}")


def test_group_isolation() -> None:
    """Parts of one group must never appear on the other group's boards."""
    g1 = _group("Egger 18", 18, (2000, 1000), (500, 400), 4)
    g2 = _group("HDF 3", 3, (1500, 1000), (300, 300), 6)
    result = optimize_sheet_order([g1, g2])
    for layout in result.sheet_layouts:
        label = getattr(layout, "group_label", None)
        for placed in layout.parts:
            mat = placed.part.material
            assert mat == label, f"part material {mat} on board labelled {label}"
    print("[OK] group isolation: each board only holds its own group's parts")


def test_aggregated_quote_covers_all_groups() -> None:
    g1 = _group("Egger 18", 18, (2000, 1000), (500, 400), 4)
    g2 = _group("HDF 3", 3, (1500, 1000), (300, 300), 6)
    result = optimize_sheet_order([g1, g2])
    summary = compute_cut_summary(result)
    # both group's formats present, total pieces = 4 + 6
    assert summary.total_pieces == 10, summary.total_pieces
    assert summary.total_board_area_m2 > 0
    assert summary.total_waste_m2 >= 0
    print(f"[OK] aggregated quote: {summary.total_pieces} szt., "
          f"material {summary.total_board_area_m2:.2f} m2")


def test_single_group_passthrough() -> None:
    g1 = _group("Egger 18", 18, (2000, 1000), (500, 400), 4)
    result = optimize_sheet_order([g1])
    assert result.sheet_layouts
    print("[OK] single group passes through to plain optimize")


def test_order_group_widget_collect() -> None:
    from app.simple_window import OrderGroupWidget

    g = OrderGroupWidget(2)
    assert g.is_empty()
    g.material.setText("Sklejka 15")
    g.thickness.setValue(15)
    # fill one stock row + one parts row
    g.stock_table.item(0, 0).setText("2000")
    g.stock_table.item(0, 1).setText("1000")
    g.stock_table.item(0, 2).setText("3")
    g.parts_table.item(0, 0).setText("400")
    g.parts_table.item(0, 1).setText("300")
    g.parts_table.item(0, 2).setText("10")
    assert not g.is_empty()
    stock = g.collect_stock(True)
    parts = g.collect_parts(True)
    assert len(stock) == 1 and stock[0].material == "Sklejka 15" and stock[0].thickness == 15
    assert len(parts) == 1 and parts[0].material == "Sklejka 15" and parts[0].quantity == 10
    g.close()
    print("[OK] OrderGroupWidget collects stock+parts with its own material/thickness")


def test_order_group_widget_roundtrip() -> None:
    from app.simple_window import OrderGroupWidget

    g = OrderGroupWidget(2)
    g.material.setText("Egger U702")
    g.thickness.setValue(18)
    g.stock_table.item(0, 0).setText("2000")
    g.stock_table.item(0, 1).setText("1000")
    g.stock_table.item(0, 2).setText("4")
    g.parts_table.item(0, 0).setText("500")
    g.parts_table.item(0, 1).setText("400")
    g.parts_table.item(0, 2).setText("8")
    data = g.to_dict()

    g2 = OrderGroupWidget(3)
    g2.load_dict(data)
    assert g2.material.text() == "Egger U702"
    assert g2.thickness.value() == 18
    assert g2.to_dict()["stock"] == [["2000", "1000", "4"]]
    assert g2.to_dict()["parts"] == [["500", "400", "8"]]
    g.close(); g2.close()
    print("[OK] OrderGroupWidget to_dict/load_dict round-trip")


def test_window_serialize_restore_groups() -> None:
    from app.simple_window import SimpleCutWindow

    win = SimpleCutWindow()
    try:
        group = win._create_order_group()
        group.material.setText("HDF 3")
        group.thickness.setValue(3)
        group.stock_table.item(0, 0).setText("1500")
        group.stock_table.item(0, 1).setText("1000")
        group.stock_table.item(0, 2).setText("2")
        group.parts_table.item(0, 0).setText("300")
        group.parts_table.item(0, 1).setText("300")
        group.parts_table.item(0, 2).setText("6")

        serialized = win._serialize_order_groups()
        assert len(serialized) == 1, serialized
        assert serialized[0]["material"] == "HDF 3"

        # wipe and restore from serialized form
        win._clear_order_groups()
        assert win._order_groups == []
        win._restore_order_groups(serialized)
        assert len(win._order_groups) == 1
        restored = win._order_groups[0].to_dict()
        assert restored["material"] == "HDF 3"
        assert restored["stock"] == [["1500", "1000", "2"]]
        assert restored["parts"] == [["300", "300", "6"]]
    finally:
        win.deleteLater()
    print("[OK] window serialize -> restore rebuilds groups intact")


if __name__ == "__main__":
    test_order_merges_two_groups()
    test_group_isolation()
    test_aggregated_quote_covers_all_groups()
    test_single_group_passthrough()
    test_order_group_widget_collect()
    test_order_group_widget_roundtrip()
    test_window_serialize_restore_groups()
    print("\ntest_order_groups: OK")

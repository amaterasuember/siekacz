from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from algorithms.smart_stock_mix import optimize_smart_stock_mix
from app.material_catalog import MaterialCatalogEntry
from app.simple_window import SimpleCutWindow
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_project


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _scenario() -> tuple[list[SheetStock], list[SheetPart]]:
    stock = [
        SheetStock("M", 10, 1000, 2000, 1, price=200),
        SheetStock("M", 10, 1500, 3000, 1, price=450),
    ]
    parts = [
        SheetPart("BIG", 1400, 1400, 1, "M", 10),
        SheetPart("SMALL", 900, 900, 3, "M", 10),
    ]
    return stock, parts


def test_smart_mix_uses_small_and_large_supplier_formats() -> None:
    stock, parts = _scenario()
    result = optimize_smart_stock_mix(
        stock,
        parts,
        kerf=5.2,
        min_reusable_size=80,
        optimization_mode="sport",
    )
    assert not result.unplaced_sheet_parts
    # The optimizer canonicalizes the physical orientation, so compare supplier
    # formats independently of which side is rendered as width.
    counts = Counter(tuple(sorted((round(layout.stock.width), round(layout.stock.height)))) for layout in result.sheet_layouts)
    assert counts[(1000, 2000)] == 2
    assert counts[(1500, 3000)] == 1
    assert sum(layout.area for layout in result.sheet_layouts) < 2 * 1500 * 3000
    assert all(layout.is_guillotine_feasible for layout in result.sheet_layouts)
    assert any(message.startswith("Inteligentny dobór formatów:") for message in result.messages)
    print("[OK] smart planner combines small and large legal supplier sheets")


def test_worker_dispatches_smart_stock_mode() -> None:
    stock, parts = _scenario()
    project = Project(
        sheet_stock=stock,
        sheet_parts=parts,
        settings=OptimizationSettings(
            algorithm="auto",
            smart_stock_mode=True,
            kerf=5,
            min_reusable_offcut_size=80,
            optimization_mode="sport",
        ),
    )
    result = optimize_sheet_project(project)
    assert result.algorithm == "Inteligentny dobór formatów"
    assert not result.unplaced_sheet_parts
    assert not result.missing_sheet_layouts
    print("[OK] worker routes smart projects through the global format planner")


def test_ui_expands_priced_catalog_formats_and_persists_the_flag() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        material = "PP PŁYTA SZARA"
        window._material_catalog = [
            MaterialCatalogEntry("PP", 10, 40, 40, material, width=1000, height=2000),
            MaterialCatalogEntry("PP", 10, 42, 42, material, width=1500, height=3000),
        ]
        manual = [SheetStock(material, 10, 1000, 2000, 7, allow_rotation=True)]
        parts = [SheetPart("A", 300, 300, 4, material, 10)]
        expanded = window._smart_catalog_stock(parts, manual)
        assert {(item.width, item.height) for item in expanded} == {(1000.0, 2000.0), (1500.0, 3000.0)}
        assert all(item.quantity == 1 and item.source == "smart-candidate" for item in expanded)
        assert sorted(item.price for item in expanded) == [80.0, 189.0]

        window.stock_table.setRowCount(0)
        window._add_stock_row(manual[0])
        window.parts.setRowCount(0)
        window.add_part_row([10, 300, 300, 4, material])
        window.smart_stock_checkbox.setChecked(True)
        project = window._project_for_calculation(window._collect_parts())
        assert project.settings.smart_stock_mode is True
        assert len(project.sheet_stock) == 2
        restored = Project.from_dict(project.to_dict())
        assert restored.settings.smart_stock_mode is True
        print("[OK] UI exposes every priced format and saves the smart-mode flag")
    finally:
        window.close()


def test_smart_mode_keeps_a_visible_manual_format_when_catalog_sheets_are_too_small() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        material = "PP PŁYTA NATURALNA"
        window._material_catalog = [
            MaterialCatalogEntry("PP", 1, 40, 40, material, width=1000, height=1000),
            MaterialCatalogEntry("PP", 1, 42, 42, material, width=1000, height=1050),
        ]
        manual = [SheetStock(material, 1, 1500, 3000, 1, allow_rotation=True)]
        parts = [SheetPart("A", 500, 1500, 5, material, 1)]

        window.stock_table.setRowCount(0)
        window._add_stock_row(manual[0])
        window.smart_stock_checkbox.setChecked(True)
        project = window._project_for_calculation(parts)

        assert {(item.width, item.height) for item in project.sheet_stock} == {
            (1000.0, 1000.0),
            (1000.0, 1050.0),
            (1500.0, 3000.0),
        }
        assert project.settings.smart_stock_mode is True
        assert window._oversized_part_issues(project) == []

        result = optimize_smart_stock_mix(
            project.sheet_stock,
            parts,
            kerf=5.2,
            min_reusable_size=80,
            optimization_mode="sport",
        )
        assert not result.unplaced_sheet_parts
        assert any(
            {round(layout.stock.width), round(layout.stock.height)} == {1500, 3000}
            for layout in result.sheet_layouts
        )
        print("[OK] smart mode keeps a visible manual sheet when catalogue formats do not fit")
    finally:
        window.close()


if __name__ == "__main__":
    test_smart_mix_uses_small_and_large_supplier_formats()
    test_worker_dispatches_smart_stock_mode()
    test_ui_expands_priced_catalog_formats_and_persists_the_flag()
    test_smart_mode_keeps_a_visible_manual_format_when_catalog_sheets_are_too_small()
    print("SMART STOCK MODE TESTS OK")

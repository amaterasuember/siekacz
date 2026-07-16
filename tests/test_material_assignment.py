from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QToolButton

from app.material_catalog import MaterialCatalogEntry, catalog_family_label
from app.simple_window import FIXED_SHEET_PRESETS, SimpleCutWindow, _material_badge_spec
from core.models import Project, SheetPart, SheetStock


def test_stock_and_parts_keep_their_own_material_at_the_same_thickness() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window.stock_table.setRowCount(0)
        window._add_stock_row({
            "material": "PA6 PŁYTA NATURALNA",
            "thickness": 20,
            "width": 2000,
            "height": 1000,
            "quantity": 1,
        })
        window._add_stock_row({
            "material": "POM H PŁYTA CZARNA",
            "thickness": 20,
            "width": 2000,
            "height": 1000,
            "quantity": 1,
        })
        window.parts.setRowCount(0)
        window.add_part_row([20, 100, 100, 2, "PA6 PŁYTA NATURALNA"])
        window.add_part_row([20, 80, 80, 3, "POM H PŁYTA CZARNA"])

        stock = window._collect_stock()
        parts = window._collect_parts()
        assert [item.material for item in stock] == [
            "PA6 PŁYTA NATURALNA",
            "POM H PŁYTA CZARNA",
        ]
        assert [item.material for item in parts] == [
            "PA6 PŁYTA NATURALNA",
            "POM H PŁYTA CZARNA",
        ]
        project = window._project_for_calculation(parts)
        groups = window._split_project_by_thickness(project)
        assert [(group.meta.material, group.sheet_parts[0].material) for group in groups] == [
            ("PA6 PŁYTA NATURALNA", "PA6 PŁYTA NATURALNA"),
            ("POM H PŁYTA CZARNA", "POM H PŁYTA CZARNA"),
        ]
        assert window.stock_table.cellWidget(0, 4).text() == "PA6"
        assert window.stock_table.cellWidget(1, 4).text() == "POM-H"
    finally:
        window.close()


def test_material_badges_use_compact_codes_and_a_dash_when_unselected() -> None:
    assert _material_badge_spec("")[0] == "-"
    assert _material_badge_spec("PŁYTA PE HD1000 ZIELONA")[0] == "PE1000"
    assert _material_badge_spec("PA6 PŁYTA NATURALNA")[0] == "PA6"
    assert _material_badge_spec("POM C PŁYTA CZARNA")[0] == "POM-C"
    assert _material_badge_spec("POM C PŁYTA NATURALNA")[1] == "#ffffff"
    assert _material_badge_spec("PŁYTA PE HD1000 CZARNA")[1] == "#202936"


def test_input_table_headers_use_intentional_short_labels_with_full_hints() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        assert [window.stock_table.horizontalHeaderItem(column).text() for column in range(5)] == [
            "GR.", "SZER.", "WYS.", "SZT.", "MATERIAŁ",
        ]
        assert [window.parts.horizontalHeaderItem(column).text() for column in range(6)] == [
            "#", "GR.", "SZER.", "DŁ.", "SZT.", "MATERIAŁ",
        ]
        assert window.stock_table.horizontalHeaderItem(1).toolTip() == "Szerokość [mm]"
        assert window.parts.horizontalHeaderItem(3).toolTip() == "Długość [mm]"
        assert window.stock_table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeMode.Fixed
        assert window.stock_table.horizontalHeader().sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch
        assert window.parts.horizontalHeader().sectionResizeMode(5) == QHeaderView.ResizeMode.Stretch
    finally:
        window.close()


def test_material_badge_color_follows_the_material_color_name() -> None:
    assert _material_badge_spec("PA6 NATURALNA")[1:] == ("#ffffff", "#111827")
    assert _material_badge_spec("PA6 CZARNA")[1:] == ("#202936", "#f3f7ff")
    assert _material_badge_spec("PE1000 ZIELONA")[1:] == ("#188c63", "#effff8")


def test_material_badge_menu_updates_an_individual_part_row() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window._material_catalog = [
            MaterialCatalogEntry("POM C", 18, 0, 100, "POM C NATURALNA"),
            MaterialCatalogEntry("PE1000", 18, 0, 100, "PE1000 CZARNA"),
        ]
        window.parts.setRowCount(0)
        window.add_part_row([18, 100, 100, 1, "POM C NATURALNA"])
        badge = window.parts.cellWidget(0, 5)
        assert isinstance(badge, QToolButton)
        action = next(action for action in badge.menu().actions() if "PE1000" in action.text())
        action.trigger()
        assert window.parts.item(0, 5).text() == "PE1000 CZARNA"
        assert window.parts.cellWidget(0, 5).text() == "PE1000"
    finally:
        window.close()


def test_blank_material_rows_still_build_a_thickness_only_project() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window.stock_table.setRowCount(0)
        window._add_stock_row({"material": "", "thickness": 18, "width": 2000, "height": 1000, "quantity": 1})
        window.stock_table.item(0, 4).setText("")
        window._set_material_badge(window.stock_table, 0, 4, "")
        window.parts.setRowCount(0)
        window.add_part_row([18, 100, 100, 2, ""])
        project = window._project_for_calculation(window._collect_parts())
        assert project.sheet_stock[0].material == "standard"
        assert project.sheet_parts[0].material == "standard"
    finally:
        window.close()


def test_stock_stack_control_and_material_defaults_are_available() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        assert (1250.0, 2500.0) in FIXED_SHEET_PRESETS
        assert window.catalog_thickness_selector.isEditable()
        assert window.stock_table.columnCount() == 6
        window.stock_table.setRowCount(0)
        window._add_stock_row({"thickness": 18, "width": 1000, "height": 2000, "quantity": 1, "stack_size": 1})
        window._set_stock_stack_size(0, 10)
        assert window.stock_table.item(0, 5).text() == ""
        assert window.stock_table.item(0, 5).data(Qt.ItemDataRole.UserRole) == 10
        stack_button = window.stock_table.cellWidget(0, 5)
        assert stack_button.text() == "10 ▾"
        quick_actions = [action for action in stack_button.menu().actions() if action.isCheckable()]
        assert len(quick_actions) >= 10
        assert [action.text() for action in quick_actions[:3]] == ["1 płyta", "2 płyty", "3 płyty"]
        quick_actions[2].trigger()
        assert window._stock_cell_text(0, 5) == "3"
        assert sum(action.isChecked() for action in quick_actions) == 1
        stack_button = window.stock_table.cellWidget(0, 5)
        assert stack_button.text() == "3 ▾"
        assert window._default_sheet_preset_for_material("POM C NATURALNA") == (1000.0, 2000.0)
        assert window._default_sheet_preset_for_material("PE1000 ZIELONA") == (1000.0, 2000.0)
    finally:
        window.close()


def test_thickness_selector_opens_catalog_options_and_accepts_manual_values() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        entries = [
            MaterialCatalogEntry("PA6", 8, 0, 100, "PA6 PŁYTA GR. 8 MM"),
            MaterialCatalogEntry("PA6", 18, 0, 100, "PA6 PŁYTA GR. 18 MM"),
            MaterialCatalogEntry("PA6", 30, 0, 100, "PA6 PŁYTA GR. 30 MM"),
        ]
        window._material_catalog = entries
        window._populate_material_selector()
        window.material_selector.setCurrentIndex(window.material_selector.findData(catalog_family_label(entries[0])))
        app.processEvents()

        thickness = window.catalog_thickness_selector
        assert thickness.isEditable()
        assert thickness.count() == 3

        window.show()
        QTest.mouseClick(thickness.lineEdit(), Qt.MouseButton.LeftButton)
        app.processEvents()
        assert thickness.view().isVisible()
        thickness.hidePopup()
    finally:
        window.close()


def test_manual_thickness_overrides_a_selected_catalog_entry() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window._material_catalog = [
            MaterialCatalogEntry("PA6", 18, 0, 100, "PA6 NATURALNA"),
        ]
        window._populate_material_selector()
        window.stock_table.setRowCount(0)
        window._add_stock_row({"thickness": 18, "width": 1000, "height": 2000, "quantity": 1})
        window.material_selector.setCurrentIndex(0)
        window.catalog_thickness_selector.setEditText("21 mm")
        window._apply_catalog_selection()
        assert window.stock_table.item(0, 0).text() == "21"
    finally:
        window.close()


def test_generic_stock_is_not_reused_across_separate_material_groups() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        project = Project(
            sheet_stock=[SheetStock("standard", 18, 1000, 2000, 1)],
            sheet_parts=[
                SheetPart("PA", 100, 100, 1, "PA6 NATURALNA", 18),
                SheetPart("POM", 100, 100, 1, "POM C NATURALNA", 18),
            ],
        )
        groups = window._split_project_by_thickness(project)
        assert len(groups) == 1
        assert len(groups[0].sheet_stock) == 1
        assert len(groups[0].sheet_parts) == 2
    finally:
        window.close()


def test_selector_updates_the_last_board_row_even_when_complete() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        entry = MaterialCatalogEntry(
            material="PŁYTA PE HD1000 CZARNA",
            thickness=12,
            net_price_m2=0,
            gross_price_m2=402.21,
            product_name="PŁYTA PE HD1000 CZARNA GR. 12 MM",
        )
        window.stock_table.setRowCount(0)
        window._add_stock_row({"material": "POM C PŁYTA NATURALNA", "thickness": 8, "width": 1000, "height": 2000, "quantity": 1})
        window._add_stock_row({"material": "POM C PŁYTA NATURALNA", "thickness": 8, "width": 2000, "height": 1000, "quantity": 1})
        row = window.stock_table.rowCount() - 1

        window.material_selector.blockSignals(True)
        window.material_selector.clear()
        window.material_selector.addItem("PŁYTA PE HD1000 CZARNA", "PŁYTA PE HD1000 CZARNA")
        window.material_selector.setCurrentIndex(0)
        window.material_selector.blockSignals(False)
        window.catalog_thickness_selector.blockSignals(True)
        window.catalog_thickness_selector.clear()
        window.catalog_thickness_selector.addItem("12 mm", entry)
        window.catalog_thickness_selector.setCurrentIndex(0)
        window.catalog_thickness_selector.blockSignals(False)
        window._apply_catalog_selection()

        assert window.stock_table.item(row, 0).text() == "12"
        assert window.stock_table.item(row, 4).text() == "PŁYTA PE HD1000 CZARNA"
        assert window.stock_table.item(row, 1).text() == "1000"
        assert window.stock_table.item(row, 2).text() == "2000"
        assert window.stock_table.item(0, 0).text() == "8"
        assert window.stock_table.item(0, 4).text() == "POM C PŁYTA NATURALNA"
    finally:
        window.close()


if __name__ == "__main__":
    test_stock_and_parts_keep_their_own_material_at_the_same_thickness()
    test_material_badges_use_compact_codes_and_a_dash_when_unselected()
    test_input_table_headers_use_intentional_short_labels_with_full_hints()
    test_material_badge_color_follows_the_material_color_name()
    test_material_badge_menu_updates_an_individual_part_row()
    test_blank_material_rows_still_build_a_thickness_only_project()
    test_stock_stack_control_and_material_defaults_are_available()
    test_thickness_selector_opens_catalog_options_and_accepts_manual_values()
    test_manual_thickness_overrides_a_selected_catalog_entry()
    test_generic_stock_is_not_reused_across_separate_material_groups()
    test_selector_updates_the_last_board_row_even_when_complete()
    print("test_material_assignment: OK")

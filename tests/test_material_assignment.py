from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QHeaderView, QToolButton
from openpyxl import Workbook

from app.material_catalog import MaterialCatalogEntry, catalog_family_label
from app.simple_window import (
    FIXED_SHEET_PRESETS,
    STOCK_FORMAT_COLUMN,
    STOCK_HEIGHT_COLUMN,
    STOCK_MATERIAL_COLUMN,
    STOCK_PRIORITY_COLUMN,
    STOCK_QUANTITY_COLUMN,
    STOCK_STACK_COLUMN,
    STOCK_THICKNESS_COLUMN,
    STOCK_WIDTH_COLUMN,
    PART_HEIGHT_COLUMN,
    PART_MATERIAL_COLUMN,
    PART_THICKNESS_COLUMN,
    PART_WIDTH_COLUMN,
    SimpleCutWindow,
    TutorialDialog,
    _material_badge_spec,
)
from core.models import Project, SheetPart, SheetStock
from database import repositories


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
        assert window.stock_table.cellWidget(0, STOCK_MATERIAL_COLUMN).text() == "PA6"
        assert window.stock_table.cellWidget(1, STOCK_MATERIAL_COLUMN).text() == "POM-H"
    finally:
        window.close()


def test_material_badges_use_compact_codes_and_a_dash_when_unselected() -> None:
    assert _material_badge_spec("")[0] == "-"
    assert _material_badge_spec("PŁYTA PE HD1000 ZIELONA")[0] == "PE1000"
    assert _material_badge_spec("PA6 PŁYTA NATURALNA")[0] == "PA6"
    assert _material_badge_spec("POM C PŁYTA CZARNA")[0] == "POM-C"
    assert _material_badge_spec("POM C PŁYTA NATURALNA")[1] == "#ffffff"
    assert _material_badge_spec("PŁYTA PE HD1000 CZARNA")[1] == "#202936"


def test_input_table_headers_use_full_labels_and_height_before_width() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        assert [window.stock_table.horizontalHeaderItem(column).text() for column in range(8)] == [
            "MATERIAŁ", "GRUBOŚĆ", "WYSOKOŚĆ", "SZEROKOŚĆ", "FORMAT", "ILOŚĆ", "", "",
        ]
        assert [window.parts.horizontalHeaderItem(column).text() for column in range(5)] == [
            "MATERIAŁ", "GRUBOŚĆ", "WYSOKOŚĆ", "SZEROKOŚĆ", "ILOŚĆ",
        ]
        assert window.stock_table.horizontalHeaderItem(STOCK_HEIGHT_COLUMN).toolTip() == "Wysokość [mm]"
        assert window.stock_table.horizontalHeaderItem(STOCK_WIDTH_COLUMN).toolTip() == "Szerokość [mm]"
        assert window.parts.horizontalHeaderItem(PART_HEIGHT_COLUMN).toolTip() == "Wysokość [mm]"
        assert window.parts.horizontalHeaderItem(PART_WIDTH_COLUMN).toolTip() == "Szerokość [mm]"
        assert window.stock_table.horizontalHeader().sectionResizeMode(STOCK_THICKNESS_COLUMN) == QHeaderView.ResizeMode.Fixed
        assert window.stock_table.horizontalHeader().sectionResizeMode(STOCK_MATERIAL_COLUMN) == QHeaderView.ResizeMode.Fixed
        assert window.parts.horizontalHeader().sectionResizeMode(PART_MATERIAL_COLUMN) == QHeaderView.ResizeMode.Stretch
        material_width = window.stock_table.columnWidth(STOCK_MATERIAL_COLUMN)
        format_width = window.stock_table.columnWidth(STOCK_FORMAT_COLUMN)
        assert material_width > format_width
        # Full labels own enough space; the icon-only stack column stays small.
        assert window.stock_table.columnWidth(STOCK_THICKNESS_COLUMN) >= 78
        assert window.stock_table.columnWidth(STOCK_WIDTH_COLUMN) >= 90
        assert window.stock_table.columnWidth(STOCK_FORMAT_COLUMN) >= 76
        assert window.stock_table.columnWidth(STOCK_PRIORITY_COLUMN) < format_width
        assert window.stock_table.columnWidth(STOCK_QUANTITY_COLUMN) < window.stock_table.columnWidth(STOCK_HEIGHT_COLUMN)
        assert window.stock_table.columnWidth(STOCK_QUANTITY_COLUMN) < window.stock_table.columnWidth(STOCK_WIDTH_COLUMN)
        part_widths = {
            window.parts.columnWidth(column)
            for column in range(window.parts.columnCount())
        }
        assert max(part_widths) - min(part_widths) <= 1
        assert window.stock_table.horizontalHeader().sectionResizeMode(STOCK_STACK_COLUMN) == QHeaderView.ResizeMode.Stretch
        assert window.stock_table.columnWidth(STOCK_STACK_COLUMN) >= 64
        initial_material = window.stock_table.item(0, STOCK_MATERIAL_COLUMN).text().strip()
        initial_thickness = float(window.stock_table.item(0, STOCK_THICKNESS_COLUMN).text())
        initial_height = float(window.stock_table.item(0, STOCK_HEIGHT_COLUMN).text())
        initial_width = float(window.stock_table.item(0, STOCK_WIDTH_COLUMN).text())
        assert initial_material
        assert any(
            catalog_family_label(entry).casefold() == initial_material.casefold()
            and abs(entry.thickness - initial_thickness) < 0.001
            and abs(entry.width - initial_height) < 0.001
            and abs(entry.height - initial_width) < 0.001
            for entry in window._material_catalog
        )
    finally:
        window.close()


def test_material_badge_color_follows_the_material_color_name() -> None:
    assert _material_badge_spec("PA6 NATURALNA")[1:] == ("#ffffff", "#111827")
    assert _material_badge_spec("PA6 CZARNA")[1:] == ("#202936", "#f3f7ff")
    assert _material_badge_spec("PE1000 ZIELONA")[1:] == ("#188c63", "#effff8")
    assert _material_badge_spec("PP PŁYTA SZARA RAL 7032")[1:] == ("#eadfbd", "#3b3425")


def test_material_menu_does_not_repeat_the_compact_code() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window._material_catalog = [
            MaterialCatalogEntry("PA6G", 10, 100, 100, "PA6G PŁYTA CZARNA"),
            MaterialCatalogEntry("PEEK", 10, 100, 100, "PEEK PŁYTA NATURALNA"),
        ]
        window.parts.setRowCount(0)
        window.add_part_row([10, 100, 100, 1, "PA6G PŁYTA CZARNA"])
        badge = window.parts.cellWidget(0, PART_MATERIAL_COLUMN)
        labels = [action.text() for action in badge.menu().actions()]
        assert "PA6G PŁYTA CZARNA" in labels
        assert "PEEK PŁYTA NATURALNA" in labels
        assert all("PA6G  PA6G" not in label and "PE  PEEK" not in label for label in labels)
    finally:
        window.close()


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
        badge = window.parts.cellWidget(0, PART_MATERIAL_COLUMN)
        assert isinstance(badge, QToolButton)
        action = next(action for action in badge.menu().actions() if "PE1000" in action.text())
        action.trigger()
        assert window.parts.item(0, PART_MATERIAL_COLUMN).text() == "PE1000 CZARNA"
        assert window.parts.cellWidget(0, PART_MATERIAL_COLUMN).text() == "PE1000"
    finally:
        window.close()


def test_part_table_displays_height_before_width_without_swapping_geometry() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window.parts.setRowCount(0)
        window.add_part_row([18, 320, 140, 2, "PA6 NATURALNA"])
        assert window.parts.item(0, PART_HEIGHT_COLUMN).text() == "140"
        assert window.parts.item(0, PART_WIDTH_COLUMN).text() == "320"
        part = window._collect_parts()[0]
        assert part.width == 320
        assert part.height == 140
    finally:
        window.close()


def test_dxf_part_upload_requires_the_experimental_pin_first() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        calls: list[str] = []
        window._unlock_experimental_tools = lambda: False
        original = QFileDialog.getOpenFileName
        QFileDialog.getOpenFileName = lambda *args, **kwargs: calls.append("opened") or ("", "")
        try:
            window.import_dxf_parts()
        finally:
            QFileDialog.getOpenFileName = original
        assert calls == []
    finally:
        window.close()


def test_blank_material_rows_still_build_a_thickness_only_project() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        # Projects without any supplier catalogue retain the legacy generic
        # "standard" material path.  With a catalogue, material is mandatory.
        window._material_catalog = []
        window.material_selector.setCurrentIndex(-1)
        window.material_selector.clearEditText()
        window.stock_table.setRowCount(0)
        window._add_stock_row({"material": "", "thickness": 18, "width": 2000, "height": 1000, "quantity": 1})
        window.stock_table.item(0, STOCK_MATERIAL_COLUMN).setText("")
        window._set_material_badge(window.stock_table, 0, STOCK_MATERIAL_COLUMN, "")
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
        assert window.stock_table.columnCount() == 8
        window.stock_table.setRowCount(0)
        window._add_stock_row({"thickness": 18, "width": 1000, "height": 2000, "quantity": 1, "stack_size": 1})
        window._set_stock_stack_size(0, 10)
        assert window.stock_table.item(0, STOCK_STACK_COLUMN).text() == ""
        assert window.stock_table.item(0, STOCK_STACK_COLUMN).data(Qt.ItemDataRole.UserRole) == 10
        stack_button = window.stock_table.cellWidget(0, STOCK_STACK_COLUMN)
        assert stack_button.text() == "10 ▾"
        quick_actions = [action for action in stack_button.menu().actions() if action.isCheckable()]
        assert len(quick_actions) >= 10
        assert [action.text() for action in quick_actions[:3]] == ["1 płyta", "2 płyty", "3 płyty"]
        quick_actions[2].trigger()
        assert window._stock_cell_text(0, STOCK_STACK_COLUMN) == "3"
        assert sum(action.isChecked() for action in quick_actions) == 1
        stack_button = window.stock_table.cellWidget(0, STOCK_STACK_COLUMN)
        assert stack_button.text() == "3 ▾"
        window._set_stock_priority(0, True)
        priority_item = window.stock_table.item(0, STOCK_PRIORITY_COLUMN)
        assert priority_item.text() == ""
        assert priority_item.data(Qt.ItemDataRole.UserRole) == 1
        priority = window.stock_table.cellWidget(0, STOCK_PRIORITY_COLUMN)
        assert priority.isChecked()
        assert priority.text() == "●"
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
        assert window.stock_table.item(0, STOCK_THICKNESS_COLUMN).text() == "21"
    finally:
        window.close()


def test_part_material_adds_one_matching_catalog_stock_with_selectable_formats() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        material = "PP PŁYTA CZARNA"
        window._material_catalog = [
            MaterialCatalogEntry("PP", 10, 0, 44, material, width=1000, height=2000),
            MaterialCatalogEntry("PP", 10, 0, 48, material, width=1250, height=2500),
        ]
        window.stock_table.setRowCount(0)
        window.parts.setRowCount(0)
        window.add_part_row([10, 120, 80, 1, material])
        window._set_row_material(window.parts, 0, PART_MATERIAL_COLUMN, material)

        assert window.stock_table.rowCount() == 1
        assert window.stock_table.item(0, STOCK_HEIGHT_COLUMN).text() == "1000"
        assert window.stock_table.item(0, STOCK_WIDTH_COLUMN).text() == "2000"
        formats = window.stock_table.cellWidget(0, STOCK_FORMAT_COLUMN)
        assert formats.count() == 3  # manual + two available supplier formats
        assert formats.currentText() == "1000 × 2000 mm"
        assert formats.itemText(0) == "-"
        assert "zł/m²" not in " ".join(formats.itemText(index) for index in range(formats.count()))
        assert window.stock_table.columnWidth(STOCK_FORMAT_COLUMN) < window.stock_table.columnWidth(STOCK_MATERIAL_COLUMN)
        formats.showPopup()
        assert formats.view().minimumWidth() >= 160
        formats.hidePopup()
        formats.setCurrentIndex(2)
        assert window.stock_table.item(0, STOCK_HEIGHT_COLUMN).text() == "1250"
        assert window.stock_table.item(0, STOCK_WIDTH_COLUMN).text() == "2500"

        window._set_row_material(window.parts, 0, PART_MATERIAL_COLUMN, material)
        assert window.stock_table.rowCount() == 1
    finally:
        window.close()


def test_stock_cost_uses_the_price_for_its_selected_supplier_format() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        material = "PP PŁYTA CZARNA"
        window._material_catalog = [
            MaterialCatalogEntry("PP", 10, 0, 44, material, width=1000, height=2000),
            MaterialCatalogEntry("PP", 10, 0, 48, material, width=1250, height=2500),
        ]
        window.stock_table.setRowCount(0)
        window._add_stock_row({"material": material, "thickness": 10, "width": 1250, "height": 2500, "quantity": 1})

        stock = window._collect_stock()[0]
        assert stock.price == 150.0  # 3.125 m² × 48 zł/m²
        picker = window.stock_table.cellWidget(0, STOCK_FORMAT_COLUMN)
        assert "48.00" in picker.itemData(2, Qt.ItemDataRole.ToolTipRole)
    finally:
        window.close()


def test_supplier_format_presets_are_limited_to_priced_catalog_intersections() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        material = "PP PŁYTA CZARNA"
        window._material_catalog = [
            MaterialCatalogEntry("PP", 10, 0, 44, material, width=1000, height=2000),
            MaterialCatalogEntry("PP", 10, 0, 48, material, width=1250, height=2500),
            MaterialCatalogEntry("PP", 12, 0, 62, material, width=1500, height=3000),
        ]
        window._populate_material_selector()
        window.material_selector.setCurrentIndex(window.material_selector.findData(material))
        window.catalog_thickness_selector.setCurrentIndex(0)
        window._refresh_recent_sheet_formats()

        preset_data = [window.recent_sheet_formats.itemData(i) for i in range(1, window.recent_sheet_formats.count())]
        preset_labels = [window.recent_sheet_formats.itemText(i) for i in range(1, window.recent_sheet_formats.count())]
        assert preset_data == ["1000|2000", "1250|2500"]
        assert all("zł/m²" in label for label in preset_labels)
        assert all("1500" not in label for label in preset_labels)
    finally:
        window.close()


def test_part_material_uses_a_priced_catalog_format_instead_of_static_preset() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        material = "PP PŁYTA ZIELONA"
        window._material_catalog = [
            MaterialCatalogEntry("PP", 12, 0, 61, material, width=1250, height=2500),
        ]
        window.stock_table.setRowCount(0)
        window.parts.setRowCount(0)
        window.add_part_row([12, 120, 80, 1, material])
        window._set_row_material(window.parts, 0, PART_MATERIAL_COLUMN, material)

        assert window.stock_table.rowCount() == 1
        assert window.stock_table.item(0, STOCK_HEIGHT_COLUMN).text() == "1250"
        assert window.stock_table.item(0, STOCK_WIDTH_COLUMN).text() == "2500"
        picker = window.stock_table.cellWidget(0, STOCK_FORMAT_COLUMN)
        assert picker.currentText() == "1250 × 2500 mm"
    finally:
        window.close()


def test_changed_uploaded_catalog_rebuilds_available_materials_and_thicknesses() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cennik.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "PVC"
            sheet.append(["LP", "Grubość", "Kolor", "Format"])
            sheet.append([None, None, None, "1000x2000"])
            sheet.append([1, 5, "naturalna", 40])
            workbook.save(path)

            settings = {
                "material_catalog_source": "uploaded",
                "material_catalog_path": str(path),
                "material_catalog_revision": "outdated",
            }

            def get_setting(key, default=None):
                return settings.get(key, default)

            def set_setting(key, value):
                settings[key] = value

            with patch.object(repositories, "get_setting", side_effect=get_setting), patch.object(
                repositories, "set_setting", side_effect=set_setting
            ):
                assert window._refresh_uploaded_material_catalog_if_changed()
                assert window.material_selector.findData("PVC PŁYTA NATURALNA") >= 0

                sheet.append([2, 12, "czarna", 65])
                workbook.save(path)
                assert window._refresh_uploaded_material_catalog_if_changed()
                black = window.material_selector.findData("PVC PŁYTA CZARNA")
                assert black >= 0
                window.material_selector.setCurrentIndex(black)
                assert window.catalog_thickness_selector.count() == 1
                assert window.catalog_thickness_selector.currentData() == 12.0
                assert "65.00" in window.catalog_thickness_selector.currentText()
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


def test_default_catalog_format_prefers_1000x2000_when_available() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        material = "POM C PŁYTA NATURALNA"
        window._material_catalog = [
            MaterialCatalogEntry("POM C", 10, 500, 500, material, width=620, height=2000),
            MaterialCatalogEntry("POM C", 10, 590, 590, material, width=1000, height=2000),
            MaterialCatalogEntry("POM C", 10, 610, 610, material, width=1250, height=2500),
        ]
        selected = window._default_catalog_format(material, 10)
        assert selected is not None
        assert (selected.width, selected.height) == (1000, 2000)
    finally:
        window.close()


def test_stock_cut_axis_is_selectable_and_saved_in_project() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window.stock_table.setRowCount(0)
        window._add_stock_row(
            {"material": "standard", "thickness": 10, "width": 610, "height": 1000, "quantity": 1}
        )
        assert window._stock_preferred_cut_axis(0) == "auto"
        window._toggle_stock_cut_axis(0, STOCK_HEIGHT_COLUMN)
        assert window._stock_preferred_cut_axis(0) == "x"
        assert window._collect_stock()[0].preferred_cut_axis == "x"
        window._toggle_stock_cut_axis(0, STOCK_HEIGHT_COLUMN)
        assert window._stock_preferred_cut_axis(0) == "auto"
    finally:
        window.close()


def test_selecting_part_thickness_moves_to_height() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        window.parts.setRowCount(0)
        window.add_part_row([10, "", "", "", "PVC PŁYTA SZARA"])
        window._set_part_material_thickness(0, "PVC PŁYTA SZARA", 10)
        app.processEvents()
        assert window.parts.currentRow() == 0
        assert window.parts.currentColumn() == PART_HEIGHT_COLUMN
    finally:
        window.close()


def test_tutorial_covers_the_key_new_user_flows() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    window.resize(1500, 850)
    window.show()
    dialog = TutorialDialog(window)
    try:
        combined = " ".join(title + " " + body for title, body in dialog.STEPS)
        for phrase in (
            "Formatki",
            "priorytet",
            "Kierunek",
            "Sztapel",
            "Projekty",
            "Wyślij",
            "cennik XLSX",
        ):
            assert phrase.casefold() in combined.casefold()
        dialog.show()
        app.processEvents()
        assert len(dialog.SPOTLIGHTS) == len(dialog.STEPS)
        assert dialog._target_rect.isValid()
        assert dialog._bubble.isVisible()
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        for index in range(len(dialog.STEPS)):
            dialog._step_index = index
            dialog._refresh_navigation()
            app.processEvents()
            assert dialog._target_rect.isValid()
            assert dialog.rect().contains(dialog._bubble.geometry())
            assert not dialog._bubble.geometry().intersects(
                dialog._target_rect.adjusted(-12, -12, 12, 12)
            )
        assert "Cennik" in dialog._title.text()
        dialog._step_index = len(dialog.STEPS) - 1
        dialog._next_step()
        assert dialog.completed
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.close()
        window.close()


def test_completed_tutorial_is_hidden_from_topbar_after_restart() -> None:
    app = QApplication.instance() or QApplication([])

    def stored_setting(key, default=None):
        return True if key == "tutorial_completed" else default

    with patch.object(repositories, "get_setting", side_effect=stored_setting):
        window = SimpleCutWindow()
    try:
        assert window._tutorial_completed
        assert window.tutorial_button.isHidden()
    finally:
        window.close()


def test_oversized_part_is_rejected_by_preflight_before_optimization() -> None:
    project = Project(
        sheet_stock=[SheetStock("POM C", 10, 610, 1000, 1)],
        sheet_parts=[SheetPart("za duża", 700, 1100, 1, "POM C", 10)],
    )
    issues = SimpleCutWindow._oversized_part_issues(project)
    assert len(issues) == 1
    assert "700 × 1100" in issues[0]
    assert "610 × 1000" in issues[0]


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

        assert window.stock_table.item(row, STOCK_THICKNESS_COLUMN).text() == "12"
        assert window.stock_table.item(row, STOCK_MATERIAL_COLUMN).text() == "PŁYTA PE HD1000 CZARNA"
        assert window.stock_table.item(row, STOCK_HEIGHT_COLUMN).text() == "1000"
        assert window.stock_table.item(row, STOCK_WIDTH_COLUMN).text() == "2000"
        assert window.stock_table.item(0, STOCK_THICKNESS_COLUMN).text() == "8"
        assert window.stock_table.item(0, STOCK_MATERIAL_COLUMN).text() == "POM C PŁYTA NATURALNA"
    finally:
        window.close()


if __name__ == "__main__":
    test_stock_and_parts_keep_their_own_material_at_the_same_thickness()
    test_material_badges_use_compact_codes_and_a_dash_when_unselected()
    test_input_table_headers_use_full_labels_and_height_before_width()
    test_material_badge_color_follows_the_material_color_name()
    test_material_menu_does_not_repeat_the_compact_code()
    test_material_badge_menu_updates_an_individual_part_row()
    test_part_table_displays_height_before_width_without_swapping_geometry()
    test_dxf_part_upload_requires_the_experimental_pin_first()
    test_blank_material_rows_still_build_a_thickness_only_project()
    test_stock_stack_control_and_material_defaults_are_available()
    test_thickness_selector_opens_catalog_options_and_accepts_manual_values()
    test_manual_thickness_overrides_a_selected_catalog_entry()
    test_part_material_adds_one_matching_catalog_stock_with_selectable_formats()
    test_stock_cost_uses_the_price_for_its_selected_supplier_format()
    test_supplier_format_presets_are_limited_to_priced_catalog_intersections()
    test_part_material_uses_a_priced_catalog_format_instead_of_static_preset()
    test_changed_uploaded_catalog_rebuilds_available_materials_and_thicknesses()
    test_generic_stock_is_not_reused_across_separate_material_groups()
    test_default_catalog_format_prefers_1000x2000_when_available()
    test_stock_cut_axis_is_selectable_and_saved_in_project()
    test_selecting_part_thickness_moves_to_height()
    test_tutorial_covers_the_key_new_user_flows()
    test_completed_tutorial_is_hidden_from_topbar_after_restart()
    test_oversized_part_is_rejected_by_preflight_before_optimization()
    test_selector_updates_the_last_board_row_even_when_complete()
    print("test_material_assignment: OK")

"""Regression coverage for part-driven stock material/thickness context."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.material_catalog import MaterialCatalogEntry, catalog_family_label
from app.simple_window import (
    CELL_ERROR_ROLE,
    LINK_GLOW_ROLE,
    PART_MATERIAL_COLUMN,
    PART_THICKNESS_COLUMN,
    STOCK_MATERIAL_COLUMN,
    STOCK_THICKNESS_COLUMN,
    SimpleCutWindow,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _stock_context(window: SimpleCutWindow) -> list[tuple[str, str]]:
    return [
        (
            window._stock_cell_text(row, STOCK_MATERIAL_COLUMN),
            window._stock_cell_text(row, STOCK_THICKNESS_COLUMN),
        )
        for row in range(window.stock_table.rowCount())
    ]


def test_first_part_replaces_default_stock_and_new_thickness_is_independent() -> None:
    _app()
    window = SimpleCutWindow()
    assert window.stock_table.rowCount() == 1

    window.add_part_row([18, 200, 300, 1, "POM-C naturalny"])
    assert _stock_context(window) == [("POM-C naturalny", "18")]

    window.add_part_row()
    assert window.parts.item(1, PART_MATERIAL_COLUMN).text() == "POM-C naturalny"
    assert window.parts.item(1, PART_THICKNESS_COLUMN).text() == "18"

    window.add_part_row([25, 200, 300, 1, "POM-C naturalny"])
    contexts = _stock_context(window)
    assert ("POM-C naturalny", "18") in contexts
    assert ("POM-C naturalny", "25") in contexts
    assert len(contexts) == 2

    window._add_blank_stock_row_and_focus()
    assert _stock_context(window)[-1] == ("POM-C naturalny", "25")
    assert window.stock_table.cellWidget(0, STOCK_MATERIAL_COLUMN).isEnabled() is False
    window.close()
    print("[OK] first default board is replaced and later board rows inherit only the latest part context")


def test_changing_only_part_thickness_updates_linked_board() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        window.parts.setRowCount(0)
        window.stock_table.setRowCount(0)
        window._add_stock_row({
            "material": "POM-C naturalny",
            "thickness": 18,
            "width": 1000,
            "height": 2000,
            "quantity": 1,
        })
        window.add_part_row([18, 200, 300, 1, "POM-C naturalny"])

        window.parts.item(0, PART_THICKNESS_COLUMN).setText("25")
        QApplication.processEvents()

        contexts = _stock_context(window)
        assert contexts == [("POM-C naturalny", "25")]
    finally:
        window.close()
    print("[OK] thickness edit follows the only linked part without adding a board")


def test_blank_material_is_inferred_and_draft_row_creates_no_stock_demand() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        window.parts.setRowCount(0)
        window.stock_table.setRowCount(0)
        default_thickness = window._last_thickness
        window._add_stock_row({
            "material": "standard",
            "thickness": default_thickness,
            "width": 1000,
            "height": 2000,
            "quantity": 1,
            "template": True,
        })
        window.add_part_row()
        assert len(_stock_context(window)) == 1

        window.add_part_row([default_thickness, 200, 300, 1, ""])
        inferred = window.parts.item(1, PART_MATERIAL_COLUMN).text().strip()
        assert inferred and inferred != "-"
        window._sync_stock_with_parts()
        assert any(material.casefold() == inferred.casefold() for material, _ in _stock_context(window))
    finally:
        window.close()
    print("[OK] populated rows get material context while empty drafts do not create boards")


def test_material_change_waits_for_valid_thickness_before_syncing_stock() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        old_entry = MaterialCatalogEntry(
            "POM C", 18, 100, 123, "POM C PŁYTA NATURALNA", width=1000, height=2000
        )
        new_entry = MaterialCatalogEntry(
            "PVC", 10, 100, 123, "PVC PŁYTA SZARA", width=1000, height=2000
        )
        old_material = catalog_family_label(old_entry)
        new_material = catalog_family_label(new_entry)
        window._material_catalog = [old_entry, new_entry]
        window.parts.setRowCount(0)
        window.stock_table.setRowCount(0)
        window._add_stock_row({
            "material": old_material,
            "thickness": 18,
            "width": 1000,
            "height": 2000,
            "quantity": 1,
        })
        window.add_part_row([18, 200, 300, 1, old_material])

        window._choose_part_thickness(0, new_material)
        QApplication.processEvents()
        assert window.parts.item(0, PART_THICKNESS_COLUMN).text() == ""
        assert _stock_context(window) == [(old_material, "18")]
        if hasattr(window, "_part_thickness_menu"):
            window._part_thickness_menu.close()

        window._set_part_material_thickness(0, new_material, 10)
        contexts = _stock_context(window)
        assert contexts == [(new_material, "10")]
        assert (new_material, "18") not in contexts
    finally:
        window.close()
    print("[OK] material switch cannot create a board with the previous thickness")


def test_integrated_pairs_share_a_subtle_stable_glow() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        window.parts.setRowCount(0)
        window.stock_table.setRowCount(0)
        for material, thickness in (("POM-C naturalny", 18), ("PP szary", 10)):
            window._add_stock_row({
                "material": material,
                "thickness": thickness,
                "width": 1000,
                "height": 2000,
                "quantity": 1,
            })
            window.add_part_row([thickness, 200, 300, 1, material])

        window.add_part_row()  # an empty draft must not look integrated
        window._refresh_linked_pair_glows()

        first_part_color = window.parts.item(0, PART_THICKNESS_COLUMN).data(LINK_GLOW_ROLE)
        first_stock_color = window.stock_table.item(0, STOCK_THICKNESS_COLUMN).data(LINK_GLOW_ROLE)
        second_color = window.parts.item(1, PART_THICKNESS_COLUMN).data(LINK_GLOW_ROLE)
        draft_color = window.parts.item(2, PART_THICKNESS_COLUMN).data(LINK_GLOW_ROLE)

        assert first_part_color
        assert first_part_color == first_stock_color
        assert second_color and second_color != first_part_color
        assert draft_color is None
        assert window.parts.item(0, PART_THICKNESS_COLUMN).background().color().alpha() <= 20
        assert "linked-pair" in window.parts.cellWidget(0, PART_MATERIAL_COLUMN).styleSheet()
        assert "linked-pair" in window.stock_table.cellWidget(0, STOCK_MATERIAL_COLUMN).styleSheet()
    finally:
        window.close()
    print("[OK] matching part/stock pairs share a delicate stable color and drafts stay neutral")


def test_validation_error_takes_priority_over_pair_glow() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        window.parts.setRowCount(0)
        window.stock_table.setRowCount(0)
        window._add_stock_row({
            "material": "POM-C naturalny",
            "thickness": 18,
            "width": 1000,
            "height": 2000,
            "quantity": 1,
        })
        window.add_part_row([18, 200, 300, 1, "POM-C naturalny"])
        window._refresh_linked_pair_glows()

        item = window.stock_table.item(0, STOCK_THICKNESS_COLUMN)
        linked_color = item.data(LINK_GLOW_ROLE)
        window._mark_stock_cell(0, STOCK_THICKNESS_COLUMN, True)
        assert item.data(CELL_ERROR_ROLE) is True
        assert item.background().color().alpha() == 96

        window._mark_stock_cell(0, STOCK_THICKNESS_COLUMN, False)
        assert item.data(CELL_ERROR_ROLE) is False
        assert item.data(LINK_GLOW_ROLE) == linked_color
        assert 0 < item.background().color().alpha() <= 20
    finally:
        window.close()
    print("[OK] red validation state wins and the pair glow returns after correction")


def test_repeated_material_edits_split_merge_and_undo() -> None:
    from core.models import Project
    from ui.grain_delegate import GRAIN_ROLE
    from app.simple_window import STOCK_QUANTITY_COLUMN
    _app()
    w = SimpleCutWindow()
    w._material_catalog = []
    w.parts.setRowCount(0)
    w.stock_table.setRowCount(0)
    w._add_stock_row({"material": "standard", "thickness": 5, "width": 1000,
                      "height": 2000, "quantity": 7, "template": True})
    w.add_part_row([5, 300, 200, 1000, "PE"])
    w.stock_table.item(0, STOCK_MATERIAL_COLUMN).setData(GRAIN_ROLE, "y")
    for material in ("PTFE", "PA6", "PE", "PTFE", "PE"):
        w._set_part_material_thickness(0, material, 5)
        assert _stock_context(w) == [(material, "5")]
        assert w._stock_cell_text(0, STOCK_QUANTITY_COLUMN) == "7"
        assert w.stock_table.item(0, STOCK_MATERIAL_COLUMN).data(GRAIN_ROLE) == "y"
    w.add_part_row([5, 100, 50, 1, "PE"])
    w._set_part_material_thickness(1, "PTFE", 5)
    assert _stock_context(w) == [("PE", "5"), ("PTFE", "5")]
    w._set_part_material_thickness(1, "PA6", 10)
    assert _stock_context(w) == [("PE", "5"), ("PA6", "10")]
    w._undo_parts()
    assert _stock_context(w) == [("PE", "5"), ("PTFE", "5")]
    w._redo_parts()
    assert _stock_context(w) == [("PE", "5"), ("PA6", "10")]
    saved = Project(sheet_stock=w._collect_stock(), sheet_parts=w._collect_parts())
    w._load_project_inputs(Project.from_dict(saved.to_dict()))
    w._set_part_material_thickness(1, "PE", 5)
    assert _stock_context(w) == [("PE", "5")]
    # Manual inventory unrelated to the edited part must survive.
    w._add_stock_row({"material": "ABS", "thickness": 3, "width": 900, "height": 900, "quantity": 2})
    w._set_part_material_thickness(0, "PTFE", 5)
    assert ("PE", "5") in _stock_context(w)  # still needed by row 2
    assert ("ABS", "3") in _stock_context(w)
    assert ("PTFE", "5") in _stock_context(w)
    w.close()
    print("[OK] repeated material changes, shared boards, splitting, merging, undo/redo and saved linkage")


def test_pending_thickness_keeps_link_and_shifted_controls_follow_row() -> None:
    from app.simple_window import STOCK_PRIORITY_COLUMN
    _app()
    w = SimpleCutWindow()
    w._material_catalog = []
    w.parts.setRowCount(0)
    w.stock_table.setRowCount(0)
    for material in ("PE", "PTFE", "PA6"):
        w.add_part_row([5, 100, 200, 1, material])
    # Simulate the real two-stage picker while another row is being edited.
    w._set_row_material(w.parts, 0, PART_MATERIAL_COLUMN, "ABS", sync_stock=False)
    w.parts.blockSignals(True)
    w.parts.item(0, PART_THICKNESS_COLUMN).setText("")
    w.parts.blockSignals(False)
    w._set_part_material_thickness(2, "PA6", 10)
    assert _stock_context(w) == [("PE", "5"), ("PTFE", "5"), ("PA6", "10")]
    w._set_part_material_thickness(0, "ABS", 3)
    assert _stock_context(w) == [("ABS", "3"), ("PTFE", "5"), ("PA6", "10")]
    w._set_part_material_thickness(1, "ABS", 3)
    assert _stock_context(w) == [("ABS", "3"), ("PA6", "10")]
    button = w.stock_table.cellWidget(1, STOCK_PRIORITY_COLUMN)
    button.click()
    assert w.stock_table.item(1, STOCK_PRIORITY_COLUMN).data(Qt.ItemDataRole.UserRole) == 1
    assert not w.stock_table.item(0, STOCK_PRIORITY_COLUMN).data(Qt.ItemDataRole.UserRole)
    w.close()
    print("[OK] pending material choice retains linkage and controls survive removal of a middle board")


if __name__ == "__main__":
    test_first_part_replaces_default_stock_and_new_thickness_is_independent()
    test_changing_only_part_thickness_updates_linked_board()
    test_blank_material_is_inferred_and_draft_row_creates_no_stock_demand()
    test_material_change_waits_for_valid_thickness_before_syncing_stock()
    test_integrated_pairs_share_a_subtle_stable_glow()
    test_validation_error_takes_priority_over_pair_glow()
    test_repeated_material_edits_split_merge_and_undo()
    test_pending_thickness_keeps_link_and_shifted_controls_follow_row()
    print("PART/STOCK LINKING TESTS OK")

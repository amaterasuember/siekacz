"""Regression coverage for first-pass custom thickness and PDF interaction."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from app.material_catalog import MaterialCatalogEntry
from app.simple_window import PART_THICKNESS_COLUMN, PannablePdfView, SimpleCutWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_custom_thickness_is_applied_on_the_first_choice() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        window._material_catalog = [
            MaterialCatalogEntry("PVC", 5.0, 10.0, 12.3, "PVC 5 mm"),
            MaterialCatalogEntry("PVC", 6.0, 11.0, 13.53, "PVC 6 mm"),
        ]
        window.parts.setRowCount(0)
        window.add_part_row([5, 100, 100, 1, "PVC"])
        index = window.parts.model().index(0, PART_THICKNESS_COLUMN)
        delegate = window.parts.itemDelegate()
        editor = delegate.createEditor(window.parts.viewport(), QStyleOptionViewItem(), index)
        custom_index = editor.findText("Własna...")
        assert custom_index >= 0
        with patch("app.simple_window.QInputDialog.getDouble", return_value=(7.5, True)):
            editor.setCurrentIndex(custom_index)
            QTest.qWait(10)
        assert window.parts.item(0, PART_THICKNESS_COLUMN).text() == "7.5"
    finally:
        window.close()
    print("[OK] first custom-thickness selection updates the row")


def test_catalog_material_thickness_editor_exposes_only_priced_values() -> None:
    _app()
    window = SimpleCutWindow()
    try:
        material = "POM C PŁYTA NATURALNA"
        window._material_catalog = [
            MaterialCatalogEntry("POM C", 1.0, 1.0, 1.0, material, width=1000, height=1000),
            MaterialCatalogEntry("POM C", 5.0, 7.0, 7.0, material, width=1000, height=1000),
        ]
        window.parts.setRowCount(0)
        # This represents a stale project row from before a new price list was
        # imported. It must not become an option in the supplier picker.
        window.add_part_row([18, 100, 100, 1, material])
        index = window.parts.model().index(0, PART_THICKNESS_COLUMN)
        editor = window.parts.itemDelegate().createEditor(window.parts.viewport(), QStyleOptionViewItem(), index)

        assert [editor.itemText(i) for i in range(editor.count())] == ["1", "5"]
        assert editor.findText("18") == -1
        assert editor.findText("Własna...") == -1
    finally:
        window.close()
    print("[OK] catalog thickness picker contains only priced variants")


def test_pdf_view_zoom_is_cursor_anchored_and_hand_ready() -> None:
    _app()
    view = PannablePdfView()
    try:
        view.setZoomMode(view.ZoomMode.Custom)
        view.setZoomFactor(1.0)
        wheel = QWheelEvent(
            QPointF(80, 60), QPointF(80, 60), QPoint(), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        view.wheelEvent(wheel)
        QTest.qWait(10)
        assert view.zoomMode() == view.ZoomMode.Custom
        assert abs(view.zoomFactor() - 1.18) < 0.001
        assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    finally:
        view.deleteLater()
    print("[OK] PDF view supports cursor zoom and hand panning")


if __name__ == "__main__":
    test_custom_thickness_is_applied_on_the_first_choice()
    test_catalog_material_thickness_editor_exposes_only_priced_values()
    test_pdf_view_zoom_is_cursor_anchored_and_hand_ready()
    print("PREVIEW/CUSTOM THICKNESS TESTS OK")

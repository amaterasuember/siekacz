from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QGraphicsRectItem

from app.technical_editor import TechnicalEditorDialog


def test_editor_uses_complete_drawing_bounds() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    first = QGraphicsRectItem(10, 20, 100, 50)
    second = QGraphicsRectItem(160, 80, 40, 30)
    for item in (first, second):
        dialog.canvas._prepare_item(item)
        dialog.canvas.scene().addItem(item)
    bounds = dialog.canvas.drawing_bounds()
    assert round(bounds.width()) == 190
    assert round(bounds.height()) == 90
    dialog.close()
    app.processEvents()
    print("[OK] technical editor returns the complete drawing envelope")


def test_editor_fillet_keeps_a_selectable_drawing_item() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = TechnicalEditorDialog()
    item = QGraphicsRectItem(0, 0, 100, 50)
    dialog.canvas._prepare_item(item)
    dialog.canvas.scene().addItem(item)
    item.setSelected(True)
    dialog.canvas.convert_selected_rectangles(8, fillet=True)
    assert len(dialog.canvas.drawing_items()) == 1
    assert dialog.canvas.scene().selectedItems()
    dialog.close()
    app.processEvents()
    print("[OK] fillet operation keeps editable geometry")


if __name__ == "__main__":
    test_editor_uses_complete_drawing_bounds()
    test_editor_fillet_keeps_a_selectable_drawing_item()
    print("TECHNICAL EDITOR TESTS OK")

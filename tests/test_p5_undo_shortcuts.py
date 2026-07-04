"""P5: tests for parts table undo/redo and keyboard shortcuts."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QTableWidget, QTableWidgetItem

from app.simple_window import SimpleCutWindow


def _app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _snapshot(window: SimpleCutWindow) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for r in range(window.parts.rowCount()):
        cells: list[str] = []
        for c in (2, 3, 4):
            item = window.parts.item(r, c)
            cells.append(item.text() if item else "")
        rows.append(tuple(cells))  # type: ignore[arg-type]
    return rows


def test_undo_after_cell_edit() -> None:
    _app()
    window = SimpleCutWindow()
    # _load_example_rows leaves a single empty row [("", "", "1")].
    baseline = _snapshot(window)
    assert baseline == [("", "", "1")]

    item = window.parts.item(0, 2)
    assert item is not None
    item.setText("500")  # fires itemChanged → records snapshot
    after_edit = _snapshot(window)
    assert after_edit == [("500", "", "1")]
    assert len(window._parts_undo_stack) == 1

    window._undo_parts()
    assert _snapshot(window) == baseline
    assert len(window._parts_redo_stack) == 1
    assert len(window._parts_undo_stack) == 0

    window._redo_parts()
    assert _snapshot(window) == after_edit
    assert len(window._parts_redo_stack) == 0
    print("[OK] undo/redo after cell edit")


def test_undo_after_add_and_remove_row() -> None:
    _app()
    window = SimpleCutWindow()
    baseline = _snapshot(window)

    window.add_part_row(["100", "200", 3])
    after_add = _snapshot(window)
    assert after_add == [("", "", "1"), ("100", "200", "3")]

    window._undo_parts()
    assert _snapshot(window) == baseline

    window._redo_parts()
    assert _snapshot(window) == after_add

    # Select second row and remove it.
    window.parts.selectRow(1)
    window.remove_selected_rows()
    after_remove = _snapshot(window)
    assert after_remove == baseline

    window._undo_parts()
    assert _snapshot(window) == after_add
    print("[OK] undo/redo with add and remove")


def test_undo_stack_capped_at_50() -> None:
    _app()
    window = SimpleCutWindow()
    for i in range(80):
        window.add_part_row([str(i), str(i), 1])
    assert len(window._parts_undo_stack) == 50, f"stack size {len(window._parts_undo_stack)} != 50"
    print("[OK] undo stack capped at 50")


def test_load_project_resets_undo() -> None:
    """Loading a project must wipe undo history so user cannot revert into stale state."""
    _app()
    window = SimpleCutWindow()
    window.add_part_row(["1", "1", 1])
    window.add_part_row(["2", "2", 1])
    assert len(window._parts_undo_stack) >= 2

    from core.models import OptimizationSettings, Project, SheetPart, SheetStock

    project = Project()
    project.sheet_stock = [SheetStock("standard", 1, 2000, 1000, 1)]
    project.sheet_parts = [SheetPart("A", 300, 400, 2, "standard", 1)]
    project.settings = OptimizationSettings(job_type="sheet", algorithm="Vertical Segmented Guillotine", kerf=5.0)
    window._load_project_inputs(project)

    assert len(window._parts_undo_stack) == 0, "undo stack not cleared after project load"
    assert len(window._parts_redo_stack) == 0
    print("[OK] _load_project_inputs resets undo history")


def test_new_cut_resets_undo() -> None:
    _app()
    window = SimpleCutWindow()
    window.add_part_row(["1", "1", 1])
    assert window._parts_undo_stack

    window.new_cut()
    assert window._parts_undo_stack == []
    assert window._parts_redo_stack == []
    print("[OK] new_cut resets undo history")


def test_shortcuts_registered() -> None:
    """Verify expected shortcuts are wired on toolbar actions."""
    _app()
    window = SimpleCutWindow()

    def has_shortcut(action, expected: str) -> bool:
        expected_seq = QKeySequence(expected)
        return any(s.matches(expected_seq) == QKeySequence.SequenceMatch.ExactMatch for s in action.shortcuts())

    assert has_shortcut(window.calc_action, "Ctrl+Return"), "Ctrl+Return missing on calc_action"
    assert has_shortcut(window.calc_action, "F5"), "F5 missing on calc_action"
    assert has_shortcut(window.save_project_action, "Ctrl+S"), "Ctrl+S missing on save_project_action"
    assert has_shortcut(window.png_action, "Ctrl+Shift+E"), "Ctrl+Shift+E missing on png_action"
    assert has_shortcut(window.new_cut_action, "Ctrl+N"), "Ctrl+N missing on new_cut_action"
    print("[OK] toolbar shortcuts registered (Ctrl+Return, F5, Ctrl+S, Ctrl+Shift+E, Ctrl+N)")


def test_remove_with_empty_selection_no_crash() -> None:
    _app()
    window = SimpleCutWindow()
    window.parts.clearSelection()
    window.remove_selected_rows()  # must not crash, must not push undo entry
    assert _snapshot(window) == [("", "", "1")]
    print("[OK] remove_selected_rows on empty selection (no-op)")


def test_parts_table_selects_cells_not_hover_like_rows() -> None:
    _app()
    window = SimpleCutWindow()
    assert (
        window.parts.selectionBehavior() == QTableWidget.SelectionBehavior.SelectItems
    ), "parts table should select individual cells while entering dimensions"
    print("[OK] parts table selects cells")


def test_parts_tab_reaches_quantity_before_next_row() -> None:
    app = _app()
    window = SimpleCutWindow()
    delegate = window.parts_delegate
    navigation: list[tuple[int, int, bool]] = []
    delegate.cell_navigation_requested.connect(lambda row, col, backwards: navigation.append((row, col, backwards)))

    length_editor = QLineEdit()
    length_editor.setProperty("tableRow", 0)
    length_editor.setProperty("tableColumn", 3)
    tab_on_length = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Tab,
        Qt.KeyboardModifier.NoModifier,
    )
    assert delegate.eventFilter(length_editor, tab_on_length)
    app.processEvents()
    assert navigation == [(0, 3, False)]
    before_rows = window.parts.rowCount()
    window._handle_part_nav_key(0, 3, False)
    assert window.parts.currentColumn() == 4, "Tab on Dł. (mm) must focus Ilość"
    assert window.parts.rowCount() == before_rows, "Tab on Dł. (mm) must not create a row"

    quantity_editor = QLineEdit()
    quantity_editor.setProperty("tableRow", 0)
    quantity_editor.setProperty("tableColumn", 4)
    tab_on_quantity = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Tab,
        Qt.KeyboardModifier.NoModifier,
    )
    assert delegate.eventFilter(quantity_editor, tab_on_quantity)
    app.processEvents()
    assert navigation[-1] == (0, 4, False)
    window._handle_part_nav_key(0, 4, False)
    assert window.parts.rowCount() == before_rows + 1, "Tab on Ilość should create/focus the next part row"
    assert window.parts.currentColumn() == 2, "new part row should start from Szer. (mm)"
    print("[OK] parts Tab navigation reaches quantity before advancing")


def test_stock_table_uses_visible_cell_navigation() -> None:
    _app()
    window = SimpleCutWindow()
    assert (
        window.stock_table.selectionBehavior() == QTableWidget.SelectionBehavior.SelectItems
    ), "stock table should select individual cells, not hover-like rows"
    assert window._editable_stock_columns() == [1, 2, 3]

    window._add_blank_stock_row_and_focus()
    assert window.stock_table.currentColumn() == 1, "new stock row should start from visible Szer. (mm)"

    assert window.stock_table.cellWidget(window.stock_table.currentRow(), 1) is None
    window._handle_stock_nav_key(window.stock_table.currentRow(), 1, False)
    assert window.stock_table.currentColumn() == 2
    print("[OK] stock table uses visible cell navigation")


def test_stock_table_has_no_permanent_hover_editors() -> None:
    app = _app()
    window = SimpleCutWindow()
    window.show()
    app.processEvents()

    for column in (1, 2, 3):
        assert window.stock_table.cellWidget(0, column) is None
        item = window.stock_table.item(0, column)
        assert item is not None
        assert item.flags() & Qt.ItemFlag.ItemIsEditable

    before = window.stock_table.currentIndex()
    app.processEvents()
    assert window.stock_table.currentIndex() == before
    window.close()
    print("[OK] stock table has no permanent hover editors")


def test_stock_table_hover_does_not_start_editing() -> None:
    app = _app()
    window = SimpleCutWindow()
    window.show()
    app.processEvents()

    table = window.stock_table
    table.clearSelection()
    table.setCurrentCell(-1, -1)
    app.processEvents()

    index = table.model().index(0, 1)
    point = table.visualRect(index).center()
    QTest.mouseMove(table.viewport(), point)
    app.processEvents()
    app.processEvents()

    assert table.cellWidget(0, 1) is None
    assert table.currentRow() == -1 or table.currentColumn() != 1
    assert not table.selectedIndexes()
    window.close()
    print("[OK] stock table hover does not start editing")


if __name__ == "__main__":
    test_undo_after_cell_edit()
    test_undo_after_add_and_remove_row()
    test_undo_stack_capped_at_50()
    test_load_project_resets_undo()
    test_new_cut_resets_undo()
    test_shortcuts_registered()
    test_remove_with_empty_selection_no_crash()
    test_parts_table_selects_cells_not_hover_like_rows()
    test_parts_tab_reaches_quantity_before_next_row()
    test_stock_table_uses_visible_cell_navigation()
    test_stock_table_has_no_permanent_hover_editors()
    test_stock_table_hover_does_not_start_editing()
    print("\ntest_p5_undo_shortcuts: OK")

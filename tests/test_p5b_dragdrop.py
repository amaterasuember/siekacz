"""P5b: drag-drop CSV/XLSX onto the parts table."""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from app.simple_window import PartsTableWidget, SimpleCutWindow, parse_parts_file


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _snapshot(window: SimpleCutWindow) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for r in range(window.parts.rowCount()):
        cells: list[str] = []
        for c in (2, 3, 4):
            item = window.parts.item(r, c)
            cells.append(item.text() if item else "")
        rows.append(tuple(cells))  # type: ignore[arg-type]
    return rows


# ---------------------------------------------------------------------------
# Parser tests (pure, no Qt drag-drop required)
# ---------------------------------------------------------------------------

def test_parse_csv_polish_headers() -> None:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Szerokość", "Długość", "Ilość"])
        writer.writerow([300, 500, 2])
        writer.writerow([200, 150, 4])
        path = Path(f.name)
    try:
        parsed = parse_parts_file(path)
        assert parsed == [(300.0, 500.0, 2), (200.0, 150.0, 4)], parsed
    finally:
        path.unlink(missing_ok=True)
    print("[OK] CSV with Polish headers")


def test_parse_csv_english_headers() -> None:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["width", "length", "qty"])
        writer.writerow([800, 600, 1])
        path = Path(f.name)
    try:
        parsed = parse_parts_file(path)
        assert parsed == [(800.0, 600.0, 1)], parsed
    finally:
        path.unlink(missing_ok=True)
    print("[OK] CSV with English headers")


def test_parse_csv_comma_decimal() -> None:
    """Polish locale uses comma as decimal separator."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Szer.", "Dł.", "Szt."])
        writer.writerow(["300,5", "500,75", "3"])
        path = Path(f.name)
    try:
        parsed = parse_parts_file(path)
        assert parsed == [(300.5, 500.75, 3)], parsed
    finally:
        path.unlink(missing_ok=True)
    print("[OK] CSV with comma decimal")


def test_parse_xlsx_polish_headers() -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Szerokość", "Wysokość", "Ilość"])
    ws.append([400, 250, 6])
    ws.append([100, 100, 12])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = Path(f.name)
    try:
        wb.save(path)
        parsed = parse_parts_file(path)
        assert parsed == [(400.0, 250.0, 6), (100.0, 100.0, 12)], parsed
    finally:
        path.unlink(missing_ok=True)
    print("[OK] XLSX with Polish headers")


def test_parse_invalid_values_raises() -> None:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Szerokość", "Długość", "Ilość"])
        writer.writerow([300, -50, 2])
        path = Path(f.name)
    try:
        try:
            parse_parts_file(path)
        except ValueError as exc:
            assert "Wiersz" in str(exc)
            print("[OK] negative values raise ValueError")
            return
        raise AssertionError("Expected ValueError for negative values")
    finally:
        path.unlink(missing_ok=True)


def test_parse_unsupported_extension() -> None:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        path = Path(f.name)
    try:
        try:
            parse_parts_file(path)
        except ValueError as exc:
            assert "format" in str(exc).lower()
            print("[OK] unsupported extension raises ValueError")
            return
        raise AssertionError("Expected ValueError for .txt")
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Window integration: dropped-file signal flow + undo coalescing
# ---------------------------------------------------------------------------

def test_drop_appends_rows_and_single_undo() -> None:
    """Dropping a CSV adds all rows and creates exactly one undo step."""
    _app()
    window = SimpleCutWindow()
    baseline = _snapshot(window)
    initial_undo_depth = len(window._parts_undo_stack)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Szerokość", "Długość", "Ilość"])
        writer.writerow([300, 500, 2])
        writer.writerow([200, 150, 4])
        writer.writerow([100, 100, 1])
        path = Path(f.name)
    try:
        # Simulate the drop by directly invoking the slot (drag-drop QEvent plumbing
        # in offscreen mode is brittle; the signal contract is what matters).
        window._on_parts_files_dropped([path])

        rows = _snapshot(window)
        assert len(rows) == len(baseline) + 3, f"expected 3 added rows, got {rows}"
        assert rows[-3:] == [
            ("300.0", "500.0", "2"),
            ("200.0", "150.0", "4"),
            ("100.0", "100.0", "1"),
        ], rows[-3:]
        assert len(window._parts_undo_stack) == initial_undo_depth + 1, \
            f"expected ONE undo entry for drop, got {len(window._parts_undo_stack) - initial_undo_depth}"

        # Undo the drop in one step.
        window._undo_parts()
        assert _snapshot(window) == baseline
    finally:
        path.unlink(missing_ok=True)
    print("[OK] drop adds rows + coalesces into single undo entry")


def test_drop_invalid_file_does_not_modify_table() -> None:
    """If the file is malformed, the table must be unchanged."""
    _app()
    window = SimpleCutWindow()
    baseline = _snapshot(window)
    initial_undo_depth = len(window._parts_undo_stack)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Szerokość", "Długość", "Ilość"])
        writer.writerow([300, 0, 2])  # zero height — invalid
        path = Path(f.name)
    try:
        # The handler shows a QMessageBox; in offscreen mode it returns immediately
        # without blocking. Monkey-patch QMessageBox.warning to avoid spawning UI.
        from PySide6.QtWidgets import QMessageBox
        original = QMessageBox.warning
        captured: list[str] = []
        QMessageBox.warning = staticmethod(lambda *args, **kwargs: captured.append(args[2] if len(args) > 2 else ""))
        try:
            window._on_parts_files_dropped([path])
        finally:
            QMessageBox.warning = original

        assert _snapshot(window) == baseline, "Table changed on invalid drop"
        assert len(window._parts_undo_stack) == initial_undo_depth, "Undo stack changed on invalid drop"
        assert captured and "Wiersz" in captured[0], f"Expected error dialog with row info, got: {captured}"
    finally:
        path.unlink(missing_ok=True)
    print("[OK] invalid drop preserves table state")


def test_drop_event_accepts_csv_url() -> None:
    """The widget accepts CSV/XLSX URLs in its drag handlers."""
    _app()
    table = PartsTableWidget(0, 4)

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("test.csv")])

    # _extract_local_paths is the gating helper used by all three event hooks.
    class _FakeEvent:
        def __init__(self, m: QMimeData) -> None:
            self._mime = m

        def mimeData(self) -> QMimeData:
            return self._mime

    paths = table._extract_local_paths(_FakeEvent(mime))
    assert len(paths) == 1 and paths[0].name == "test.csv"

    mime2 = QMimeData()
    mime2.setUrls([QUrl.fromLocalFile("test.png")])
    paths2 = table._extract_local_paths(_FakeEvent(mime2))
    assert paths2 == [], f"expected PNG rejected, got {paths2}"
    print("[OK] drag-drop filter accepts CSV/XLSX only")


if __name__ == "__main__":
    test_parse_csv_polish_headers()
    test_parse_csv_english_headers()
    test_parse_csv_comma_decimal()
    test_parse_xlsx_polish_headers()
    test_parse_invalid_values_raises()
    test_parse_unsupported_extension()
    test_drop_appends_rows_and_single_undo()
    test_drop_invalid_file_does_not_modify_table()
    test_drop_event_accepts_csv_url()
    print("\ntest_p5b_dragdrop: OK")

from __future__ import annotations

"""Regression checks for the 15,000-piece safety limit and session PIN."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QInputDialog

from app.simple_window import MAX_TOTAL_PARTS, SimpleCutWindow
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from core.validation import getValidationErrors, sanitizeProjectState


def run() -> None:
    QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    window.parts.setRowCount(0)
    window.add_part_row(["50", "50", MAX_TOTAL_PARTS + 1])

    original_get_text = QInputDialog.getText
    try:
        QInputDialog.getText = staticmethod(lambda *_args, **_kwargs: ("wrong", True))
        try:
            window._collect_parts()
        except ValueError as exc:
            assert "15 000" in str(exc)
        else:
            raise AssertionError("wrong PIN must not bypass the safety limit")

        QInputDialog.getText = staticmethod(lambda *_args, **_kwargs: ("1984", True))
        parts = window._collect_parts()
        assert parts[0].quantity == MAX_TOTAL_PARTS + 1
        assert window._part_limit_override_authorized
    finally:
        QInputDialog.getText = original_get_text
        window.deleteLater()

    project = Project(
        sheet_stock=[SheetStock("standard", 1, 1000, 2000, 1)],
        sheet_parts=[SheetPart("A", 50, 50, MAX_TOTAL_PARTS + 1, "standard", 1)],
        settings=OptimizationSettings(job_type="sheet"),
    )
    assert getValidationErrors(project, max_total_parts=MAX_TOTAL_PARTS)
    assert not getValidationErrors(project, max_total_parts=None)
    restored = sanitizeProjectState(project.to_dict(), max_total_parts=None)
    assert restored.sheet_parts[0].quantity == MAX_TOTAL_PARTS + 1
    print("part-limit authorization: OK")


if __name__ == "__main__":
    run()

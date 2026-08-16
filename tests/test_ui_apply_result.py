"""Regression tests for the UI result-application path.

Background: a calculation that produces *missing sheets* used to crash in the
GUI even though the optimizer itself finished in ~3s.  The crash chain was:

    _calculation_worker_finished -> _apply_result -> _rebuild_sheet_nav

`_rebuild_sheet_nav` wiped the previous chip widgets with::

    item = layout.takeAt(0)
    if item.widget() is not None:
        item.widget().setParent(None)
        item.widget().deleteLater()   # <- crashed

After ``setParent(None)`` PySide6 drops the internal widget reference held by
the ``QLayoutItem``, so the second ``item.widget()`` call returned ``None`` and
``.deleteLater()`` raised ``AttributeError: 'NoneType' object has no attribute
'deleteLater'``.

Because that exception happened *after* the optimizer finished but *before*
``optimization_progress_overlay.finish()`` was called, the progress overlay
never stopped — its elapsed-time counter kept growing and looked like a
"239 second calculation".

These tests pin down both halves of the fix:
  1. ``_rebuild_sheet_nav`` survives repeated rebuilds (the takeAt/deleteLater
     loop), including the missing-sheet case.
  2. The full ``_apply_result`` path does not raise for a project that yields
     missing sheets, and the overlay is always stopped by
     ``_calculation_worker_finished`` even when applying the result.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QGraphicsTextItem

from app.simple_window import PdfPreviewDialog, SimpleCutWindow, _polish_sheet_count
from core.models import (
    OptimizationSettings,
    OptimizationResult,
    PlacedSheetPart,
    Project,
    SheetLayout,
    SheetPart,
    SheetStock,
)
from ui.layout_view import LayoutView
from workers.optimizer_worker import optimize_sheet_project


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _missing_sheet_project() -> Project:
    """The exact scenario from the bug report: more parts than one sheet holds.

    40x900 x55 + 80x190 x18 on a single 2000x1000 sheet cannot all fit, so the
    optimizer emits ``missing_sheet_layouts`` — the path that crashed the UI.
    """
    project = Project()
    project.sheet_stock = [
        SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True,
                   min_offcut_width=80, min_offcut_height=80)
    ]
    project.sheet_parts = [
        SheetPart("40 x 900", 40, 900, 55, "standard", 1, allow_rotation=True),
        SheetPart("80 x 190", 80, 190, 18, "standard", 1, allow_rotation=True),
    ]
    project.settings = OptimizationSettings(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        kerf=5,
        cutting_mode="hybrid",
        optimization_mode="comfort",
        display_orientation="horizontal",
        min_reusable_offcut_size=80,
    )
    return project


def test_material_badge_precedes_and_centers_with_sheet_title() -> None:
    _app()
    view = LayoutView()
    stock = SheetStock("POM C PŁYTA NATURALNA", 10, 2000, 1000, 1)
    part = SheetPart("A", 100, 100, 1, stock.material, 10)
    layout = SheetLayout(stock=stock, sheet_index=1)
    layout.parts.append(PlacedSheetPart(part=part, x=0, y=0, width=100, height=100))
    result = OptimizationResult(
        job_type="sheet",
        algorithm="test",
        sheet_layouts=[layout],
    )
    try:
        view.show_result(result)
        text_items = [
            item for item in view.scene.items()
            if isinstance(item, QGraphicsTextItem)
        ]
        badge = next(item for item in text_items if item.toPlainText() == "POM-C")
        title = next(
            item for item in text_items
            if item.toPlainText().startswith("POM C PŁYTA NATURALNA")
        )
        badge_rect = badge.sceneBoundingRect()
        title_rect = title.sceneBoundingRect()
        assert badge_rect.right() < title_rect.left()
        assert abs(badge_rect.center().y() - title_rect.center().y()) <= 1.5
        print("[OK] material badge is before and vertically centred with sheet title")
    finally:
        view.deleteLater()


def test_rebuild_sheet_nav_survives_repeated_rebuilds() -> None:
    """Rebuilding the chip bar must wipe old chips without crashing.

    The second rebuild exercises the takeAt/setParent/deleteLater teardown loop
    that previously dereferenced a None widget.
    """
    _app()
    window = SimpleCutWindow()
    # First build: 2 real sheets + 1 missing -> chips created.
    window._rebuild_sheet_nav(2, 1)
    assert window._sheet_nav_layout.count() > 0
    # Rebuild several times with different counts; each call tears down the
    # previous chips first.  This must not raise.
    window._rebuild_sheet_nav(3, 0)
    window._rebuild_sheet_nav(1, 2)
    window._rebuild_sheet_nav(0, 0)  # nothing to navigate -> nav hidden
    print("[OK] _rebuild_sheet_nav survives repeated rebuilds")


def test_apply_result_with_missing_sheets_does_not_crash() -> None:
    """The full UI apply path must handle a result that contains missing sheets."""
    _app()
    window = SimpleCutWindow()
    result = optimize_sheet_project(_missing_sheet_project())
    # Sanity: this scenario really does produce missing sheets (otherwise the
    # regression would silently stop covering the bug).
    assert getattr(result, "missing_sheet_layouts", []), \
        "expected missing sheets for this scenario; test no longer covers the bug"
    # The crash happened here, inside _apply_result -> _rebuild_sheet_nav.
    window._apply_result(result)
    print("[OK] _apply_result with missing sheets does not crash")


def test_missing_sheets_follow_the_matching_material_and_thickness_group() -> None:
    """A missing board belongs below its matching material/thickness group."""
    _app()
    pom_stock = SheetStock("POM C", 20, 2000, 1000, 1, allow_rotation=True)
    pe_stock = SheetStock("PE1000", 25, 2000, 1000, 1, allow_rotation=True)
    pom_part = SheetPart("POM part", 200, 200, 1, "POM C", 20, allow_rotation=True)
    pe_part = SheetPart("PE part", 200, 200, 1, "PE1000", 25, allow_rotation=True)

    pom_layout = SheetLayout(stock=pom_stock, sheet_index=1)
    pom_layout.parts.append(PlacedSheetPart(pom_part, 0, 0, 200, 200, False))
    pe_layout = SheetLayout(stock=pe_stock, sheet_index=2)
    pe_layout.parts.append(PlacedSheetPart(pe_part, 0, 0, 200, 200, False))
    missing_layout = SheetLayout(stock=pom_stock, sheet_index=3)
    missing_layout.parts.append(PlacedSheetPart(pom_part, 0, 0, 200, 200, False))

    result = OptimizationResult(
        job_type="sheet",
        algorithm="test",
        sheet_layouts=[pom_layout, pe_layout],
        missing_sheet_layouts=[missing_layout],
    )
    view = LayoutView()
    try:
        view.show_result(result)
        labels = [label.casefold() for label in view.nav_chip_labels()]
        assert "pom c" in labels[0], labels
        assert labels[1].startswith("brakuj"), labels
        assert "pom c" in labels[1], labels
        assert "pe1000" in labels[2], labels
        print("[OK] missing boards follow their material/thickness group")
    finally:
        view.deleteLater()


def test_worker_finished_always_stops_overlay() -> None:
    """Even when applying the result, the progress overlay must end.

    Guards the 'overlay stuck visible' symptom: the overlay's elapsed-time
    timer drives the on-screen counter; if it keeps running the user sees an
    ever-growing "239 second" calculation.  After _calculation_worker_finished
    handles a result, that timer must be stopped and the calculating flag
    cleared.  (finish() defers the actual hide() by a few hundred ms, so we
    assert on the frozen timer, not on visibility.)
    """
    _app()
    window = SimpleCutWindow()
    overlay = window.optimization_progress_overlay
    result = optimize_sheet_project(_missing_sheet_project())
    # Simulate the overlay being active during a calculation.
    window._is_calculating = True
    overlay.start(_missing_sheet_project())
    assert overlay._timer.isActive(), "overlay timer should run while calculating"
    window._calculation_worker_finished(result, None)
    # The elapsed-time timer must be frozen and the calculating flag cleared.
    assert not overlay._timer.isActive(), "overlay timer still running after finish"
    assert window._is_calculating is False, "calculating flag not cleared"
    print("[OK] _calculation_worker_finished always stops the overlay")


def test_pdf_preview_exposes_later_report_pages_and_zoom() -> None:
    """The in-app preview must expose cutting pages after the title page."""
    from reportlab.pdfgen.canvas import Canvas
    from PySide6.QtPdfWidgets import QPdfView

    _app()
    path = Path(tempfile.gettempdir()) / "siekacz_pdf_preview_regression.pdf"
    canvas = Canvas(str(path))
    canvas.drawString(72, 720, "Strona tytulowa")
    canvas.showPage()
    canvas.drawString(72, 720, "Rozkroj plyty")
    canvas.save()

    dialog = PdfPreviewDialog(path)
    try:
        assert dialog._document.pageCount() == 2
        assert dialog._view.pageMode() == QPdfView.PageMode.MultiPage
        dialog._change_zoom(1.2)
        dialog._change_page(1)
        assert dialog._view.pageNavigator().currentPage() == 1
        print("[OK] PDF preview exposes later pages and supports zoom")
    finally:
        dialog.close()


def test_polish_sheet_count_uses_correct_plural_forms() -> None:
    assert _polish_sheet_count(1) == "1 płyta"
    assert _polish_sheet_count(2) == "2 płyty"
    assert _polish_sheet_count(9) == "9 płyt"
    assert _polish_sheet_count(12) == "12 płyt"
    assert _polish_sheet_count(24) == "24 płyty"
    assert _polish_sheet_count(1, missing=True) == "1 brakująca płyta"
    assert _polish_sheet_count(3, missing=True) == "3 brakujące płyty"
    assert _polish_sheet_count(15, missing=True) == "15 brakujących płyt"
    print("[OK] Polish board-count notifications use correct forms")


def _columns_layout(count: int, part_w: float, part_h: float, kerf: float = 5.0) -> SheetLayout:
    """A layout of *count* identical full-height columns separated by kerf."""
    stock = SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True,
                       min_offcut_width=80, min_offcut_height=80)
    layout = SheetLayout(stock=stock, sheet_index=1)
    part = SheetPart("col", part_w, part_h, count, "standard", 1, allow_rotation=True)
    x = 0.0
    for _ in range(count):
        layout.parts.append(PlacedSheetPart(part, x, 0.0, part_w, part_h, False))
        x += part_w + kerf
    return layout


def test_vertical_bands_group_identical_columns() -> None:
    """Identical adjacent strips must collapse into one 'N × W' group."""
    _app()
    view = LayoutView()
    try:
        layout = _columns_layout(7, 40, 900)
        groups = view._compute_vertical_bands(layout)
        assert len(groups) == 1, f"expected one merged group, got {len(groups)}"
        assert groups[0]["n"] == 7, groups
        assert abs(groups[0]["w"] - 40) < 0.6, groups
        print("[OK] vertical bands group 7 identical columns into 7 × 40")
    finally:
        view.deleteLater()


def test_multiple_bands_report_one_total_cut_span() -> None:
    """Three band labels collapse into one final cut-width summary."""
    _app()
    view = LayoutView()
    try:
        groups = [
            {"start": 0.0, "end": 112.0},
            {"start": 112.0, "end": 274.0},
            {"start": 274.0, "end": 396.0},
        ]
        assert view._shows_total_cut_span(groups)
        assert view._total_cut_span_label(groups) == "396 mm"
        print("[OK] multiple bands report one total occupied cut span")
    finally:
        view.deleteLater()


def test_vertical_bands_separate_distinct_widths() -> None:
    """Two blocks of different strip widths must form two groups."""
    _app()
    view = LayoutView()
    try:
        stock = SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True,
                           min_offcut_width=80, min_offcut_height=80)
        layout = SheetLayout(stock=stock, sheet_index=1)
        col = SheetPart("col", 40, 900, 3, "standard", 1, allow_rotation=True)
        wide = SheetPart("wide", 100, 900, 2, "standard", 1, allow_rotation=True)
        x = 0.0
        for _ in range(3):  # three 40-wide columns
            layout.parts.append(PlacedSheetPart(col, x, 0.0, 40, 900, False))
            x += 45.0
        x += 20.0  # clear gap between the two blocks
        for _ in range(2):  # two 100-wide columns
            layout.parts.append(PlacedSheetPart(wide, x, 0.0, 100, 900, False))
            x += 105.0
        groups = view._compute_vertical_bands(layout)
        assert len(groups) == 2, f"expected two groups, got {groups}"
        assert groups[0]["n"] == 3 and abs(groups[0]["w"] - 40) < 0.6, groups
        assert groups[1]["n"] == 2 and abs(groups[1]["w"] - 100) < 0.6, groups
        print("[OK] vertical bands separate 3×40 and 2×100 into distinct groups")
    finally:
        view.deleteLater()


def test_horizontal_bands_group_clean_rows() -> None:
    """Short-side dimensioning must detect uniform stacked rows (N × H)."""
    _app()
    view = LayoutView()
    try:
        stock = SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True,
                           min_offcut_width=80, min_offcut_height=80)
        layout = SheetLayout(stock=stock, sheet_index=1)
        part = SheetPart("P", 300, 200, 9, "standard", 1, allow_rotation=True)
        for row in range(3):          # 3 rows of height 200
            for col in range(3):      # 3 columns of width 300
                layout.parts.append(
                    PlacedSheetPart(part, col * 305.0, row * 205.0, 300, 200, False)
                )
        hbands = view._compute_horizontal_bands(layout)
        vbands = view._compute_vertical_bands(layout)
        assert len(hbands) == 1 and hbands[0]["n"] == 3 and abs(hbands[0]["w"] - 200) < 0.6, hbands
        assert len(vbands) == 1 and vbands[0]["n"] == 3 and abs(vbands[0]["w"] - 300) < 0.6, vbands
        print("[OK] horizontal bands detect 3 × 200 rows; vertical 3 × 300 columns")
    finally:
        view.deleteLater()


def test_vertical_bands_same_formatka_is_one_block() -> None:
    """A contiguous region of a single formatka must be ONE band, not split.

    Regression for the "205 + 1021" complaint: bands are grouped by formatka
    type, so adjacent strips of the same part (even with differing widths or a
    rotated row) collapse into a single dimension instead of being chopped up.
    """
    _app()
    view = LayoutView()
    try:
        project = Project()
        project.sheet_stock = [
            SheetStock("standard", 1, 2000, 1000, 1, allow_rotation=True,
                       min_offcut_width=80, min_offcut_height=80)
        ]
        project.sheet_parts = [
            SheetPart("C", 100, 200, 40, "standard", 1, allow_rotation=True),
        ]
        project.settings = OptimizationSettings(
            job_type="sheet", algorithm="Vertical Segmented Guillotine", kerf=5,
            cutting_mode="hybrid", optimization_mode="comfort",
            display_orientation="horizontal", min_reusable_offcut_size=80,
        )
        result = optimize_sheet_project(project)
        layout = (result.sheet_layouts + result.missing_sheet_layouts)[0]
        groups = view._compute_vertical_bands(layout)
        assert len(groups) == 1, f"single formatka must be one block, got {groups}"
        span = groups[0]["end"] - groups[0]["start"]
        assert abs(span - layout.used_width) <= 6, (span, layout.used_width)
        print("[OK] single formatka region collapses into one band")
    finally:
        view.deleteLater()


if __name__ == "__main__":
    test_material_badge_precedes_and_centers_with_sheet_title()
    test_rebuild_sheet_nav_survives_repeated_rebuilds()
    test_apply_result_with_missing_sheets_does_not_crash()
    test_missing_sheets_follow_the_matching_material_and_thickness_group()
    test_worker_finished_always_stops_overlay()
    test_pdf_preview_exposes_later_report_pages_and_zoom()
    test_polish_sheet_count_uses_correct_plural_forms()
    test_vertical_bands_group_identical_columns()
    test_multiple_bands_report_one_total_cut_span()
    test_vertical_bands_separate_distinct_widths()
    test_horizontal_bands_group_clean_rows()
    test_vertical_bands_same_formatka_is_one_block()
    print("\ntest_ui_apply_result: OK")

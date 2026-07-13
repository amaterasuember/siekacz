from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Table

from algorithms.layout_grouping import group_identical_layouts
from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetPart, SheetStock
from import_export.pdf_report import SheetFlowable, _append_drawings, _cut_metrics_section, _styles


class _LabelCanvas:
    """Tiny recording canvas for checking label geometry without a PDF file."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def setFillColor(self, *args) -> None:
        self.calls.append(("fill", args))

    def setFont(self, *args) -> None:
        self.calls.append(("font", args))

    def drawRightString(self, *args) -> None:
        self.calls.append(("right", args))

    def drawCentredString(self, *args) -> None:
        self.calls.append(("center", args))

    def saveState(self) -> None:
        self.calls.append(("save", ()))

    def restoreState(self) -> None:
        self.calls.append(("restore", ()))

    def translate(self, *args) -> None:
        self.calls.append(("translate", args))

    def rotate(self, *args) -> None:
        self.calls.append(("rotate", args))


def _result() -> OptimizationResult:
    stock = SheetStock("PA6", 18, 2000, 1000, 2)
    part = SheetPart("C", 54, 35, 1, "PA6", 18)
    placements = [PlacedSheetPart(part, 0, 0, 54, 35)]
    layouts = [
        SheetLayout(stock, 1, parts=list(placements), cut_count=2),
        SheetLayout(stock, 2, parts=list(placements), cut_count=2),
    ]
    return OptimizationResult("sheet", "test", sheet_layouts=layouts)


def test_group_caption_contains_plate_thickness_and_explicit_numbers() -> None:
    result = _result()
    story: list[object] = []
    symbols = {(51.0, 50.0): "A", (154.0, 120.0): "B", (54.0, 35.0): "C"}
    _append_drawings(
        story,
        group_identical_layouts(result.sheet_layouts),
        _styles(),
        False,
        170 * mm,
        110 * mm,
        symbol_map=symbols,
    )
    drawing = next(item for item in story if isinstance(item, SheetFlowable))
    assert "PA6 · gr. 18 mm" in drawing.caption
    assert "nr 1 i 2" in drawing.caption
    assert "×2" not in drawing.caption

    legends = [item.text for item in story if isinstance(item, Paragraph) and "Legenda formatek" in item.text]
    assert len(legends) == 1
    assert ">C<" in legends[0]
    assert ">A<" not in legends[0] and ">B<" not in legends[0]
    legend_index = next(
        index
        for index, item in enumerate(story)
        if isinstance(item, Paragraph) and "Legenda formatek" in item.text
    )
    time_table_index = next(index for index, item in enumerate(story) if isinstance(item, Table))
    assert legend_index < time_table_index


def test_pdf_cut_metrics_has_no_duplicate_dimension_or_rotation_columns() -> None:
    story: list[object] = []
    _cut_metrics_section(story, _result(), _styles())
    table = next(item for item in story if isinstance(item, Table))
    header = [str(value) for value in table._cellvalues[0]]
    assert "Wymiar [mm]" not in header
    assert "Obrót" not in header
    assert header[:4] == ["Format", "Szt.", "Cięć", "mb piły"]


def test_small_pdf_part_keeps_dimension_and_drops_colliding_symbol() -> None:
    stock = SheetStock("PA6", 18, 100, 100, 1)
    part = SheetPart("A", 40, 41, 1, "PA6", 18)
    placement = PlacedSheetPart(part, 0, 0, 40, 41)
    flowable = SheetFlowable(
        SheetLayout(stock, 1, parts=[placement]),
        1,
        100,
        100,
        "test",
        symbol_map={(41.0, 40.0): "A"},
    )
    canvas = _LabelCanvas()
    flowable._label_part(canvas, placement, 0, 0, 8, 8.2, None)
    assert any(call == "right" and args[-1] == "40x41" for call, args in canvas.calls)
    assert not any(call == "center" for call, _args in canvas.calls)


def test_portrait_pdf_symbol_follows_the_long_edge() -> None:
    stock = SheetStock("PA6", 18, 500, 500, 1)
    part = SheetPart("A", 40, 140, 1, "PA6", 18)
    placement = PlacedSheetPart(part, 0, 0, 40, 140)
    flowable = SheetFlowable(
        SheetLayout(stock, 1, parts=[placement]),
        1,
        100,
        100,
        "test",
        symbol_map={(140.0, 40.0): "A"},
    )
    canvas = _LabelCanvas()
    flowable._label_part(canvas, placement, 0, 0, 100, 220, None)
    assert any(call == "rotate" and args == (90,) for call, args in canvas.calls)
    assert any(call == "center" and args[-1] == "A" for call, args in canvas.calls)


if __name__ == "__main__":
    test_group_caption_contains_plate_thickness_and_explicit_numbers()
    test_pdf_cut_metrics_has_no_duplicate_dimension_or_rotation_columns()
    test_small_pdf_part_keeps_dimension_and_drops_colliding_symbol()
    test_portrait_pdf_symbol_follows_the_long_edge()
    print("test_pdf_report_layout: OK")

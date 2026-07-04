from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.layout_scoring import score_result
from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetPart, SheetStock


EPS = 0.001


def _largest_free_area(layout: SheetLayout) -> float:
    return max((width * height for _, _, width, height in layout.offcuts), default=0.0)


def _placements_by_name(layout: SheetLayout, name: str) -> list[PlacedSheetPart]:
    return [placement for placement in layout.parts if placement.part.name == name]


def _assert_bounds(layout: SheetLayout) -> None:
    for part in layout.parts:
        assert part.x >= -EPS
        assert part.y >= -EPS
        assert part.x + part.width <= layout.stock.width + EPS
        assert part.y + part.height <= layout.stock.height + EPS


def _assert_kerf_spacing(layout: SheetLayout, kerf: float) -> None:
    for index, a in enumerate(layout.parts):
        for b in layout.parts[index + 1 :]:
            x_overlap = a.x < b.x + b.width - EPS and b.x < a.x + a.width - EPS
            y_overlap = a.y < b.y + b.height - EPS and b.y < a.y + a.height - EPS
            assert not (x_overlap and y_overlap), f"{a.part.name} overlaps {b.part.name}"
            if y_overlap:
                gap = max(b.x - (a.x + a.width), a.x - (b.x + b.width))
                assert gap + EPS >= kerf, f"x kerf gap too small: {gap}"
            if x_overlap:
                gap = max(b.y - (a.y + a.height), a.y - (b.y + b.height))
                assert gap + EPS >= kerf, f"y kerf gap too small: {gap}"


def _assert_rotation_flags(layout: SheetLayout) -> None:
    for placement in layout.parts:
        original = (
            abs(placement.width - placement.part.width) < EPS
            and abs(placement.height - placement.part.height) < EPS
        )
        rotated = (
            abs(placement.width - placement.part.height) < EPS
            and abs(placement.height - placement.part.width) < EPS
            and abs(placement.part.width - placement.part.height) > EPS
        )
        assert original or rotated
        assert placement.rotated is rotated


def _assert_no_segment_crossings(layout: SheetLayout) -> None:
    boundaries = sorted({segment["x"] for segment in layout.vertical_segments} | {segment["right"] for segment in layout.vertical_segments})
    for placement in layout.parts:
        left = placement.x
        right = placement.x + placement.width
        for boundary in boundaries:
            assert not (left + EPS < boundary < right - EPS), f"{placement.part.name} crosses segment boundary {boundary}"


def test_remnant_scoring_prefers_consolidated_reusable_second_sheet() -> None:
    stock = [
        SheetStock(
            material="standard",
            thickness=1,
            width=2050,
            height=3050,
            quantity=2,
            allow_rotation=True,
            min_offcut_width=200,
            min_offcut_height=200,
        )
    ]
    parts = [
        SheetPart("1000 x 1280", 1000, 1280, 3, "standard", 1, allow_rotation=True),
        SheetPart("1000 x 1450", 1000, 1450, 1, "standard", 1, allow_rotation=True),
        SheetPart("1450 x 1150", 1450, 1150, 1, "standard", 1, allow_rotation=True),
    ]

    result = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, min_reusable_size=200)

    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 2

    consolidated = next(
        layout
        for layout in result.sheet_layouts
        if len(_placements_by_name(layout, "1000 x 1280")) == 3
        and len(_placements_by_name(layout, "1000 x 1450")) == 1
    )
    remnant_sheet = next(layout for layout in result.sheet_layouts if layout is not consolidated)

    assert len(remnant_sheet.parts) == 1
    assert remnant_sheet.parts[0].part.name == "1450 x 1150"
    assert _largest_free_area(remnant_sheet) > 3_500_000

    for layout in result.sheet_layouts:
        _assert_bounds(layout)
        _assert_kerf_spacing(layout, 5)
        _assert_rotation_flags(layout)
        _assert_no_segment_crossings(layout)


def _bad_horizontal_row_result() -> OptimizationResult:
    stock = SheetStock("standard", 1, 3050, 2050, 1, allow_rotation=True, min_offcut_width=200, min_offcut_height=200)
    p1280 = SheetPart("1000 x 1280", 1000, 1280, 1, "standard", 1, allow_rotation=True)
    p1450 = SheetPart("1000 x 1450", 1000, 1450, 1, "standard", 1, allow_rotation=True)
    pbig = SheetPart("1450 x 1150", 1450, 1150, 1, "standard", 1, allow_rotation=True)

    sheet1 = SheetLayout(stock=stock, sheet_index=1)
    for index, x in enumerate((0, 1005, 2010), start=1):
        sheet1.parts.append(PlacedSheetPart(p1280, x, 0, 1000, 1280, False))
        sheet1.vertical_segments.append({"index": index, "x": x, "width": 1000, "right": x + 1000})

    sheet2 = SheetLayout(stock=stock, sheet_index=2)
    sheet2.parts = [
        PlacedSheetPart(pbig, 0, 0, 1150, 1450, True),
        PlacedSheetPart(p1450, 1155, 0, 1000, 1450, False),
    ]
    sheet2.vertical_segments = [
        {"index": 1, "x": 0, "width": 1150, "right": 1150},
        {"index": 2, "x": 1155, "width": 1000, "right": 2155},
    ]

    return OptimizationResult(
        job_type="sheet",
        algorithm="bad horizontal row fixture",
        sheet_layouts=[sheet1, sheet2],
    )


def _mirrored_strip_result() -> OptimizationResult:
    stock = SheetStock("standard", 1, 2050, 3050, 1, allow_rotation=True, min_offcut_width=200, min_offcut_height=200)
    p1280 = SheetPart("1000 x 1280", 1000, 1280, 1, "standard", 1, allow_rotation=True)
    p1450 = SheetPart("1000 x 1450", 1000, 1450, 1, "standard", 1, allow_rotation=True)
    pbig = SheetPart("1450 x 1150", 1450, 1150, 1, "standard", 1, allow_rotation=True)

    sheet1 = SheetLayout(stock=stock, sheet_index=1)
    sheet1.parts = [
        PlacedSheetPart(p1450, 0, 0, 1000, 1450, False),
        PlacedSheetPart(p1280, 0, 1455, 1000, 1280, False),
        PlacedSheetPart(p1280, 1005, 0, 1000, 1280, False),
        PlacedSheetPart(p1280, 1005, 1285, 1000, 1280, False),
    ]
    sheet1.vertical_segments = [
        {"index": 1, "x": 0, "width": 1000, "right": 1000},
        {"index": 2, "x": 1005, "width": 1000, "right": 2005},
    ]

    sheet2 = SheetLayout(stock=stock, sheet_index=2)
    sheet2.parts = [PlacedSheetPart(pbig, 0, 0, 1450, 1150, False)]
    sheet2.vertical_segments = [{"index": 1, "x": 0, "width": 1450, "right": 1450}]

    return OptimizationResult(
        job_type="sheet",
        algorithm="mirrored strip fixture",
        sheet_layouts=[sheet1, sheet2],
    )


def test_packs_1000mm_width_panels_into_two_vertical_strips_before_using_second_sheet() -> None:
    os.environ["SIEKACZ_DEBUG_CANDIDATES"] = "1"
    stock = [
        SheetStock(
            material="standard",
            thickness=1,
            width=2050,
            height=3050,
            quantity=2,
            allow_rotation=True,
            min_offcut_width=200,
            min_offcut_height=200,
        )
    ]
    parts = [
        SheetPart("1000 x 1280", 1000, 1280, 3, "standard", 1, allow_rotation=True),
        SheetPart("1000 x 1450", 1000, 1450, 1, "standard", 1, allow_rotation=True),
        SheetPart("1450 x 1150", 1450, 1150, 1, "standard", 1, allow_rotation=True),
    ]

    try:
        result = optimize_2d_vertical_segmented(stock, parts, kerf=5, margin=0, min_reusable_size=200)
    finally:
        os.environ.pop("SIEKACZ_DEBUG_CANDIDATES", None)
    debug = "\n".join(result.messages)

    assert "Vertical strip candidate debug" in debug
    assert "strip width 1000" in debug
    assert "Candidate debug:" in debug
    assert not result.unplaced_sheet_parts
    assert len(result.sheet_layouts) == 2

    assert not any(
        len(layout.parts) == 3
        and len(_placements_by_name(layout, "1000 x 1280")) == 3
        and not _placements_by_name(layout, "1000 x 1450")
        for layout in result.sheet_layouts
    )

    strip_sheet = next(
        layout
        for layout in result.sheet_layouts
        if len(_placements_by_name(layout, "1000 x 1280")) == 3
        and len(_placements_by_name(layout, "1000 x 1450")) == 1
    )
    special_panel = _placements_by_name(strip_sheet, "1000 x 1450")[0]
    assert special_panel.x > 1000, "1000 x 1450 should be in the right strip"
    assert special_panel.y < 1, "1000 x 1450 should start the right strip for the preferred workshop sequence"
    assert len([placement for placement in _placements_by_name(strip_sheet, "1000 x 1280") if placement.x < 1000]) == 2
    remnant_sheet = next(layout for layout in result.sheet_layouts if layout is not strip_sheet)
    assert len(remnant_sheet.parts) == 1
    assert remnant_sheet.parts[0].part.name == "1450 x 1150"
    assert remnant_sheet.largest_reusable_offcut_area == max(layout.largest_reusable_offcut_area for layout in result.sheet_layouts)
    assert remnant_sheet.largest_reusable_offcut_area > 3_500_000

    expected_score = score_result(result, kerf=5, min_reusable_size=200).value
    bad_score = score_result(_bad_horizontal_row_result(), kerf=5, min_reusable_size=200).value
    mirrored_score = score_result(_mirrored_strip_result(), kerf=5, min_reusable_size=200).value
    assert expected_score < bad_score
    assert expected_score < mirrored_score

    for layout in result.sheet_layouts:
        _assert_bounds(layout)
        _assert_kerf_spacing(layout, 5)
        _assert_rotation_flags(layout)
        _assert_no_segment_crossings(layout)


def test_layout_view_horizontal_orientation_keeps_height_axis_as_workflow_direction() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from ui.layout_view import LayoutView

    app = QApplication.instance() or QApplication([])
    _ = app
    view = LayoutView()
    layout = SheetLayout(stock=SheetStock("standard", 1, 2050, 3050, 1), sheet_index=1)

    display_w, display_h, rotated = view._display_sheet_geometry(layout)
    assert view.display_orientation == "horizontal"
    assert rotated
    assert round(display_w) == 3050
    assert round(display_h) == 2050
    assert display_w > display_h
    mapped_regular = view._map_rect_to_display(layout, (1005, 0, 1000, 1280))
    mapped_special = view._map_rect_to_display(layout, (1005, 1285, 1000, 1450))
    assert mapped_special[0] > mapped_regular[0], "1000 x 1450 should render at the end of the height-axis strip"

    view.set_display_orientation("vertical")
    display_w, display_h, rotated = view._display_sheet_geometry(layout)
    assert not rotated
    assert round(display_w) == 2050
    assert round(display_h) == 3050

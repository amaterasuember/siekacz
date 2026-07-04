from __future__ import annotations

import math
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from app import project_history
from app.simple_window import SimpleCutWindow
from core.models import OptimizationResult, OptimizationSettings, Project, SheetLayout, SheetPart, SheetStock
from workers.optimizer_worker import KERF_TOLERANCE_MM, optimize_sheet_project


EPS = 0.001


def make_project(
    stock_width: float,
    stock_height: float,
    quantity: int,
    kerf: float,
    parts: list[tuple[float, float, int]],
    mode: str = "comfort",
) -> Project:
    project = Project()
    project.sheet_stock = [
        SheetStock("standard", 1, stock_width, stock_height, quantity, allow_rotation=True, min_offcut_width=80, min_offcut_height=80)
    ]
    project.sheet_parts = [SheetPart(f"{width:g} x {height:g}", width, height, qty, "standard", 1, allow_rotation=True) for width, height, qty in parts]
    project.settings = OptimizationSettings(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        kerf=kerf,
        cutting_mode="hybrid",
        optimization_mode=mode,
        display_orientation="horizontal",
        min_reusable_offcut_size=80,
    )
    return project


def all_layouts(result: OptimizationResult) -> list[SheetLayout]:
    return list(result.sheet_layouts) + list(result.missing_sheet_layouts)


def placed_count(result: OptimizationResult) -> int:
    return sum(len(layout.parts) for layout in result.sheet_layouts)


def placed_counts(result: OptimizationResult) -> Counter[str]:
    counts: Counter[str] = Counter()
    for layout in all_layouts(result):
        for placement in layout.parts:
            counts[placement.part.name] += 1
    return counts


def requested_counts(project: Project) -> Counter[str]:
    return Counter({part.name: part.quantity for part in project.sheet_parts})


def assert_layout_valid(project: Project, result: OptimizationResult) -> None:
    assert math.isfinite(result.utilization)
    assert result.utilization <= 100 + EPS
    assert result.waste >= -EPS

    requested = requested_counts(project)
    for name, count in placed_counts(result).items():
        assert count <= requested[name], f"{name} duplicated: {count} > {requested[name]}"

    effective_kerf = project.settings.kerf + KERF_TOLERANCE_MM
    for layout in all_layouts(result):
        assert layout.sheet_utilization <= 100 + EPS
        assert layout.utilization <= 100 + EPS
        if layout.parts:
            assert layout.is_guillotine_feasible
            assert layout.cut_tree
        for placement in layout.parts:
            values = (placement.x, placement.y, placement.width, placement.height)
            assert all(math.isfinite(value) for value in values)
            assert placement.x >= -EPS
            assert placement.y >= -EPS
            assert placement.width > 0
            assert placement.height > 0
            assert placement.x + placement.width <= layout.stock.width + EPS
            assert placement.y + placement.height <= layout.stock.height + EPS

        for index, first in enumerate(layout.parts):
            for second in layout.parts[index + 1 :]:
                x_overlap = first.x < second.x + second.width - EPS and second.x < first.x + first.width - EPS
                y_overlap = first.y < second.y + second.height - EPS and second.y < first.y + first.height - EPS
                assert not (x_overlap and y_overlap), f"{first.part.name} overlaps {second.part.name}"
                if y_overlap:
                    gap = max(second.x - (first.x + first.width), first.x - (second.x + second.width))
                    assert gap + EPS >= effective_kerf, f"x kerf gap too small: {gap}"
                if x_overlap:
                    gap = max(second.y - (first.y + first.height), first.y - (second.y + second.height))
                    assert gap + EPS >= effective_kerf, f"y kerf gap too small: {gap}"


def layout_signature(result: OptimizationResult) -> tuple[object, ...]:
    layouts: list[tuple[object, ...]] = []
    for layout in all_layouts(result):
        placements = tuple(
            sorted(
                (
                    placement.part.name,
                    round(placement.x, 3),
                    round(placement.y, 3),
                    round(placement.width, 3),
                    round(placement.height, 3),
                    bool(placement.rotated),
                )
                for placement in layout.parts
            )
        )
        layouts.append(
            (
                layout.stock.source,
                round(layout.stock.width, 3),
                round(layout.stock.height, 3),
                round(layout.used_width, 3),
                round(layout.used_height, 3),
                len(layout.parts),
                placements,
            )
        )
    return (len(result.sheet_layouts), len(result.missing_sheet_layouts), len(result.unplaced_sheet_parts), tuple(layouts))


def test_stock_orientation_and_missing_sheets_are_repeatable() -> None:
    normal_project = make_project(2000, 1000, 1, 5, [(40, 900, 55)])
    swapped_project = make_project(1000, 2000, 1, 5, [(40, 900, 55)])

    normal = optimize_sheet_project(normal_project)
    swapped = optimize_sheet_project(swapped_project)

    assert_layout_valid(normal_project, normal)
    assert_layout_valid(swapped_project, swapped)
    assert placed_count(normal) == placed_count(swapped) == 48
    # After the optimizer's missing-sheet pass, parts that fit on virtual
    # missing sheets are no longer counted as `unplaced_sheet_parts` — only
    # parts that truly cannot be placed (too large for any available format).
    # The 7 leftover 40×900 parts here all fit on a second virtual sheet,
    # so `unplaced_sheet_parts` is empty and the count moves into
    # `missing_sheet_layouts[*].parts`.
    assert len(normal.unplaced_sheet_parts) == len(swapped.unplaced_sheet_parts) == 0
    assert len(normal.missing_sheet_layouts) == len(swapped.missing_sheet_layouts) == 1
    assert normal.missing_sheet_layouts[0].stock.source == "missing"
    assert swapped.missing_sheet_layouts[0].stock.source == "missing"
    assert len(normal.missing_sheet_layouts[0].parts) == len(swapped.missing_sheet_layouts[0].parts) == 7
    assert layout_signature(normal) == layout_signature(swapped)


def test_optimizer_is_deterministic_for_repeated_runs() -> None:
    project = make_project(2000, 1000, 1, 5, [(40, 900, 55)])
    signatures = []
    for _ in range(10):
        result = optimize_sheet_project(project)
        assert_layout_valid(project, result)
        signatures.append(layout_signature(result))
    assert len(set(signatures)) == 1


def test_worker_rejects_invalid_numeric_inputs() -> None:
    invalid_kerf = make_project(1000, 1000, 1, -1, [(100, 100, 1)])
    try:
        optimize_sheet_project(invalid_kerf)
    except ValueError as exc:
        assert "kerf" in str(exc).lower() and "ujemny" in str(exc).lower()
    else:
        raise AssertionError("negative kerf should be rejected")

    invalid_stock = make_project(float("nan"), 1000, 1, 5, [(100, 100, 1)])
    try:
        optimize_sheet_project(invalid_stock)
    except ValueError as exc:
        assert "skończoną" in str(exc).lower()
    else:
        raise AssertionError("NaN stock width should be rejected")

    invalid_quantity = make_project(1000, 1000, 1, 5, [(100, 100, 1)])
    invalid_quantity.sheet_parts[0].quantity = 1.5  # type: ignore[assignment]
    try:
        optimize_sheet_project(invalid_quantity)
    except ValueError as exc:
        assert "całkowitą" in str(exc).lower()
    else:
        raise AssertionError("decimal quantity should be rejected")

    empty = Project(settings=OptimizationSettings(job_type="sheet"))
    try:
        optimize_sheet_project(empty)
    except ValueError as exc:
        assert "płyty" in str(exc).lower()
    else:
        raise AssertionError("empty stock should be rejected")


def test_mixed_orientation_compaction_regression() -> None:
    project = make_project(2000, 1000, 1, 5, [(100, 600, 10), (100, 100, 10)], mode="sport")
    result = optimize_sheet_project(project)

    assert_layout_valid(project, result)
    layout = result.sheet_layouts[0]
    a_parts = [placement for placement in layout.parts if placement.part.name == "100 x 600"]
    orientations = {(round(placement.width), round(placement.height)) for placement in a_parts}
    assert len(orientations) > 1, orientations
    assert layout.used_width <= 900, layout.used_width


def test_project_history_preserves_multiple_stock_formats_and_notes() -> None:
    project = Project()
    project.meta.client_name = "Klient QA"
    project.meta.notes = "Pilna notatka do zamówienia"
    project.sheet_stock = [
        SheetStock("standard", 1, 1000, 2000, 1, allow_rotation=True),
        SheetStock("standard", 1, 2050, 3050, 1, allow_rotation=True),
        SheetStock("standard", 1, 1500, 3000, 1, allow_rotation=True),
    ]
    project.sheet_parts = [SheetPart("A", 500, 500, 2, "standard", 1, allow_rotation=True)]
    project.settings = OptimizationSettings(kerf=5, optimization_mode="comfort")
    result = optimize_sheet_project(project)
    assert_layout_valid(project, result)

    summary = project_history.record_summary(project, result)
    assert len(summary["sheet_formats"]) == 3
    assert "1000 x 2000" in summary["sheet_format"]
    assert "2050 x 3050" in summary["sheet_format"]
    assert "1500 x 3000" in summary["sheet_format"]

    restored = project_history.result_from_dict(project_history.result_to_dict(result))
    assert restored is not None
    assert layout_signature(restored) == layout_signature(result)


def test_png_header_has_no_company_field() -> None:
    app = QApplication.instance() or QApplication([])
    window = SimpleCutWindow()
    try:
        header = window._png_header_text("ALUMEN")
        assert "Klient: ALUMEN" in header
        assert "Firma:" not in header
    finally:
        window.deleteLater()


def main() -> None:
    tests = [
        test_stock_orientation_and_missing_sheets_are_repeatable,
        test_optimizer_is_deterministic_for_repeated_runs,
        test_worker_rejects_invalid_numeric_inputs,
        test_mixed_orientation_compaction_regression,
        test_project_history_preserves_multiple_stock_formats_and_notes,
        test_png_header_has_no_company_field,
    ]
    for test in tests:
        test()
    print("FINAL QA GUARDRAILS: OK")


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from app.simple_window import _format_table_number
from core.models import OptimizationResult, OptimizationSettings, PlacedSheetPart, Project, SheetLayout, SheetPart, SheetStock
from workers.optimizer_worker import _attach_missing_sheet_layouts, optimize_sheet_order, optimize_sheet_project


def test_mismatched_thickness_is_rejected_before_optimization() -> None:
    project = Project(
        sheet_stock=[SheetStock("POM H PŁYTA CZARNA", 20, 2000, 1000, 1)],
        sheet_parts=[SheetPart("A", 34, 34, 1, "POM H PŁYTA CZARNA", 18)],
        settings=OptimizationSettings(algorithm="vertical_segmented", multi_core=False),
    )

    try:
        optimize_sheet_project(project)
    except ValueError as exc:
        message = str(exc)
        assert "18" in message and "zgodnej p" in message
    else:
        raise AssertionError("Formatka 18 mm nie moze zostac policzona z plyty 20 mm.")


def test_vertical_optimizer_never_places_a_mismatched_part_directly() -> None:
    stock = [SheetStock("POM H PŁYTA CZARNA", 20, 2000, 1000, 1)]
    parts = [SheetPart("A", 34, 34, 1, "POM H PŁYTA CZARNA", 18)]
    result = optimize_2d_vertical_segmented(stock, parts)

    assert not result.sheet_layouts
    assert result.unplaced_sheet_parts


def test_matching_board_specification_survives_every_layout() -> None:
    project = Project(
        sheet_stock=[SheetStock("POM H PŁYTA CZARNA", 18, 2000, 1000, 1)],
        sheet_parts=[SheetPart("A", 34, 34, 20, "POM H PŁYTA CZARNA", 18)],
        settings=OptimizationSettings(algorithm="vertical_segmented", multi_core=False),
    )
    result = optimize_sheet_project(project)

    assert result.sheet_layouts
    for layout in result.sheet_layouts + result.missing_sheet_layouts:
        assert all(abs(placement.part.thickness - layout.stock.thickness) < 0.001 for placement in layout.parts)
        assert all(placement.part.material == layout.stock.material for placement in layout.parts)


def test_generic_sheet_can_be_used_for_named_part_at_the_same_thickness() -> None:
    project = Project(
        sheet_stock=[SheetStock("standard", 18, 2000, 1000, 1)],
        sheet_parts=[SheetPart("A", 34, 34, 20, "PA6 NATURALNA", 18)],
        settings=OptimizationSettings(algorithm="vertical_segmented", multi_core=False),
    )
    result = optimize_sheet_project(project)

    assert result.sheet_layouts
    assert not result.unplaced_sheet_parts
    assert all(
        abs(placement.part.thickness - layout.stock.thickness) < 0.001
        for layout in result.sheet_layouts
        for placement in layout.parts
    )


def test_material_thickness_groups_restart_the_display_sheet_number() -> None:
    projects = []
    for thickness in (18, 20):
        projects.append(Project(
            sheet_stock=[SheetStock("POM C CZARNA", thickness, 1000, 2000, 1)],
            sheet_parts=[SheetPart("A", 100, 100, 1, "POM C CZARNA", thickness)],
            settings=OptimizationSettings(algorithm="vertical_segmented", multi_core=False),
        ))
    result = optimize_sheet_order(projects)
    assert [layout.sheet_index for layout in result.sheet_layouts] == [1, 2]
    assert [getattr(layout, "display_sheet_index") for layout in result.sheet_layouts] == [1, 1]


def test_stack_size_cannot_exceed_the_available_board_quantity() -> None:
    project = Project(
        sheet_stock=[SheetStock("standard", 18, 2000, 1000, 2, stack_size=3)],
        sheet_parts=[SheetPart("A", 100, 100, 1, "standard", 18)],
        settings=OptimizationSettings(algorithm="vertical_segmented", multi_core=False),
    )
    try:
        optimize_sheet_project(project)
    except ValueError as exc:
        assert "sztapel" in str(exc).lower()
    else:
        raise AssertionError("Sztapel większy od ilości płyt musi zablokować obliczenia.")


def test_whole_millimetre_values_have_no_float_suffix_in_tables() -> None:
    assert _format_table_number(20.0) == "20"
    assert _format_table_number("18.0") == "18"
    assert _format_table_number(2050.0) == "2050"
    assert _format_table_number(2.5) == "2.5"


def test_missing_boards_repeat_the_proven_real_board_pattern() -> None:
    """Full virtual boards retain the mixed layout chosen for the real board."""
    stock = SheetStock("POM C CZARNA", 20, 2000, 1000, 1, allow_rotation=True)
    part = SheetPart("B", 122, 434, 1, "POM C CZARNA", 20, allow_rotation=True)
    real_layout = SheetLayout(stock=stock, sheet_index=1)
    real_layout.parts = [
        PlacedSheetPart(part, col * 127.2, row * 439.2, 122, 434, False)
        for row in range(2)
        for col in range(15)
    ]
    result = OptimizationResult(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        sheet_layouts=[real_layout],
        unplaced_sheet_parts=[replace(part, quantity=1) for _ in range(30)],
    )

    result = _attach_missing_sheet_layouts(
        result,
        "Vertical Segmented Guillotine",
        [stock],
        kerf=5.2,
        margin=0,
        mode="minimize_waste",
        min_reusable_size=80,
        cutting_mode="hybrid",
        optimization_mode="comfort",
        execution_mode="sequential",
    )

    assert len(result.missing_sheet_layouts) == 1
    repeated = result.missing_sheet_layouts[0]
    assert repeated.stock.source == "missing"
    assert [(placed.width, placed.height, placed.rotated) for placed in repeated.parts] == [
        (placed.width, placed.height, placed.rotated) for placed in real_layout.parts
    ]


if __name__ == "__main__":
    test_mismatched_thickness_is_rejected_before_optimization()
    test_vertical_optimizer_never_places_a_mismatched_part_directly()
    test_matching_board_specification_survives_every_layout()
    test_generic_sheet_can_be_used_for_named_part_at_the_same_thickness()
    test_material_thickness_groups_restart_the_display_sheet_number()
    test_stack_size_cannot_exceed_the_available_board_quantity()
    test_whole_millimetre_values_have_no_float_suffix_in_tables()
    test_missing_boards_repeat_the_proven_real_board_pattern()
    print("test_sheet_specification_safety: OK")

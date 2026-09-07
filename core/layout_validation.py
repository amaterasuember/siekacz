"""Geometry and order checks shared by the solver and its output boundary."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import fields

from core.grain import part_orientations
from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetPart, materials_are_compatible

EPS = 0.001


def layout_geometry_errors(layout: SheetLayout, kerf: float, margin: float = 0.0) -> list[str]:
    errors: list[str] = []
    board = layout.stock
    values = (board.width, board.height, kerf, margin)
    if not all(math.isfinite(v) for v in values) or min(board.width, board.height) <= 0 or min(kerf, margin) < 0:
        return ["Nieprawidłowe wymiary płyty, margines lub rzaz."]
    for p in layout.parts:
        if not all(math.isfinite(v) for v in (p.x, p.y, p.width, p.height)) or min(p.width, p.height) <= 0:
            errors.append(f"{p.part.name}: nieprawidłowa geometria formatki")
            continue
        if p.x < margin - EPS or p.y < margin - EPS:
            errors.append(f"{p.part.name}: pozycja poza płytą / marginesem")
        if p.x + p.width > board.width - margin + EPS or p.y + p.height > board.height - margin + EPS:
            errors.append(f"{p.part.name}: wystaje poza płytę / margines")
    if errors:
        return errors

    # Sweep along the axis with the wider extent. Distant columns/rows cannot
    # collide; avoid comparing every pair on dense multi-thousand-part grids.
    swap = board.height > board.width
    rectangles = sorted(
        ((p.y, p.x, p.height, p.width, i, p) if swap else (p.x, p.y, p.width, p.height, i, p))
        for i, p in enumerate(layout.parts)
    )
    active: list[tuple[float, float, float, float, int, PlacedSheetPart]] = []
    for rect in rectangles:
        x, y, w, h, _, p = rect
        active = [r for r in active if r[0] + r[2] + kerf > x + EPS]
        for ax, ay, aw, ah, _, other in active:
            x_overlap = x < ax + aw - EPS
            y_overlap = y < ay + ah - EPS and ay < y + h - EPS
            if x_overlap and y_overlap:
                errors.append(f"{p.part.name} nachodzi na {other.part.name}")
            elif y_overlap and x - (ax + aw) < kerf - EPS:
                errors.append(f"Za mały rzaz: {p.part.name} / {other.part.name}")
            elif x_overlap and max(y - (ay + ah), ay - (y + h)) < kerf - EPS:
                errors.append(f"Za mały rzaz: {p.part.name} / {other.part.name}")
        active.append(rect)
    return errors


def _part_key(part: SheetPart) -> tuple[object, ...]:
    # Empty user labels are expanded to per-instance labels by the packers.
    return tuple(getattr(part, f.name) for f in fields(SheetPart) if f.name not in {"quantity", "is_waste_fill", "label"})


def assert_sheet_result(result: OptimizationResult, requested: list[SheetPart], kerf: float, margin: float = 0.0) -> None:
    """Reject corrupt output before it can be displayed, exported or saved.

    Unplaced parts are individual instances. Optional waste-fill copies remain
    explicit extras and must never hide missing required order quantities.
    """
    expected: Counter[tuple[object, ...]] = Counter()
    actual: Counter[tuple[object, ...]] = Counter()
    for part in requested:
        expected[_part_key(part)] += part.quantity
    errors: list[str] = []
    for layout in [*result.sheet_layouts, *result.missing_sheet_layouts]:
        errors.extend(layout_geometry_errors(layout, kerf, margin))
        for p in layout.parts:
            part = p.part
            if not part.is_waste_fill:
                actual[_part_key(part)] += 1
            width, height = (part.height, part.width) if p.rotated else (part.width, part.height)
            if abs(p.width - width) > EPS or abs(p.height - height) > EPS:
                errors.append(f"{part.name}: zmienione wymiary formatki")
            if not any(rotated == p.rotated for _, _, rotated in part_orientations(part, layout.stock)):
                errors.append(f"{part.name}: niedozwolony obrót")
            if not materials_are_compatible(layout.stock.material, part.material) or abs(layout.stock.thickness - part.thickness) >= EPS:
                errors.append(f"{part.name}: niezgodny materiał lub grubość")
    for part in result.unplaced_sheet_parts:
        if not part.is_waste_fill:
            actual[_part_key(part)] += 1
    used_area = math.fsum(layout.used_area for layout in result.sheet_layouts)
    consumed_area = math.fsum(layout.consumed_area for layout in result.sheet_layouts)
    expected_waste = max(0.0, consumed_area - used_area)
    expected_utilization = used_area / consumed_area * 100.0 if consumed_area else 0.0
    for name, value, calculated in (
        ("odpad", result.waste, expected_waste),
        ("wykorzystanie", result.utilization, expected_utilization),
    ):
        if not math.isfinite(value) or not math.isclose(value, calculated, rel_tol=1e-9, abs_tol=EPS):
            errors.append(f"Nieprawidłowe statystyki: {name}")
    if actual != expected:
        errors.append("Liczba lub specyfikacja formatek nie odpowiada zamówieniu.")
    if errors:
        raise RuntimeError("Blokada niepoprawnego rozkroju: " + "; ".join(errors[:8]))

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.models import PlacedSheetPart, SheetLayout

EPS = 0.001
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class SlicingProof:
    feasible: bool
    tree: dict[str, Any] | None
    reason: str = ""


def _part_node(part: PlacedSheetPart) -> dict[str, Any]:
    return {
        "type": "part",
        "x": part.x,
        "y": part.y,
        "width": part.width,
        "height": part.height,
        "part": part.part.name,
        "rotated": part.rotated,
    }


def _bounds(parts: list[PlacedSheetPart], region: Rect) -> Rect:
    if not parts:
        return region
    min_x = min(part.x for part in parts)
    min_y = min(part.y for part in parts)
    max_x = max(part.x + part.width for part in parts)
    max_y = max(part.y + part.height for part in parts)
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def _vertical_splits(parts: list[PlacedSheetPart], kerf: float) -> list[tuple[float, list[PlacedSheetPart], list[PlacedSheetPart]]]:
    splits: list[tuple[float, list[PlacedSheetPart], list[PlacedSheetPart]]] = []
    right_edges = sorted({round(part.x + part.width, 4) for part in parts})
    for edge in right_edges:
        left = [part for part in parts if part.x + part.width <= edge + EPS]
        right = [part for part in parts if part.x >= edge + kerf - EPS]
        if not left or not right or len(left) + len(right) != len(parts):
            continue
        left_max = max(part.x + part.width for part in left)
        right_min = min(part.x for part in right)
        if right_min - left_max + EPS >= kerf:
            splits.append(((left_max + right_min) / 2.0, left, right))
    return splits


def _horizontal_splits(parts: list[PlacedSheetPart], kerf: float) -> list[tuple[float, list[PlacedSheetPart], list[PlacedSheetPart]]]:
    splits: list[tuple[float, list[PlacedSheetPart], list[PlacedSheetPart]]] = []
    bottom_edges = sorted({round(part.y + part.height, 4) for part in parts})
    for edge in bottom_edges:
        top = [part for part in parts if part.y + part.height <= edge + EPS]
        bottom = [part for part in parts if part.y >= edge + kerf - EPS]
        if not top or not bottom or len(top) + len(bottom) != len(parts):
            continue
        top_max = max(part.y + part.height for part in top)
        bottom_min = min(part.y for part in bottom)
        if bottom_min - top_max + EPS >= kerf:
            splits.append(((top_max + bottom_min) / 2.0, top, bottom))
    return splits


def _build_tree(parts: list[PlacedSheetPart], region: Rect, kerf: float, depth: int = 0) -> dict[str, Any] | None:
    if not parts:
        return {"type": "waste", "x": region[0], "y": region[1], "width": region[2], "height": region[3]}
    if len(parts) == 1:
        return _part_node(parts[0])
    if depth > max(12, len(parts) * 2):
        return None

    vertical = _vertical_splits(parts, kerf)
    horizontal = _horizontal_splits(parts, kerf)
    choices: list[tuple[tuple[float, int], str, float, list[PlacedSheetPart], list[PlacedSheetPart]]] = []
    for position, left, right in vertical:
        balance = abs(len(left) - len(right))
        choices.append(((balance, 0), "vertical", position, left, right))
    for position, top, bottom in horizontal:
        balance = abs(len(top) - len(bottom))
        choices.append(((balance, 1), "horizontal", position, top, bottom))

    for _, orientation, position, first, second in sorted(choices, key=lambda item: item[0]):
        first_region = _bounds(first, region)
        second_region = _bounds(second, region)
        first_tree = _build_tree(first, first_region, kerf, depth + 1)
        if first_tree is None:
            continue
        second_tree = _build_tree(second, second_region, kerf, depth + 1)
        if second_tree is None:
            continue
        return {
            "type": "recursive-slicing",
            "orientation": orientation,
            "position": position,
            "kerf": kerf,
            "first": first_tree,
            "second": second_tree,
        }
    return None


def build_slicing_tree(layout: SheetLayout, kerf: float = 0.0) -> SlicingProof:
    parts = list(layout.parts)
    if not parts:
        return SlicingProof(True, {"type": "empty", "width": layout.stock.width, "height": layout.stock.height})

    for part in parts:
        if part.x < -EPS or part.y < -EPS:
            return SlicingProof(False, None, f"{part.part.name}: poza plyta")
        if part.x + part.width > layout.stock.width + EPS or part.y + part.height > layout.stock.height + EPS:
            return SlicingProof(False, None, f"{part.part.name}: wystaje poza plyte")

    region = (0.0, 0.0, layout.stock.width, layout.stock.height)
    tree = _build_tree(parts, region, max(0.0, float(kerf or 0.0)))
    if tree is None:
        return SlicingProof(False, None, "brak rekurencyjnego pelnego ciecia")
    return SlicingProof(True, tree)

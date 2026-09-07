"""Physical side alignment, shared by every packing and validation path.

New projects use x (model width) and y (model height). Legacy length/width
values retain their original no-rotation behaviour. A part with an explicit
axis requires a board whose grain axis has also been marked.
"""
from __future__ import annotations

from functools import lru_cache

from core.models import SheetPart, SheetStock


@lru_cache(maxsize=4096)
def orientation_options(width: float, height: float, rotate: bool, grain: str,
                        stock_rotate: bool, stock_grain: str) -> tuple[tuple[float, float, bool], ...]:
    if grain in {"x", "y"}:
        if stock_grain not in {"x", "y"}:
            return ()
        required_rotation = grain != stock_grain
        if required_rotation and not (rotate and stock_rotate):
            return ()
        return ((height, width, True),) if required_rotation else ((width, height, False),)
    options = [(width, height, False)]
    if rotate and stock_rotate and grain == "none" and abs(width - height) > 1e-8:
        options.append((height, width, True))
    return tuple(options)


def part_orientations(part: SheetPart, stock: SheetStock) -> tuple[tuple[float, float, bool], ...]:
    return orientation_options(part.width, part.height, part.allow_rotation,
                               part.grain_direction, stock.allow_rotation, stock.grain_direction)

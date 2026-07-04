"""Shared helpers for classifying parts and stock geometry.

This module hosts the *canonical* implementations of part-classification
helpers used by both the vertical-segmented optimizer and the
optimizer_worker (e.g. for choosing bonus-fill candidates on missing
sheets).  Centralising the logic here prevents the rules from drifting
between call-sites.
"""
from __future__ import annotations

from core.models import SheetPart, SheetStock

EPS = 1e-9

# Known sheet profiles → strategic (constraining) dimension.  The strategic
# dimension drives difficulty scoring and "is this part big enough to be
# structural?" decisions in the vertical-segmented optimizer.
_STRATEGIC_PROFILES: dict[tuple[int, int], float] = {
    (1000, 2000): 1000.0,
    (1500, 3000): 1500.0,
    (2050, 3050): 2050.0,
    (1300, 1400): 1300.0,
}
_KNOWN_STRATEGIC_SIDES = tuple(sorted(set(_STRATEGIC_PROFILES.values())))


def stock_profile_key(stock: SheetStock) -> tuple[int, int]:
    return tuple(sorted((round(stock.width), round(stock.height))))  # type: ignore[return-value]


def strategic_dimension(stock: SheetStock) -> float:
    """Return the *strategic* (constraining) dimension of *stock*.

    This is the dimension the shop wants to use as fully as possible.  For
    known profiles it is explicit (1000x2000 -> 1000, 1500x3000 -> 1500,
    2050x3050 -> 2050).  If a custom stock contains one of those strategic
    sides (e.g. 1000x620), that side remains strategic even when it is not the
    shorter side.  Only fully unknown profiles fall back to the shorter side.
    """
    exact = _STRATEGIC_PROFILES.get(stock_profile_key(stock))
    if exact is not None:
        return exact
    sides = (stock.width, stock.height)
    side_matches: list[float] = []
    for side in sides:
        for known in _KNOWN_STRATEGIC_SIDES:
            if abs(side - known) / known <= 0.01:
                side_matches.append(known)
                break
    if side_matches:
        return min(side_matches)

    sw, sh = sorted(sides)
    for (pw, ph), strategic in _STRATEGIC_PROFILES.items():
        if abs(sw - pw) / pw <= 0.08 and abs(sh - ph) / ph <= 0.08:
            return strategic
    return sw


def is_small_filler_part(part: SheetPart, stock: SheetStock) -> bool:
    """Return True if *part* is small enough to act as a *filler*.

    Filler parts are tiny relative to the stock and to the strategic
    dimension.  They are not allowed to drive the main layout — they
    must be placed *into* waste areas after structural parts.

    Used by:
      - vertical-segmented optimizer (filler ordering, waste-recovery,
        repair pass)
      - optimizer_worker (bonus-fill candidates for missing-sheet runs)
    """
    sheet_area = max(stock.width * stock.height, EPS)
    strategic = max(strategic_dimension(stock), EPS)
    longest = max(part.width, part.height)
    area_ratio = part.width * part.height / sheet_area
    strategic_limited = longest >= strategic * 0.45
    return longest <= strategic * 0.12 or (area_ratio <= 0.012 and not strategic_limited)

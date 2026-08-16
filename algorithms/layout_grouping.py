from __future__ import annotations

"""Group sheet layouts that are cut identically.

When a job needs ten boards cut exactly the same way, the operator only needs to
see one drawing labelled ``×10`` — not ten copies.  This module produces a
canonical signature per :class:`SheetLayout` and groups equal ones together,
preserving first-seen order.
"""

from dataclasses import dataclass

from core.models import SheetLayout


@dataclass
class LayoutGroup:
    representative: SheetLayout
    count: int
    indices: list[int]            # original positions of every member
    sheet_indices: list[int]      # SheetLayout.sheet_index of every member
    display_sheet_indices: list[int]  # numbering within material/thickness group


def format_sheet_number_ranges(numbers: list[int] | tuple[int, ...]) -> str:
    """Return compact, human-readable sheet numbers (``1–3, 5, 7–9``).

    A grouped layout can represent dozens of identical boards.  Listing every
    number made the preview header, navigator and PDF unreadable, while this
    keeps the complete numbering unambiguous.
    """
    values = sorted({int(value) for value in numbers})
    if not values:
        return ""
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}–{previous}")
    return ", ".join(ranges)


def layout_signature(layout: SheetLayout) -> tuple:
    """Order-independent fingerprint of how a board is cut.

    Two layouts share a signature iff their stock size and the full multiset of
    placed parts (position, size, rotation) match.
    """
    stock = layout.stock
    parts = tuple(
        sorted(
            (
                round(p.x, 1),
                round(p.y, 1),
                round(p.width, 1),
                round(p.height, 1),
                bool(p.rotated),
            )
            for p in layout.parts
        )
    )
    return (
        round(stock.width, 1),
        round(stock.height, 1),
        str(stock.material),
        round(float(getattr(stock, "thickness", 0.0) or 0.0), 3),
        max(1, int(getattr(stock, "stack_size", 1) or 1)),
        parts,
    )


def group_identical_layouts(layouts: list[SheetLayout]) -> list[LayoutGroup]:
    """Collapse identical layouts into groups, keeping first-seen order."""
    groups: list[LayoutGroup] = []
    by_sig: dict[tuple, LayoutGroup] = {}
    for index, layout in enumerate(layouts):
        sig = layout_signature(layout)
        group = by_sig.get(sig)
        if group is None:
            group = LayoutGroup(
                representative=layout,
                count=0,
                indices=[],
                sheet_indices=[],
                display_sheet_indices=[],
            )
            by_sig[sig] = group
            groups.append(group)
        group.count += 1
        group.indices.append(index)
        group.sheet_indices.append(getattr(layout, "sheet_index", index + 1))
        group.display_sheet_indices.append(getattr(layout, "display_sheet_index", index + 1))
    return groups

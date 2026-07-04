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
    return (round(stock.width, 1), round(stock.height, 1), str(stock.material), parts)


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
            )
            by_sig[sig] = group
            groups.append(group)
        group.count += 1
        group.indices.append(index)
        group.sheet_indices.append(getattr(layout, "sheet_index", index + 1))
    return groups

from __future__ import annotations

from dataclasses import replace

from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetPart, SheetStock, materials_are_compatible

Rect = tuple[float, float, float, float]


def _expand_parts(parts: list[SheetPart]) -> list[SheetPart]:
    expanded: list[SheetPart] = []
    for part in parts:
        for index in range(part.quantity):
            expanded.append(replace(part, quantity=1, label=part.label or f"{part.name}-{index + 1}"))
    return sorted(expanded, key=lambda p: (-p.priority, -(p.width * p.height), max(p.width, p.height), p.name))


def _expand_stock(stock: list[SheetStock]) -> list[SheetStock]:
    expanded: list[SheetStock] = []
    for item in stock:
        for _ in range(item.quantity):
            expanded.append(replace(item, quantity=1))
    return sorted(
        expanded,
        key=lambda s: (
            -int(getattr(s, "priority", 0) or 0),
            s.material,
            s.thickness,
            s.width * s.height,
            s.price,
        ),
    )


def _orientations(part: SheetPart, stock: SheetStock) -> list[tuple[float, float, bool]]:
    options = [(part.width, part.height, False)]
    if part.allow_rotation and stock.allow_rotation and part.grain_direction == "none":
        options.append((part.height, part.width, True))
    return options


def _fits(part: SheetPart, stock: SheetStock, rect: Rect, kerf: float) -> tuple[bool, float, float, bool]:
    _, _, rw, rh = rect
    options = _orientations(part, stock)
    for width, height, rotated in options:
        if width <= rw + 0.0001 and height <= rh + 0.0001:
            return True, width, height, rotated
        if width + kerf <= rw + 0.0001 and height + kerf <= rh + 0.0001:
            return True, width, height, rotated
    return False, 0.0, 0.0, False


def _prune(free_rects: list[Rect]) -> list[Rect]:
    result: list[Rect] = []
    for i, a in enumerate(free_rects):
        ax, ay, aw, ah = a
        if aw <= 0.001 or ah <= 0.001:
            continue
        contained = False
        for j, b in enumerate(free_rects):
            if i == j:
                continue
            bx, by, bw, bh = b
            if ax >= bx and ay >= by and ax + aw <= bx + bw and ay + ah <= by + bh:
                contained = True
                break
        if not contained:
            result.append(a)
    return result


def _split_free_rects(free_rects: list[Rect], used: Rect, kerf: float) -> list[Rect]:
    ux, uy, uw, uh = used
    next_free: list[Rect] = []
    for rect in free_rects:
        x, y, w, h = rect
        if ux >= x + w or ux + uw <= x or uy >= y + h or uy + uh <= y:
            next_free.append(rect)
            continue
        right_w = x + w - (ux + uw + kerf)
        bottom_h = y + h - (uy + uh + kerf)
        if right_w > 0:
            next_free.append((ux + uw + kerf, y, right_w, h))
        if bottom_h > 0:
            next_free.append((x, uy + uh + kerf, w, bottom_h))
        left_w = ux - x - kerf
        top_h = uy - y - kerf
        if left_w > 0:
            next_free.append((x, y, left_w, h))
        if top_h > 0:
            next_free.append((x, y, w, top_h))
    return _prune(next_free)


def _used_bounds(layout: SheetLayout) -> tuple[float, float]:
    if not layout.parts:
        return 0.0, 0.0
    return (
        max(placement.x + placement.width for placement in layout.parts),
        max(placement.y + placement.height for placement in layout.parts),
    )


def _candidate_score(layout: SheetLayout, rect: Rect, width: float, height: float) -> tuple[float, float, float, float, float]:
    x, y, rw, rh = rect
    current_width, current_height = _used_bounds(layout)
    used_width = max(current_width, x + width)
    used_height = max(current_height, y + height)
    bounding_area = used_width * max(used_height, 1.0)
    height_fill = used_height / layout.stock.height if layout.stock.height else 0.0
    short_side = min(rw - width, rh - height)
    long_side = max(rw - width, rh - height)
    return (
        used_width,
        -height_fill,
        bounding_area,
        y,
        short_side * 10_000 + long_side,
    )


def _place_part(layout: SheetLayout, free_rects: list[Rect], part: SheetPart, kerf: float) -> bool:
    best: tuple[tuple[float, float, float, float, float], int, float, float, bool] | None = None
    for index, rect in enumerate(free_rects):
        _, _, rw, rh = rect
        for width, height, rotated in _orientations(part, layout.stock):
            if width <= rw + 0.0001 and height <= rh + 0.0001:
                score = _candidate_score(layout, rect, width, height)
                if best is None or score < best[0]:
                    best = (score, index, width, height, rotated)
    if best is None:
        return False
    _, rect_index, width, height, rotated = best
    x, y, _, _ = free_rects[rect_index]
    placement = PlacedSheetPart(part=part, x=x, y=y, width=width, height=height, rotated=rotated)
    layout.parts.append(placement)
    free_rects[:] = _split_free_rects(free_rects, (x, y, width, height), kerf)
    return True


def _strip_offcuts(layout: SheetLayout, kerf: float) -> list[Rect]:
    if not layout.parts:
        return []

    offcuts: list[Rect] = []
    right_x = min(layout.stock.width, layout.used_width + kerf)
    if right_x < layout.stock.width - 0.001:
        offcuts.append((right_x, 0.0, layout.stock.width - right_x, layout.stock.height))

    bottom_y = min(layout.stock.height, layout.used_height + kerf)
    if bottom_y < layout.stock.height - 0.001 and layout.used_width > 0.001:
        offcuts.append((0.0, bottom_y, layout.used_width, layout.stock.height - bottom_y))
    return offcuts


def optimize_2d_maxrects(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float = 3.0,
    margin: float = 0.0,
    mode: str = "minimize_waste",
) -> OptimizationResult:
    layouts: list[SheetLayout] = []
    free_by_layout: dict[int, list[Rect]] = {}
    unplaced: list[SheetPart] = []
    available_stock = _expand_stock(stock)

    for part in _expand_parts(parts):
        placed = False
        for layout in layouts:
            if not materials_are_compatible(layout.stock.material, part.material) or abs(layout.stock.thickness - part.thickness) > 0.001:
                continue
            if _place_part(layout, free_by_layout[id(layout)], part, kerf):
                placed = True
                break
        if placed:
            continue

        stock_index = next(
            (
                i
                for i, item in enumerate(available_stock)
                if materials_are_compatible(item.material, part.material)
                and abs(item.thickness - part.thickness) < 0.001
                and (
                    (part.width <= item.width - margin * 2 and part.height <= item.height - margin * 2)
                    or (
                        part.allow_rotation
                        and item.allow_rotation
                        and part.grain_direction == "none"
                        and part.height <= item.width - margin * 2
                        and part.width <= item.height - margin * 2
                    )
                )
            ),
            None,
        )
        if stock_index is None:
            unplaced.append(part)
            continue
        selected = available_stock.pop(stock_index)
        layout = SheetLayout(stock=selected, sheet_index=len(layouts) + 1)
        layouts.append(layout)
        free_by_layout[id(layout)] = [(margin, margin, selected.width - margin * 2, selected.height - margin * 2)]
        if not _place_part(layout, free_by_layout[id(layout)], part, kerf):
            unplaced.append(part)

    total_area = sum(layout.consumed_area for layout in layouts)
    used_area = sum(layout.used_area for layout in layouts)
    total_cost = sum(layout.stock.price for layout in layouts)
    reusable = []
    for layout in layouts:
        layout.offcuts = [
            rect
            for rect in _strip_offcuts(layout, kerf)
            if rect[2] >= layout.stock.min_offcut_width and rect[3] >= layout.stock.min_offcut_height
        ]
        for x, y, w, h in layout.offcuts:
            reusable.append(
                {
                    "type": "sheet",
                    "material": layout.stock.material,
                    "thickness": layout.stock.thickness,
                    "width": round(w, 2),
                    "height": round(h, 2),
                    "source": f"Sheet {layout.sheet_index}",
                    "x": round(x, 2),
                    "y": round(y, 2),
                }
            )

    if mode == "minimize_cost":
        layouts.sort(key=lambda x: x.stock.price)
    messages = []
    if unplaced:
        messages.append(f"{len(unplaced)} sheet part(s) could not be placed with available stock.")
    utilization = used_area / total_area * 100.0 if total_area else 0.0
    return OptimizationResult(
        job_type="sheet",
        algorithm="MaxRects",
        sheet_layouts=layouts,
        unplaced_sheet_parts=unplaced,
        total_cost=total_cost,
        waste=max(0.0, total_area - used_area),
        utilization=utilization,
        reusable_offcuts=reusable,
        messages=messages,
    )

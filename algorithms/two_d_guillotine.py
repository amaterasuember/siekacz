from __future__ import annotations

from dataclasses import replace

from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetPart, SheetStock, materials_are_compatible

Rect = tuple[float, float, float, float]


def _expand_parts(parts: list[SheetPart]) -> list[SheetPart]:
    expanded: list[SheetPart] = []
    for part in parts:
        for index in range(part.quantity):
            expanded.append(replace(part, quantity=1, label=part.label or f"{part.name}-{index + 1}"))
    return sorted(expanded, key=lambda p: (-p.priority, -max(p.width, p.height), -(p.width * p.height)))


def _expand_stock(stock: list[SheetStock]) -> list[SheetStock]:
    expanded: list[SheetStock] = []
    for item in stock:
        for _ in range(item.quantity):
            expanded.append(replace(item, quantity=1))
    return sorted(
        expanded,
        key=lambda s: (-int(getattr(s, "priority", 0) or 0), s.material, s.thickness, s.width * s.height),
    )


def _orientations(part: SheetPart, stock: SheetStock) -> list[tuple[float, float, bool]]:
    options = [(part.width, part.height, False)]
    if part.allow_rotation and stock.allow_rotation and part.grain_direction == "none":
        options.append((part.height, part.width, True))
    return options


def _used_bounds(layout: SheetLayout) -> tuple[float, float]:
    if not layout.parts:
        return 0.0, 0.0
    return (
        max(placement.x + placement.width for placement in layout.parts),
        max(placement.y + placement.height for placement in layout.parts),
    )


def _candidate_score(layout: SheetLayout, rect: Rect, width: float, height: float) -> tuple[float, float, float, float, float]:
    x, y, free_width, free_height = rect
    current_width, current_height = _used_bounds(layout)
    used_width = max(current_width, x + width)
    used_height = max(current_height, y + height)
    bounding_area = used_width * max(used_height, 1.0)
    height_fill = used_height / layout.stock.height if layout.stock.height else 0.0
    local_waste = free_width * free_height - width * height

    return (
        used_width,
        -height_fill,
        bounding_area,
        y,
        local_waste,
    )


def _try_place(layout: SheetLayout, free_rects: list[Rect], part: SheetPart, kerf: float) -> bool:
    best: tuple[tuple[float, float, float, float, float], int, float, float, bool] | None = None
    for index, (x, y, w, h) in enumerate(free_rects):
        for pw, ph, rotated in _orientations(part, layout.stock):
            if pw <= w + 0.0001 and ph <= h + 0.0001:
                score = _candidate_score(layout, (x, y, w, h), pw, ph)
                if best is None or score < best[0]:
                    best = (score, index, pw, ph, rotated)
    if best is None:
        return False

    _, index, pw, ph, rotated = best
    x, y, w, h = free_rects.pop(index)
    layout.parts.append(PlacedSheetPart(part=part, x=x, y=y, width=pw, height=ph, rotated=rotated))

    right = (x + pw + kerf, y, max(0.0, w - pw - kerf), ph)
    bottom = (x, y + ph + kerf, w, max(0.0, h - ph - kerf))
    remainder = [r for r in (right, bottom) if r[2] > 0.001 and r[3] > 0.001]
    free_rects.extend(sorted(remainder, key=lambda r: r[2] * r[3], reverse=True))
    free_rects.sort(key=lambda r: (r[1], r[0]))
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


def optimize_2d_guillotine(
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
            if materials_are_compatible(layout.stock.material, part.material) and abs(layout.stock.thickness - part.thickness) < 0.001:
                if _try_place(layout, free_by_layout[id(layout)], part, kerf):
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
                and any(pw <= item.width - margin * 2 and ph <= item.height - margin * 2 for pw, ph, _ in _orientations(part, item))
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
        if not _try_place(layout, free_by_layout[id(layout)], part, kerf):
            unplaced.append(part)

    total_area = sum(l.consumed_area for l in layouts)
    used_area = sum(l.used_area for l in layouts)
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
    messages = [f"{len(unplaced)} sheet part(s) could not be placed with available stock."] if unplaced else []
    return OptimizationResult(
        job_type="sheet",
        algorithm="Guillotine",
        sheet_layouts=layouts,
        unplaced_sheet_parts=unplaced,
        total_cost=sum(l.stock.price for l in layouts),
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        reusable_offcuts=reusable,
        messages=messages,
    )

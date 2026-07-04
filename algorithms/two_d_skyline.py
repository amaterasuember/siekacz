from __future__ import annotations

from dataclasses import replace

from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetPart, SheetStock


def _expand_parts(parts: list[SheetPart]) -> list[SheetPart]:
    expanded: list[SheetPart] = []
    for part in parts:
        for index in range(part.quantity):
            expanded.append(replace(part, quantity=1, label=part.label or f"{part.name}-{index + 1}"))
    return sorted(expanded, key=lambda p: (-p.priority, -p.height, -p.width))


def _expand_stock(stock: list[SheetStock]) -> list[SheetStock]:
    expanded: list[SheetStock] = []
    for item in stock:
        for _ in range(item.quantity):
            expanded.append(replace(item, quantity=1))
    return sorted(expanded, key=lambda s: (s.material, s.thickness, s.width * s.height))


def _orientations(part: SheetPart, stock: SheetStock) -> list[tuple[float, float, bool]]:
    options = [(part.width, part.height, False)]
    if part.allow_rotation and stock.allow_rotation and part.grain_direction == "none":
        options.append((part.height, part.width, True))
    return options


def _place(layout: SheetLayout, shelves: list[dict[str, float]], part: SheetPart, kerf: float, margin: float) -> bool:
    for shelf in shelves:
        for pw, ph, rotated in _orientations(part, layout.stock):
            if ph <= shelf["height"] and shelf["x"] + pw <= layout.stock.width - margin + 0.0001:
                x = shelf["x"]
                y = shelf["y"]
                layout.parts.append(PlacedSheetPart(part=part, x=x, y=y, width=pw, height=ph, rotated=rotated))
                shelf["x"] += pw + kerf
                return True

    y = margin if not shelves else max(s["y"] + s["height"] + kerf for s in shelves)
    for pw, ph, rotated in _orientations(part, layout.stock):
        if pw <= layout.stock.width - margin * 2 + 0.0001 and y + ph <= layout.stock.height - margin + 0.0001:
            shelves.append({"x": margin + pw + kerf, "y": y, "height": ph})
            layout.parts.append(PlacedSheetPart(part=part, x=margin, y=y, width=pw, height=ph, rotated=rotated))
            return True
    return False


def _strip_offcuts(layout: SheetLayout, kerf: float) -> list[tuple[float, float, float, float]]:
    if not layout.parts:
        return []

    offcuts: list[tuple[float, float, float, float]] = []
    right_x = min(layout.stock.width, layout.used_width + kerf)
    if right_x < layout.stock.width - 0.001:
        offcuts.append((right_x, 0.0, layout.stock.width - right_x, layout.stock.height))

    bottom_y = min(layout.stock.height, layout.used_height + kerf)
    if bottom_y < layout.stock.height - 0.001 and layout.used_width > 0.001:
        offcuts.append((0.0, bottom_y, layout.used_width, layout.stock.height - bottom_y))
    return offcuts


def optimize_2d_skyline(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float = 3.0,
    margin: float = 0.0,
    mode: str = "minimize_waste",
) -> OptimizationResult:
    layouts: list[SheetLayout] = []
    shelves_by_layout: dict[int, list[dict[str, float]]] = {}
    unplaced: list[SheetPart] = []
    available_stock = _expand_stock(stock)

    for part in _expand_parts(parts):
        placed = False
        for layout in layouts:
            if layout.stock.material == part.material and abs(layout.stock.thickness - part.thickness) < 0.001:
                if _place(layout, shelves_by_layout[id(layout)], part, kerf, margin):
                    placed = True
                    break
        if placed:
            continue

        stock_index = next(
            (
                i
                for i, item in enumerate(available_stock)
                if item.material == part.material
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
        shelves_by_layout[id(layout)] = []
        if not _place(layout, shelves_by_layout[id(layout)], part, kerf, margin):
            unplaced.append(part)

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
    total_area = sum(l.consumed_area for l in layouts)
    used_area = sum(l.used_area for l in layouts)
    messages = [f"{len(unplaced)} sheet part(s) could not be placed with available stock."] if unplaced else []
    return OptimizationResult(
        job_type="sheet",
        algorithm="Skyline",
        sheet_layouts=layouts,
        unplaced_sheet_parts=unplaced,
        total_cost=sum(l.stock.price for l in layouts),
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        reusable_offcuts=reusable,
        messages=messages,
    )

from __future__ import annotations

from dataclasses import replace

from core.models import LinearLayout, LinearPart, LinearPlacement, LinearStock, OptimizationResult


def _expand_parts(parts: list[LinearPart]) -> list[LinearPart]:
    expanded: list[LinearPart] = []
    for part in parts:
        for index in range(part.quantity):
            expanded.append(replace(part, quantity=1, label=part.label or f"{part.name}-{index + 1}"))
    return sorted(expanded, key=lambda p: (-p.priority, -p.length, p.name))


def _expand_stock(stock: list[LinearStock]) -> list[LinearStock]:
    expanded: list[LinearStock] = []
    for item in stock:
        for _ in range(item.quantity):
            expanded.append(replace(item, quantity=1))
    return sorted(expanded, key=lambda s: (s.material, s.length, s.price))


def _required_length(layout: LinearLayout, part: LinearPart, kerf: float) -> float:
    return part.length + (kerf if layout.placements else 0.0)


def _remaining(layout: LinearLayout, kerf: float) -> float:
    used = sum(p.length for p in layout.placements)
    gaps = max(0, len(layout.placements) - 1) * kerf
    return layout.stock.length - used - gaps


def optimize_1d(
    stock: list[LinearStock],
    parts: list[LinearPart],
    method: str = "Best Fit Decreasing",
    mode: str = "minimize_waste",
) -> OptimizationResult:
    layouts: list[LinearLayout] = []
    unplaced: list[LinearPart] = []
    available_stock = _expand_stock(stock)
    expanded_parts = _expand_parts(parts)

    for part in expanded_parts:
        candidates: list[tuple[float, LinearLayout]] = []
        for layout in layouts:
            if layout.stock.material != part.material:
                continue
            kerf = layout.stock.kerf
            remaining_after = _remaining(layout, kerf) - _required_length(layout, part, kerf)
            if remaining_after >= -0.0001:
                score = remaining_after if method.lower().startswith("best") else len(layout.placements)
                candidates.append((score, layout))

        target: LinearLayout | None = None
        if candidates:
            target = min(candidates, key=lambda x: x[0])[1] if method.lower().startswith("best") else candidates[0][1]
        else:
            stock_index = next((i for i, s in enumerate(available_stock) if s.material == part.material and part.length <= s.length), None)
            if stock_index is None:
                unplaced.append(part)
                continue
            selected_stock = available_stock.pop(stock_index)
            target = LinearLayout(stock=selected_stock, bar_index=len(layouts) + 1)
            layouts.append(target)

        kerf = target.stock.kerf
        start = 0.0
        if target.placements:
            previous = target.placements[-1]
            start = previous.start + previous.length + kerf
        target.placements.append(LinearPlacement(part=part, start=start, length=part.length))

    total_length = sum(layout.stock.length for layout in layouts)
    used_length = sum(sum(p.length for p in layout.placements) for layout in layouts)
    kerf_loss = sum(max(0, len(layout.placements) - 1) * layout.stock.kerf for layout in layouts)
    total_cost = sum(layout.stock.price for layout in layouts)
    reusable = []
    for layout in layouts:
        layout.leftover = max(0.0, _remaining(layout, layout.stock.kerf))
        if layout.leftover >= layout.stock.min_offcut_length:
            reusable.append(
                {
                    "type": "linear",
                    "material": layout.stock.material,
                    "profile": layout.stock.profile,
                    "length": round(layout.leftover, 2),
                    "source": f"Bar {layout.bar_index}",
                }
            )

    denominator = total_length or 1.0
    waste = max(0.0, total_length - used_length - kerf_loss)
    utilization = used_length / denominator * 100.0
    if mode == "minimize_cost":
        layouts.sort(key=lambda x: x.stock.price)

    messages = []
    if unplaced:
        messages.append(f"{len(unplaced)} linear part(s) could not be placed with available stock.")

    return OptimizationResult(
        job_type="linear",
        algorithm=method,
        linear_layouts=layouts,
        unplaced_linear_parts=unplaced,
        total_cost=total_cost,
        waste=waste,
        utilization=utilization,
        reusable_offcuts=reusable,
        messages=messages,
    )


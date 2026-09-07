from __future__ import annotations

"""Select a waste-minimising mixture of supplier sheet formats.

The production optimiser remains responsible for every individual sheet.  This
module only combines already legal, kerf-aware sheet layouts into a global
purchase plan.  It therefore cannot invent a geometrically attractive but
uncuttable arrangement.
"""

import math
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, replace

from algorithms.guillotine_technology import annotate_guillotine_result
from algorithms.layout_scoring import annotate_result_metrics
from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.grain import part_orientations
from core.models import OptimizationResult, SheetLayout, SheetPart, SheetStock, materials_are_compatible


_EPS = 1e-6
_BEAM_WIDTH = 192


def _part_key(part: SheetPart) -> tuple[object, ...]:
    return (
        part.name,
        round(float(part.width), 4),
        round(float(part.height), 4),
        str(part.material or "").strip().casefold(),
        round(float(part.thickness), 4),
        bool(part.allow_rotation),
        str(part.grain_direction or "none"),
        int(part.priority),
    )


def _aggregate_parts(parts: list[SheetPart]) -> list[SheetPart]:
    grouped: dict[tuple[object, ...], SheetPart] = {}
    quantities: Counter[tuple[object, ...]] = Counter()
    for part in parts:
        key = _part_key(part)
        grouped.setdefault(key, part)
        quantities[key] += int(part.quantity)
    return [replace(grouped[key], quantity=quantity) for key, quantity in quantities.items()]


def _fits(part: SheetPart, stock: SheetStock) -> bool:
    return any(w <= stock.width + _EPS and h <= stock.height + _EPS
               for w, h, _ in part_orientations(part, stock))


@dataclass(frozen=True)
class _Pattern:
    layout: SheetLayout
    counts: tuple[int, ...]
    stock_area: float
    placed_area: float
    cost: float
    cut_count: int


@dataclass(frozen=True)
class _SearchState:
    remaining: tuple[int, ...]
    chosen: tuple[int, ...]
    purchased_area: float
    placed_area: float
    cost: float
    cut_count: int

    @property
    def sheets(self) -> int:
        return len(self.chosen)


def _candidate_key(stock: SheetStock) -> tuple[object, ...]:
    return (
        str(stock.material or "").strip().casefold(),
        round(float(stock.thickness), 4),
        tuple(sorted((round(float(stock.width), 4), round(float(stock.height), 4)))),
        round(float(stock.price), 4),
        bool(stock.allow_rotation),
        str(stock.grain_direction or "none"),
        str(getattr(stock, "preferred_cut_axis", "auto") or "auto"),
    )


def _deduplicate_stock(stock: list[SheetStock]) -> list[SheetStock]:
    unique: dict[tuple[object, ...], SheetStock] = {}
    for item in stock:
        unique.setdefault(_candidate_key(item), replace(item, quantity=1, stack_size=1))
    return sorted(unique.values(), key=lambda item: (item.width * item.height, item.price, item.width, item.height))


def _uniform_sheet_limit(stock: SheetStock, parts: list[SheetPart]) -> int:
    total_pieces = sum(int(part.quantity) for part in parts)
    total_area = sum(part.width * part.height * part.quantity for part in parts)
    # 62% is deliberately conservative.  If a difficult geometry still leaves
    # parts unplaced, the caller doubles the limit until the uniform run is
    # complete or reaches the trivial one-part-per-sheet upper bound.
    estimate = math.ceil(total_area / max(stock.width * stock.height * 0.62, _EPS))
    return max(1, min(total_pieces, estimate + len(parts) + 1))


def _patterns_for_format(
    stock: SheetStock,
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
    min_reusable_size: float,
    cutting_mode: str,
    optimization_mode: str,
) -> tuple[list[_Pattern], int]:
    eligible = [
        part for part in parts
        if materials_are_compatible(stock.material, part.material)
        and abs(stock.thickness - part.thickness) < 0.001
        and _fits(part, stock)
    ]
    if not eligible:
        return [], 0
    total_pieces = sum(int(part.quantity) for part in eligible)
    limit = _uniform_sheet_limit(stock, eligible)
    result: OptimizationResult | None = None
    while True:
        result = optimize_2d_vertical_segmented(
            [replace(stock, quantity=limit, stack_size=1, source="stock")],
            eligible,
            kerf,
            margin,
            mode,
            min_reusable_size,
            cutting_mode,
            optimization_mode,
        )
        if not result.unplaced_sheet_parts or limit >= total_pieces:
            break
        limit = min(total_pieces, max(limit + 1, limit * 2))

    keys = [_part_key(part) for part in parts]
    key_to_index = {key: index for index, key in enumerate(keys)}
    patterns: list[_Pattern] = []
    seen: set[tuple[object, ...]] = set()

    def register(layout: SheetLayout) -> None:
        counts = [0] * len(parts)
        for placement in layout.parts:
            index = key_to_index.get(_part_key(placement.part))
            if index is not None:
                counts[index] += 1
        if not any(counts):
            return
        signature = (_candidate_key(layout.stock), tuple(counts))
        if signature in seen:
            return
        seen.add(signature)
        patterns.append(
            _Pattern(
                layout=deepcopy(layout),
                counts=tuple(counts),
                stock_area=float(layout.stock.width * layout.stock.height),
                placed_area=float(layout.used_area),
                cost=float(layout.stock.price),
                cut_count=int(getattr(layout, "cut_count", 0) or 0),
            )
        )

    for layout in result.sheet_layouts:
        register(layout)
        # A uniform full-sheet run naturally yields e.g. 5+5 pieces.  A mixed
        # solution may need only 1–3 pieces on its last small sheet after a
        # larger format absorbed the rest.  Removing placements from a legal
        # gillotine layout cannot create a collision; re-annotation below
        # rebuilds the cut tree and exposes these useful remainder patterns.
        grouped_positions: dict[int, list[int]] = {}
        for position, placement in enumerate(layout.parts):
            index = key_to_index.get(_part_key(placement.part))
            if index is not None:
                grouped_positions.setdefault(index, []).append(position)
        for part_index, positions in grouped_positions.items():
            count = len(positions)
            if count <= 1:
                continue
            for keep in sorted({1, min(2, count), math.ceil(count / 2), count - 1}):
                if keep <= 0 or keep >= count:
                    continue
                keep_positions = set(positions[:keep])
                variant = deepcopy(layout)
                variant.parts = [
                    placement
                    for position, placement in enumerate(variant.parts)
                    if position in keep_positions
                ]
                wrapper = OptimizationResult(
                    job_type="sheet",
                    algorithm="Inteligentny wzorzec resztowy",
                    sheet_layouts=[variant],
                )
                annotate_guillotine_result(wrapper, kerf, min_reusable_size)
                if variant.is_guillotine_feasible:
                    register(variant)
    return patterns, len(result.sheet_layouts)


def _state_rank(state: _SearchState, part_areas: tuple[float, ...]) -> tuple[float, ...]:
    remaining_area = sum(count * area for count, area in zip(state.remaining, part_areas))
    current_waste = max(0.0, state.purchased_area - state.placed_area)
    # purchased + remaining is an admissible lower bound: it assumes the rest
    # can be packed with zero additional waste.  It keeps promising mixtures in
    # the beam without preferring a large board only because it places more now.
    optimistic_total_area = state.purchased_area + remaining_area
    return (
        optimistic_total_area,
        current_waste,
        state.cost,
        float(state.sheets),
        float(sum(state.remaining)),
        float(state.cut_count),
    )


def _complete_rank(state: _SearchState) -> tuple[float, ...]:
    return (
        state.purchased_area,
        state.cost,
        float(state.sheets),
        float(state.cut_count),
    )


def _choose_patterns(
    parts: list[SheetPart],
    patterns: list[_Pattern],
    maximum_depth: int,
) -> tuple[_SearchState | None, int]:
    demand = tuple(int(part.quantity) for part in parts)
    part_areas = tuple(float(part.width * part.height) for part in parts)
    initial = _SearchState(demand, (), 0.0, 0.0, 0.0, 0)
    frontier = [initial]
    best_complete: _SearchState | None = None
    examined = 0

    for _depth in range(maximum_depth):
        by_remaining: dict[tuple[int, ...], _SearchState] = {}
        for state in frontier:
            for index, pattern in enumerate(patterns):
                if any(take > left for take, left in zip(pattern.counts, state.remaining)):
                    continue
                remaining = tuple(left - take for left, take in zip(state.remaining, pattern.counts))
                candidate = _SearchState(
                    remaining=remaining,
                    chosen=state.chosen + (index,),
                    purchased_area=state.purchased_area + pattern.stock_area,
                    placed_area=state.placed_area + pattern.placed_area,
                    cost=state.cost + pattern.cost,
                    cut_count=state.cut_count + pattern.cut_count,
                )
                examined += 1
                if not any(remaining):
                    if best_complete is None or _complete_rank(candidate) < _complete_rank(best_complete):
                        best_complete = candidate
                    continue
                if best_complete is not None:
                    optimistic = candidate.purchased_area + sum(
                        count * area for count, area in zip(candidate.remaining, part_areas)
                    )
                    if optimistic > best_complete.purchased_area + _EPS:
                        continue
                previous = by_remaining.get(remaining)
                if previous is None or _state_rank(candidate, part_areas) < _state_rank(previous, part_areas):
                    by_remaining[remaining] = candidate
        if not by_remaining:
            break
        frontier = sorted(by_remaining.values(), key=lambda state: _state_rank(state, part_areas))[:_BEAM_WIDTH]
    return best_complete, examined


def optimize_smart_stock_mix(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float = 3.0,
    margin: float = 0.0,
    mode: str = "minimize_waste",
    min_reusable_size: float = 200.0,
    cutting_mode: str = "hybrid",
    optimization_mode: str = "comfort",
) -> OptimizationResult:
    """Return a legal global mix of available supplier sheet formats."""
    aggregated = _aggregate_parts(parts)
    candidates = _deduplicate_stock(stock)
    if not aggregated or not candidates:
        return OptimizationResult(
            job_type="sheet",
            algorithm="Inteligentny dobór formatów",
            unplaced_sheet_parts=aggregated,
            messages=["Tryb inteligentny nie otrzymał dostępnych formatów płyt."],
        )

    for part in aggregated:
        if not any(
            materials_are_compatible(item.material, part.material)
            and abs(item.thickness - part.thickness) < 0.001
            and _fits(part, item)
            for item in candidates
        ):
            return OptimizationResult(
                job_type="sheet",
                algorithm="Inteligentny dobór formatów",
                unplaced_sheet_parts=aggregated,
                messages=[
                    f"Brak formatu mieszczącego formatkę {part.width:g} × {part.height:g} mm "
                    f"({part.material}, gr. {part.thickness:g} mm)."
                ],
            )

    patterns: list[_Pattern] = []
    uniform_depths: list[int] = []
    for item in candidates:
        generated, depth = _patterns_for_format(
            item,
            aggregated,
            kerf,
            margin,
            mode,
            min_reusable_size,
            cutting_mode,
            optimization_mode,
        )
        patterns.extend(generated)
        if depth:
            uniform_depths.append(depth)

    if not patterns:
        return OptimizationResult(
            job_type="sheet",
            algorithm="Inteligentny dobór formatów",
            unplaced_sheet_parts=aggregated,
            messages=["Nie udało się zbudować legalnego wzorca dla dostępnych formatów."],
        )

    total_pieces = sum(part.quantity for part in aggregated)
    maximum_depth = min(total_pieces, max(uniform_depths, default=1) + len(aggregated) + 4)
    selected, examined = _choose_patterns(aggregated, patterns, maximum_depth)
    if selected is None:
        return OptimizationResult(
            job_type="sheet",
            algorithm="Inteligentny dobór formatów",
            unplaced_sheet_parts=aggregated,
            messages=[
                "Tryb inteligentny zbudował legalne rozkroje pojedynczych formatów, "
                "ale nie znalazł pełnej mieszanki w bezpiecznym budżecie wyszukiwania."
            ],
        )

    layouts: list[SheetLayout] = []
    for sheet_index, pattern_index in enumerate(selected.chosen, start=1):
        layout = deepcopy(patterns[pattern_index].layout)
        layout.sheet_index = sheet_index
        layout.stock = replace(layout.stock, quantity=1, stack_size=1, source="stock")
        layouts.append(layout)

    result = OptimizationResult(
        job_type="sheet",
        algorithm="Inteligentny dobór formatów",
        sheet_layouts=layouts,
        total_cost=sum(layout.stock.price for layout in layouts),
    )
    annotate_guillotine_result(result, kerf, min_reusable_size)
    annotate_result_metrics(result, kerf, min_reusable_size)
    sheet_area = sum(layout.stock.width * layout.stock.height for layout in layouts)
    used_area = sum(layout.used_area for layout in layouts)
    result.waste = max(0.0, sheet_area - used_area)
    result.utilization = used_area / sheet_area * 100.0 if sheet_area else 0.0

    format_counts: Counter[tuple[int, int]] = Counter(
        (round(layout.stock.width), round(layout.stock.height)) for layout in layouts
    )
    summary = ", ".join(
        f"{quantity} × {width} × {height} mm"
        for (width, height), quantity in sorted(format_counts.items())
    )
    result.messages.append(
        f"Inteligentny dobór formatów: {summary}. "
        f"Sprawdzono {len(patterns)} legalnych wzorców i {examined} kombinacji; "
        f"wykorzystanie {result.utilization:.1f}%."
    )
    return result

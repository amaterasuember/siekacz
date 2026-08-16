from __future__ import annotations

import functools
import math
import os
import random as _random
from copy import deepcopy
from collections import Counter
from dataclasses import dataclass, field, replace
from itertools import permutations

from algorithms.candidate_pruning import prune_dominated_candidates
from algorithms.guillotine_technology import annotate_guillotine_result, validate_guillotine_feasibility
from algorithms.layout_scoring import annotate_result_metrics, build_reusable_offcuts, collect_free_rectangles, manufacturing_score, rect_area, score_result
from algorithms.part_classification import (
    is_small_filler_part as _shared_is_small_filler_part,
    stock_profile_key as _shared_stock_profile_key,
    strategic_dimension as _shared_strategic_dimension,
)
from algorithms.vertical_candidate_parallel import build_strategy_candidates_parallel
from core.models import OptimizationResult, PlacedSheetPart, SheetLayout, SheetPart, SheetStock, materials_are_compatible

Rect = tuple[float, float, float, float]
EPS = 0.001


@dataclass
class _Segment:
    index: int
    x: float
    width: float
    height: float
    margin: float
    free_rects: list[Rect] = field(default_factory=list)
    parts: list[PlacedSheetPart] = field(default_factory=list)
    _used_height_cache: float | None = field(default=None, init=False, repr=False)

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def used_height(self) -> float:
        if self._used_height_cache is None:
            self._used_height_cache = max((part.y + part.height for part in self.parts), default=self.margin)
        return self._used_height_cache

    def record_placement(self, placement: PlacedSheetPart) -> None:
        self._used_height_cache = max(self.used_height, placement.y + placement.height)


@dataclass(frozen=True)
class _Strategy:
    name: str
    order: str
    anchor: str = "narrow"


@dataclass(frozen=True)
class _PartDifficulty:
    score: float
    is_filler: bool
    orientation_count: int
    potential_region_count: int
    position_count: int
    aspect_ratio: float
    strategic_closeness: float
    area_ratio: float


@dataclass(frozen=True)
class _ResidualRegion:
    layout: SheetLayout
    x: float
    y: float
    width: float
    height: float
    source: str
    guillotine: bool
    useful: bool
    fitting_part_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _UsedBoundingBox:
    min_x: float
    max_x: float
    min_y: float
    max_y: float

    @property
    def length_x(self) -> float:
        return max(0.0, self.max_x - self.min_x)

    @property
    def length_y(self) -> float:
        return max(0.0, self.max_y - self.min_y)


_DEFAULT_STRATEGY = _Strategy("area descending", "area_desc", "narrow")


@dataclass(frozen=True)
class _PartVariant:
    part: SheetPart
    width: float
    height: float
    rotated: bool


@dataclass
class _StripStack:
    width: float
    height: float = 0.0
    variants: list[_PartVariant] = field(default_factory=list)
    rows: list[list[tuple[_PartVariant, float]]] = field(default_factory=list)

    def can_add(self, variant: _PartVariant, stock_height: float, kerf: float) -> bool:
        next_height = self.height + (kerf if self.variants else 0.0) + variant.height
        return next_height <= stock_height + EPS

    def add(self, variant: _PartVariant, kerf: float) -> None:
        self.height += (kerf if self.variants else 0.0) + variant.height
        self.variants.append(variant)
        self.rows.append([(variant, 0.0)])

    def can_add_row(self, row_height: float, stock_height: float, kerf: float) -> bool:
        next_height = self.height + (kerf if self.variants else 0.0) + row_height
        return next_height <= stock_height + EPS

    def add_row(self, row: list[tuple[_PartVariant, float]], row_height: float, kerf: float) -> None:
        self.height += (kerf if self.variants else 0.0) + row_height
        self.variants.extend(variant for variant, _ in row)
        self.rows.append(row)


def _expand_parts(parts: list[SheetPart]) -> list[SheetPart]:
    expanded: list[SheetPart] = []
    for part in parts:
        for index in range(part.quantity):
            expanded.append(replace(part, quantity=1, label=part.label or f"{part.name}-{index + 1}"))
    return expanded


def _ordered_parts(
    parts: list[SheetPart],
    strategy: _Strategy,
    stock: SheetStock | None = None,
    kerf: float = 0.0,
    margin: float = 0.0,
    residual_regions: list[Rect] | None = None,
) -> list[SheetPart]:
    difficulty_cache = {
        id(part): _part_difficulty_memo(part, stock, kerf, margin, residual_regions)
        for part in parts
    }

    def base_key(part: SheetPart) -> tuple[object, ...]:
        difficulty = difficulty_cache[id(part)]
        if strategy.order == "difficulty_desc":
            return (
                -part.priority,
                1 if difficulty.is_filler else 0,
                -difficulty.score,
                difficulty.potential_region_count,
                difficulty.position_count,
                -(part.width * part.height),
                part.name,
            )
        if strategy.order == "constrained_desc":
            return (
                -part.priority,
                1 if difficulty.is_filler else 0,
                difficulty.orientation_count,
                difficulty.potential_region_count,
                difficulty.position_count,
                -difficulty.strategic_closeness,
                -difficulty.aspect_ratio,
                part.name,
            )
        if strategy.order == "long_thin_desc":
            return (
                -part.priority,
                1 if difficulty.is_filler else 0,
                -difficulty.aspect_ratio,
                -max(part.width, part.height),
                -(part.width * part.height),
                part.name,
            )
        if strategy.order == "strategic_desc":
            return (
                -part.priority,
                1 if difficulty.is_filler else 0,
                -difficulty.strategic_closeness,
                -difficulty.score,
                -max(part.width, part.height),
                part.name,
            )
        if strategy.order == "filler_last_area":
            return (-part.priority, 1 if difficulty.is_filler else 0, -(part.width * part.height), -difficulty.score, part.name)
        if strategy.order == "filler_last_longest":
            return (-part.priority, 1 if difficulty.is_filler else 0, -max(part.width, part.height), -(part.width * part.height), part.name)
        if strategy.order == "positions_asc":
            return (
                -part.priority,
                1 if difficulty.is_filler else 0,
                difficulty.position_count,
                difficulty.potential_region_count,
                -difficulty.score,
                part.name,
            )
        if strategy.order == "longest_desc":
            return (-part.priority, -max(part.width, part.height), -(part.width * part.height), part.name)
        if strategy.order == "width_desc":
            return (-part.priority, -part.width, -part.height, -(part.width * part.height), part.name)
        if strategy.order == "height_desc":
            return (-part.priority, -part.height, -part.width, -(part.width * part.height), part.name)
        if strategy.order == "similar_width":
            return (-part.priority, round(part.width, 1), part.height, -(part.width * part.height), part.name)
        if strategy.order == "similar_height":
            return (-part.priority, round(part.height, 1), part.width, -(part.width * part.height), part.name)
        if strategy.order == "width_group_area":
            return (-part.priority, round(part.width, 1), -(part.width * part.height), part.height, part.name)
        if strategy.order == "height_asc":
            return (-part.priority, part.height, part.width, -(part.width * part.height), part.name)
        return (-part.priority, -(part.width * part.height), -max(part.width, part.height), -min(part.width, part.height), part.name)

    return sorted(parts, key=base_key)


def _expand_stock(stock: list[SheetStock]) -> list[SheetStock]:
    expanded: list[SheetStock] = []
    for item in stock:
        for _ in range(item.quantity):
            expanded.append(replace(item, quantity=1))
    return sorted(
        expanded,
        key=lambda item: (
            -int(getattr(item, "priority", 0) or 0),
            item.material,
            item.thickness,
            item.width * item.height,
            item.price,
        ),
    )


def _canonicalize_stock_orientation(item: SheetStock) -> SheetStock:
    """Return a copy of the stock with the longer side as width.

    This makes the optimizer's view of stock deterministic regardless of which
    way the user typed the dimensions — `(2000, 1000)` and `(1000, 2000)`
    produce the same canonical stock when rotation is allowed and grain is free.
    Also synchronizes nominal_width/nominal_height so dataclass equality holds
    between canonicalized stocks from either input order.
    """
    preferred_axis = str(getattr(item, "preferred_cut_axis", "auto") or "auto").lower()

    # The production candidates in this optimizer are vertical strips.  When
    # the operator selects a board side, orient that physical side vertically
    # so the long rip cuts really run along it.  Swap the stored axis together
    # with the dimensions so it continues to identify the same physical side.
    if preferred_axis in {"x", "y"} and item.grain_direction == "none":
        if preferred_axis == "y":
            return replace(item)
        return replace(
            item,
            width=item.height,
            height=item.width,
            nominal_width=item.nominal_height or item.height,
            nominal_height=item.nominal_width or item.width,
            preferred_cut_axis="y",
        )

    if not item.allow_rotation or item.grain_direction != "none":
        return replace(item)
    if abs(item.width - item.height) <= EPS:
        return replace(item)
    if item.width >= item.height:
        # Already canonical; normalize nominal_* so it matches a swapped twin.
        return replace(
            item,
            nominal_width=item.nominal_width or item.width,
            nominal_height=item.nominal_height or item.height,
        )
    return replace(
        item,
        width=item.height,
        height=item.width,
        nominal_width=item.nominal_height or item.height,
        nominal_height=item.nominal_width or item.width,
    )


def _stock_orientation_sets(stock: list[SheetStock]) -> list[tuple[str, list[SheetStock]]]:
    canonical = [_canonicalize_stock_orientation(item) for item in stock]
    # A selected rip direction is an explicit production instruction, not a
    # scoring hint.  Do not add the 90-degree alternative that would put the
    # saw back along the other side.
    if any(str(getattr(item, "preferred_cut_axis", "auto") or "auto").lower() in {"x", "y"} for item in stock):
        return [("stock selected cut direction", canonical)]
    rotated: list[SheetStock] = []
    can_rotate_any = False
    for item in canonical:
        if item.allow_rotation and item.grain_direction == "none" and abs(item.width - item.height) > EPS:
            can_rotate_any = True
            rotated.append(
                replace(
                    item,
                    width=item.height,
                    height=item.width,
                    nominal_width=item.nominal_height or item.height,
                    nominal_height=item.nominal_width or item.width,
                    preferred_cut_axis=(
                        "y" if getattr(item, "preferred_cut_axis", "auto") == "x"
                        else "x" if getattr(item, "preferred_cut_axis", "auto") == "y"
                        else "auto"
                    ),
                )
            )
        else:
            rotated.append(replace(item))
    if can_rotate_any:
        return [("stock 0deg", canonical), ("stock 90deg", rotated)]
    return [("stock 0deg", canonical)]


@functools.lru_cache(maxsize=2048)
def _orientations_cached(
    pw: float, ph: float, allow_rotation: bool, grain: str | None, stock_allow_rotation: bool
) -> tuple[tuple[float, float, bool], ...]:
    options: list[tuple[float, float, bool]] = [(pw, ph, False)]
    if allow_rotation and stock_allow_rotation and grain == "none":
        options.append((ph, pw, True))
    unique: list[tuple[float, float, bool]] = []
    seen: set[tuple[float, float]] = set()
    for width, height, rotated in options:
        key = (round(width, 4), round(height, 4))
        if key not in seen:
            seen.add(key)
            unique.append((width, height, rotated))
    return tuple(unique)


def _orientations(part: SheetPart, stock: SheetStock) -> tuple[tuple[float, float, bool], ...]:
    return _orientations_cached(part.width, part.height, part.allow_rotation, part.grain_direction, stock.allow_rotation)


def _part_variants(part: SheetPart, stock: SheetStock) -> list[_PartVariant]:
    return [_PartVariant(part, width, height, rotated) for width, height, rotated in _orientations(part, stock)]


# Stock-profile and strategic-dimension helpers now live in
# algorithms.part_classification so the optimizer_worker can reuse the same
# rules without duplicating them.  These local aliases preserve all call-sites.
_stock_profile_key = _shared_stock_profile_key
_strategic_dimension = _shared_strategic_dimension


def _saved_axis(stock: SheetStock) -> str:
    preferred_cut_axis = str(getattr(stock, "preferred_cut_axis", "auto") or "auto").lower()
    if preferred_cut_axis == "x":
        return "y"
    if preferred_cut_axis == "y":
        return "x"
    strategic = _strategic_dimension(stock)
    width_is_strategic = abs(stock.width - strategic) <= max(1.0, strategic * 0.01)
    height_is_strategic = abs(stock.height - strategic) <= max(1.0, strategic * 0.01)
    if width_is_strategic and not height_is_strategic:
        return "y"
    if height_is_strategic and not width_is_strategic:
        return "x"
    return "x" if stock.width >= stock.height else "y"


def _used_bounding_box(layout: SheetLayout) -> _UsedBoundingBox:
    if not layout.parts:
        return _UsedBoundingBox(0.0, 0.0, 0.0, 0.0)
    return _UsedBoundingBox(
        min(part.x for part in layout.parts),
        max(part.x + part.width for part in layout.parts),
        min(part.y for part in layout.parts),
        max(part.y + part.height for part in layout.parts),
    )


def _saved_used_length(layout: SheetLayout) -> float:
    bbox = _used_bounding_box(layout)
    return bbox.length_x if _saved_axis(layout.stock) == "x" else bbox.length_y


def _saved_consumed_area(layout: SheetLayout) -> float:
    if not layout.parts:
        return 0.0
    length = _saved_used_length(layout)
    return length * (layout.stock.height if _saved_axis(layout.stock) == "x" else layout.stock.width)


# Canonical filler classification lives in algorithms.part_classification.
# This local alias preserves all in-module references.
_is_small_filler_part = _shared_is_small_filler_part


def _region_position_count(width: float, height: float, regions: list[Rect], kerf: float) -> int:
    count = 0
    for _, _, region_width, region_height in regions:
        if width > region_width + EPS or height > region_height + EPS:
            continue
        x_step = max(width + kerf, EPS)
        y_step = max(height + kerf, EPS)
        cols = max(1, int((region_width - width + kerf + EPS) // x_step) + 1)
        rows = max(1, int((region_height - height + kerf + EPS) // y_step) + 1)
        count += min(cols * rows, 10_000)
    return count


def _difficulty_regions(stock: SheetStock, margin: float, residual_regions: list[Rect] | None = None) -> list[Rect]:
    regions = [(margin, margin, max(0.0, stock.width - margin * 2), max(0.0, stock.height - margin * 2))]
    if residual_regions:
        regions.extend(residual_regions)
    return regions


def _part_difficulty(
    part: SheetPart,
    stock: SheetStock | None,
    kerf: float = 0.0,
    margin: float = 0.0,
    residual_regions: list[Rect] | None = None,
) -> _PartDifficulty:
    if stock is None:
        longest = max(part.width, part.height)
        shortest = max(min(part.width, part.height), EPS)
        return _PartDifficulty(
            score=float(part.priority) * 1000.0 + longest / shortest,
            is_filler=False,
            orientation_count=1,
            potential_region_count=1,
            position_count=1,
            aspect_ratio=longest / shortest,
            strategic_closeness=0.0,
            area_ratio=0.0,
        )

    regions = _difficulty_regions(stock, margin, residual_regions)
    variants = _part_variants(part, stock)
    fitting_variants = [
        variant
        for variant in variants
        if any(variant.width <= rw + EPS and variant.height <= rh + EPS for _, _, rw, rh in regions)
    ]
    orientation_count = max(1, len(fitting_variants))
    fitting_region_keys: set[tuple[int, int, int, int]] = set()
    position_count = 0
    strategic = max(_strategic_dimension(stock), EPS)
    strategic_closeness = 0.0
    for variant in fitting_variants:
        position_count += _region_position_count(variant.width, variant.height, regions, kerf)
        for x, y, width, height in regions:
            if variant.width <= width + EPS and variant.height <= height + EPS:
                fitting_region_keys.add((round(x * 1000), round(y * 1000), round(width * 1000), round(height * 1000)))
        for dimension in (variant.width, variant.height):
            strategic_closeness = max(strategic_closeness, 1.0 - min(1.0, abs(strategic - dimension) / strategic))

    longest = max(part.width, part.height)
    shortest = max(min(part.width, part.height), EPS)
    aspect_ratio = longest / shortest
    sheet_area = max(stock.width * stock.height, EPS)
    area_ratio = part.width * part.height / sheet_area
    potential_region_count = max(1, len(fitting_region_keys))
    is_filler = _is_small_filler_part(part, stock)
    scarcity = 1.0 / potential_region_count
    position_penalty = math.log(max(position_count, 1) + 1.0)

    score = (
        float(part.priority) * 1000.0
        + strategic_closeness * 240.0
        + min(aspect_ratio, 30.0) * 16.0
        + scarcity * 120.0
        + (2 - min(orientation_count, 2)) * 70.0
        + area_ratio * 85.0
        - position_penalty * 14.0
    )
    if is_filler:
        score -= 420.0
    return _PartDifficulty(
        score=score,
        is_filler=is_filler,
        orientation_count=orientation_count,
        potential_region_count=potential_region_count,
        position_count=position_count,
        aspect_ratio=aspect_ratio,
        strategic_closeness=strategic_closeness,
        area_ratio=area_ratio,
    )


_difficulty_cache: dict[tuple, _PartDifficulty] = {}
_DIFFICULTY_CACHE_MAX = 4096  # entries; evict oldest half when exceeded


def _difficulty_cache_key(
    part: SheetPart,
    stock: SheetStock | None,
    kerf: float,
    margin: float,
) -> tuple:
    stock_key = (
        round(stock.width, 1), round(stock.height, 1),
        bool(stock.allow_rotation), stock.grain_direction,
    ) if stock else None
    return (
        round(part.width, 1), round(part.height, 1),
        bool(part.allow_rotation), part.grain_direction, int(part.priority),
        stock_key, round(kerf, 2), round(margin, 2),
    )


def _part_difficulty_memo(
    part: SheetPart,
    stock: SheetStock | None,
    kerf: float = 0.0,
    margin: float = 0.0,
    residual_regions: list[Rect] | None = None,
) -> _PartDifficulty:
    if residual_regions is not None:
        return _part_difficulty(part, stock, kerf, margin, residual_regions)
    key = _difficulty_cache_key(part, stock, kerf, margin)
    cached = _difficulty_cache.get(key)
    if cached is None:
        cached = _part_difficulty(part, stock, kerf, margin, None)
        # Evict oldest half of entries when the cache grows too large so
        # long-running sessions don't accumulate unbounded memory.
        if len(_difficulty_cache) >= _DIFFICULTY_CACHE_MAX:
            evict_count = _DIFFICULTY_CACHE_MAX // 2
            for evict_key in list(_difficulty_cache)[:evict_count]:
                del _difficulty_cache[evict_key]
        _difficulty_cache[key] = cached
    return cached


def _is_small_filler_variant(variant: _PartVariant) -> bool:
    return max(variant.width, variant.height) <= 160.0 or variant.width * variant.height <= 20000.0


def _small_filler_variants(part: SheetPart, stock: SheetStock, max_width: float, max_height: float) -> list[_PartVariant]:
    variants = [
        variant
        for variant in _part_variants(part, stock)
        if _is_small_filler_variant(variant)
        and variant.width <= max_width + EPS
        and variant.height <= max_height + EPS
    ]
    return sorted(variants, key=lambda variant: (variant.height, -variant.width, variant.rotated, variant.part.name))


def _usable_height(stock: SheetStock, margin: float) -> float:
    return max(0.0, stock.height - margin * 2)


def _part_fits_stock(part: SheetPart, stock: SheetStock, margin: float) -> bool:
    usable_width = max(0.0, stock.width - margin * 2)
    usable_height = _usable_height(stock, margin)
    return any(width <= usable_width + EPS and height <= usable_height + EPS for width, height, _ in _orientations(part, stock))


def _anchor_orientation(
    part: SheetPart,
    stock: SheetStock,
    available_width: float,
    margin: float,
    strategy: _Strategy = _DEFAULT_STRATEGY,
) -> tuple[float, float, bool] | None:
    usable_height = _usable_height(stock, margin)
    options = [
        option
        for option in _orientations(part, stock)
        if option[0] <= available_width + EPS and option[1] <= usable_height + EPS
    ]
    if not options:
        return None
    if strategy.anchor == "original":
        return min(options, key=lambda option: (option[2], -option[0], option[1]))
    if strategy.anchor == "wide":
        return min(options, key=lambda option: (-option[0], option[1], option[2]))
    if strategy.anchor == "low":
        return min(options, key=lambda option: (option[1], option[0], option[2]))
    return min(options, key=lambda option: (option[0], -option[1], option[2]))


def _split_segment_rects(free_rects: list[Rect], rect_index: int, width: float, height: float, kerf: float) -> Rect:
    x, y, free_width, free_height = free_rects.pop(rect_index)
    right = (x + width + kerf, y, max(0.0, free_width - width - kerf), height)
    bottom = (x, y + height + kerf, free_width, max(0.0, free_height - height - kerf))
    for rect in (right, bottom):
        if rect[2] > EPS and rect[3] > EPS:
            free_rects.append(rect)
    free_rects.sort(key=lambda rect: (rect[1], rect[0], rect[2] * rect[3]))
    return x, y, free_width, free_height


def _candidate_score(segment: _Segment, rect: Rect, width: float, height: float, part: SheetPart) -> tuple[float, ...]:
    fx, fy, free_width, free_height = rect
    new_height = max(segment.used_height, fy + height)
    local_waste = free_width * free_height - width * height
    compact_part = max(part.width, part.height) <= 80.0 or part.width * part.height <= 3600.0

    if compact_part:
        return (segment.x, fy, fx, new_height, local_waste, width)
    return (new_height, segment.x, fy, fx, local_waste, width)


def _try_place_in_segment(
    layout: SheetLayout,
    segment: _Segment,
    part: SheetPart,
    kerf: float,
    strategy: _Strategy = _DEFAULT_STRATEGY,
) -> bool:
    best: tuple[tuple[float, ...], int, float, float, bool] | None = None
    for rect_index, rect in enumerate(segment.free_rects):
        _, _, free_width, free_height = rect
        for width, height, rotated in _orientations(part, layout.stock):
            if width <= free_width + EPS and height <= free_height + EPS:
                score = _candidate_score(segment, rect, width, height, part)
                if best is None or score < best[0]:
                    best = (score, rect_index, width, height, rotated)

    if best is None:
        return False

    _, rect_index, width, height, rotated = best
    local_x, y, _, _ = _split_segment_rects(segment.free_rects, rect_index, width, height, kerf)
    placement = PlacedSheetPart(
        part=part,
        x=segment.x + local_x,
        y=y,
        width=width,
        height=height,
        rotated=rotated,
    )
    segment.parts.append(placement)
    segment.record_placement(placement)
    layout.parts.append(placement)
    return True


def _create_segment(
    layout: SheetLayout,
    segments: list[_Segment],
    part: SheetPart,
    kerf: float,
    margin: float,
    strategy: _Strategy = _DEFAULT_STRATEGY,
) -> _Segment | None:
    next_x = margin if not segments else max(segment.right for segment in segments) + kerf
    available_width = layout.stock.width - margin - next_x
    orientation = _anchor_orientation(part, layout.stock, available_width, margin, strategy)
    if orientation is None:
        return None

    width, _, _ = orientation
    segment = _Segment(
        index=len(segments) + 1,
        x=next_x,
        width=width,
        height=_usable_height(layout.stock, margin),
        margin=margin,
        free_rects=[(0.0, margin, width, _usable_height(layout.stock, margin))],
    )
    segments.append(segment)
    if not _try_place_in_segment(layout, segment, part, kerf, strategy):
        segments.pop()
        return None
    return segment


def _matching_layout(layout: SheetLayout, part: SheetPart) -> bool:
    return materials_are_compatible(layout.stock.material, part.material) and abs(layout.stock.thickness - part.thickness) < EPS


def _place_existing(
    layouts: list[SheetLayout],
    segments_by_layout: dict[int, list[_Segment]],
    part: SheetPart,
    kerf: float,
    strategy: _Strategy = _DEFAULT_STRATEGY,
) -> bool:
    best: tuple[tuple[float, ...], SheetLayout, _Segment] | None = None
    for layout in layouts:
        if not _matching_layout(layout, part):
            continue
        for segment in segments_by_layout[id(layout)]:
            for rect in segment.free_rects:
                free_width = rect[2]
                free_height = rect[3]
                for width, height, _ in _orientations(part, layout.stock):
                    if width <= free_width + EPS and height <= free_height + EPS:
                        score = _candidate_score(segment, rect, width, height, part)
                        layout_score = (layout.sheet_index, *score)
                        if best is None or layout_score < best[0]:
                            best = (layout_score, layout, segment)
    if best is None:
        return False
    _, layout, segment = best
    return _try_place_in_segment(layout, segment, part, kerf, strategy)


def _place_new_segment(
    layouts: list[SheetLayout],
    segments_by_layout: dict[int, list[_Segment]],
    part: SheetPart,
    kerf: float,
    margin: float,
    strategy: _Strategy = _DEFAULT_STRATEGY,
) -> bool:
    best: tuple[tuple[float, ...], SheetLayout] | None = None
    for layout in layouts:
        if not _matching_layout(layout, part):
            continue
        segments = segments_by_layout[id(layout)]
        next_x = margin if not segments else max(segment.right for segment in segments) + kerf
        available_width = layout.stock.width - margin - next_x
        orientation = _anchor_orientation(part, layout.stock, available_width, margin, strategy)
        if orientation is None:
            continue
        width, height, _ = orientation
        score = (layout.sheet_index, next_x + width, width, -height)
        if best is None or score < best[0]:
            best = (score, layout)
    if best is None:
        return False
    _, layout = best
    return _create_segment(layout, segments_by_layout[id(layout)], part, kerf, margin, strategy) is not None


def _open_new_sheet(
    layouts: list[SheetLayout],
    segments_by_layout: dict[int, list[_Segment]],
    available_stock: list[SheetStock],
    part: SheetPart,
    kerf: float,
    margin: float,
    strategy: _Strategy = _DEFAULT_STRATEGY,
) -> bool:
    stock_index = next(
        (
            index
            for index, stock in enumerate(available_stock)
            if materials_are_compatible(stock.material, part.material) and abs(stock.thickness - part.thickness) < EPS and _part_fits_stock(part, stock, margin)
        ),
        None,
    )
    if stock_index is None:
        return False

    selected = available_stock.pop(stock_index)
    layout = SheetLayout(stock=selected, sheet_index=len(layouts) + 1)
    layouts.append(layout)
    segments_by_layout[id(layout)] = []
    return _create_segment(layout, segments_by_layout[id(layout)], part, kerf, margin, strategy) is not None


def _rebuild_segment(layout: SheetLayout, segment: _Segment, kerf: float) -> bool:
    original_parts = [placement.part for placement in segment.parts]
    rebuilt = _Segment(
        index=segment.index,
        x=segment.x,
        width=segment.width,
        height=segment.height,
        margin=segment.margin,
        free_rects=[(0.0, segment.margin, segment.width, segment.height)],
    )
    temp_layout = SheetLayout(stock=layout.stock, sheet_index=layout.sheet_index)
    for part in sorted(original_parts, key=lambda item: (-max(item.width, item.height), -(item.width * item.height), item.name)):
        if not _try_place_in_segment(temp_layout, rebuilt, part, kerf):
            return False
    segment.free_rects = rebuilt.free_rects
    segment.parts = rebuilt.parts
    return True


def _compress_layout(layout: SheetLayout, segments: list[_Segment], kerf: float) -> None:
    rebuilt_parts: list[PlacedSheetPart] = []
    for segment in segments:
        if _rebuild_segment(layout, segment, kerf):
            rebuilt_parts.extend(segment.parts)
        else:
            # Compression is a visual/compactness improvement only.  A failed
            # repack must never erase an already valid segment from the result.
            # Keep its original placements so the quantity invariant remains
            # intact and any parts that do not fit still stay explicitly
            # represented by the caller's unplaced list.
            rebuilt_parts.extend(segment.parts)
    if rebuilt_parts:
        layout.parts = sorted(rebuilt_parts, key=lambda placement: (placement.x, placement.y, -placement.width * placement.height))


def _segment_offcuts(layout: SheetLayout, segments: list[_Segment], kerf: float) -> list[Rect]:
    offcuts: list[Rect] = []
    if not layout.parts:
        return offcuts

    right_x = min(layout.stock.width, layout.used_width + kerf)
    if right_x < layout.stock.width - EPS:
        offcuts.append((right_x, 0.0, layout.stock.width - right_x, layout.stock.height))

    for segment in segments:
        if not segment.parts:
            continue
        bottom_y = min(layout.stock.height, max(part.y + part.height for part in segment.parts) + kerf)
        if bottom_y < layout.stock.height - EPS:
            offcuts.append((segment.x, bottom_y, segment.width, layout.stock.height - bottom_y))
    return offcuts


def _layout_segment_bounds(layout: SheetLayout) -> list[tuple[int, float, float, float]]:
    bounds: list[tuple[int, float, float, float]] = []
    for index, segment in enumerate(layout.vertical_segments, start=1):
        try:
            x = float(segment.get("x", 0.0))
            width = float(segment.get("width", 0.0))
            right = float(segment.get("right", x + width))
            segment_index = int(segment.get("index", index))
        except (AttributeError, TypeError, ValueError):
            continue
        if width > EPS and right > x + EPS:
            bounds.append((segment_index, x, width, right))
    return sorted(bounds, key=lambda item: item[1])


def _region_fit_variants(part: SheetPart, stock: SheetStock, region: Rect) -> list[_PartVariant]:
    _, _, region_width, region_height = region
    return [
        variant
        for variant in _part_variants(part, stock)
        if variant.width <= region_width + EPS and variant.height <= region_height + EPS
    ]


def _region_class(layout: SheetLayout, rect: Rect, bbox: _UsedBoundingBox) -> int:
    x, y, width, height = rect
    right = x + width
    bottom = y + height
    inside_bbox = (
        x >= bbox.min_x - EPS
        and y >= bbox.min_y - EPS
        and right <= bbox.max_x + EPS
        and bottom <= bbox.max_y + EPS
    )
    if inside_bbox:
        return 1
    if _saved_axis(layout.stock) == "x":
        return 2 if right <= bbox.max_x + EPS else 3
    return 2 if bottom <= bbox.max_y + EPS else 3


def _collect_internal_filler_regions(layout: SheetLayout, baseline: _UsedBoundingBox, kerf: float) -> list[tuple[int, Rect, str]]:
    regions: list[tuple[int, Rect, str]] = []
    if not layout.parts:
        return regions

    segment_bottoms: list[tuple[float, float, float]] = []
    for segment_index, segment_x, _segment_width, segment_right in _layout_segment_bounds(layout):
        segment_parts = [
            part
            for part in layout.parts
            if part.x + EPS >= segment_x and part.x + part.width <= segment_right + EPS
        ]
        rows: dict[tuple[int, int], list[PlacedSheetPart]] = {}
        for part in segment_parts:
            rows.setdefault((round(part.y * 1000), round(part.height * 1000)), []).append(part)
        for row_parts in rows.values():
            row_parts.sort(key=lambda part: part.x)
            y = row_parts[0].y
            height = row_parts[0].height
            row_used_right = max(part.x + part.width for part in row_parts)
            tail_x = row_used_right + kerf
            tail_width = segment_right - tail_x
            if tail_width > EPS and height > EPS:
                rect = (tail_x, y, tail_width, height)
                region_class = _region_class(layout, rect, baseline)
                if region_class <= 2:
                    regions.append((region_class, rect, f"row tail in segment {segment_index}"))

        bottom = max((part.y + part.height for part in segment_parts), default=0.0)
        bottom_y = bottom + kerf
        bottom_height = layout.stock.height - bottom_y
        if segment_parts and bottom_height > EPS:
            segment_bottoms.append((segment_x, segment_right, bottom))
        if bottom_height > EPS:
            rect = (segment_x, bottom_y, segment_right - segment_x, bottom_height)
            region_class = _region_class(layout, rect, baseline)
            if region_class <= 2:
                regions.append((region_class, rect, f"bottom tail in segment {segment_index}"))

    # Adjacent strips with the same occupied height form one useful bottom band.
    # Treating every narrow strip as isolated makes 40x900-style columns look
    # like unusable 40 mm leftovers, even when a legal crosscut can create a
    # wider residual rectangle below the whole block.
    segment_bottoms.sort(key=lambda item: item[0])
    group: list[tuple[float, float, float]] = []
    for item in segment_bottoms:
        if not group:
            group = [item]
            continue
        previous = group[-1]
        adjacent = item[0] <= previous[1] + kerf + EPS
        same_bottom = abs(item[2] - previous[2]) <= EPS
        if adjacent and same_bottom:
            group.append(item)
        else:
            if len(group) >= 2:
                left = group[0][0]
                right = group[-1][1]
                bottom_y = group[0][2] + kerf
                height = layout.stock.height - bottom_y
                rect = (left, bottom_y, right - left, height)
                region_class = _region_class(layout, rect, baseline)
                if height > EPS and region_class <= 2:
                    regions.append((region_class, rect, "bottom band across adjacent strips"))
            group = [item]
    if len(group) >= 2:
        left = group[0][0]
        right = group[-1][1]
        bottom_y = group[0][2] + kerf
        height = layout.stock.height - bottom_y
        rect = (left, bottom_y, right - left, height)
        region_class = _region_class(layout, rect, baseline)
        if height > EPS and region_class <= 2:
            regions.append((region_class, rect, "bottom band across adjacent strips"))

    # Remove individual "bottom tail in segment N" regions that are fully covered by
    # a "bottom band across adjacent strips" region — prevents double-placement where
    # the same physical space is offered as both a wide band and individual strip tails.
    band_rects = [rect for _, rect, src in regions if src == "bottom band across adjacent strips"]
    if band_rects:
        regions = [
            (cls, rect, src) for cls, rect, src in regions
            if not (
                src.startswith("bottom tail in segment")
                and any(
                    rect[0] >= br[0] - EPS and rect[0] + rect[2] <= br[0] + br[2] + EPS
                    and rect[1] >= br[1] - EPS and rect[1] + rect[3] <= br[1] + br[3] + EPS
                    for br in band_rects
                )
            )
        ]

    # Sort priority:
    #   1. class (class-1 inside-bbox before class-2, class-2 before class-3)
    #   2. source type: "bottom band across adjacent strips" (0) before individual
    #      "bottom tail in segment N" (1) and "row tail in segment N" (1).
    #      This prevents tall strip-interior tails from being promoted above the
    #      proper bottom band in subsequent scans when fillers extend bbox.max_y.
    #   3. high y-start first — consolidate fillers at the bottom of the stock
    #   4. largest area within the same y-band
    #   5. leftmost x
    def _region_sort_key(item: tuple[int, Rect, str]) -> tuple:
        cls, rect, src = item
        not_band = 0 if src == "bottom band across adjacent strips" else 1
        return (cls, not_band, -rect[1], -rect[2] * rect[3], rect[0])

    regions.sort(key=_region_sort_key)
    return regions


def _try_place_filler_in_rect(
    layout: SheetLayout,
    part: SheetPart,
    rect: Rect,
    kerf: float,
) -> PlacedSheetPart | None:
    x, y, width, height = rect
    variants = _small_filler_variants(part, layout.stock, width, height)
    if not variants:
        return None
    variant = variants[0]
    placement = PlacedSheetPart(part, x, y, variant.width, variant.height, variant.rotated)
    layout.parts.append(placement)
    return placement


def _recover_internal_fillers(
    layout: SheetLayout,
    remaining: list[SheetPart],
    kerf: float,
    margin: float,
    messages: list[str] | None = None,
    max_scans: int = 10,
    filler_only: bool = True,
    max_region_class: int = 2,
) -> list[SheetPart]:
    """Multi-pass waste-first refill.

    After each scan that successfully placed at least one filler, the regions
    are re-collected because new gaps may have opened (e.g. a placed filler
    extended the bounding box, promoting a class-3 region to class-1/2) or
    free rectangles inside existing regions have changed shape. The loop ends
    when a full pass adds nothing, or when ``max_scans`` is reached.

    When *filler_only* is False, all remaining parts are eligible for placement
    in class 1/2 waste regions. This prevents medium-sized non-filler parts
    from being stranded on individual sheets when they fit in existing waste.
    """
    if not remaining or not layout.parts:
        return remaining

    not_placed = remaining[:]

    for scan_index in range(max_scans):
        if not not_placed:
            break

        baseline = _used_bounding_box(layout)
        region_segments: list[tuple[int, str, _Segment]] = []
        for index, (region_class, rect, source) in enumerate(
            _collect_internal_filler_regions(layout, baseline, kerf), start=1
        ):
            x, y, width, height = rect
            region_segments.append(
                (
                    region_class,
                    source,
                    _Segment(
                        index=10_000 + scan_index * 1000 + index,
                        x=x,
                        width=width,
                        height=height,
                        margin=margin,
                        free_rects=[(0.0, y, width, height)],
                    ),
                )
            )
        if not region_segments:
            break

        strategy = _Strategy(f"internal filler pass {scan_index + 1}", "difficulty_desc", "narrow")
        placed_in_scan = 0
        placed_ids: set[int] = set()
        for part in not_placed:
            difficulty = _part_difficulty_memo(part, layout.stock, kerf, margin)
            if filler_only and not difficulty.is_filler:
                continue
            part_oris = _orientations(part, layout.stock)
            eligible_segments = [
                (rc, src, seg) for rc, src, seg in region_segments
                if rc <= max_region_class
            ] if not filler_only else region_segments
            fit_options = [
                (region_class, source, segment)
                for region_class, source, segment in eligible_segments
                if any(
                    width <= rect[2] + EPS and height <= rect[3] + EPS
                    for rect in segment.free_rects
                    for width, height, _ in part_oris
                )
            ]
            if not fit_options:
                if messages is not None and scan_index == 0:
                    messages.append(
                        f"filler placement rejected part={part.name} "
                        f"reason=no class 1/2 region alternatives=0"
                    )
                continue
            # Multiple recovery segments can describe overlapping physical
            # rectangles (e.g. a "bottom band across adjacent strips" that
            # overlaps a per-strip residual).  `_try_place_in_segment` only
            # checks its own segment's free_rects, so a previously placed
            # filler in a different segment may occupy the same coordinates.
            # Try fit_options in order, validate each placement against every
            # other placed part, undo and try the next option on collision.
            placement = None
            region_class = source = segment = None
            before_length = _saved_used_length(layout)
            for option_class, option_source, option_segment in fit_options:
                before_count = len(layout.parts)
                if not _try_place_in_segment(layout, option_segment, part, kerf, strategy):
                    continue
                trial = layout.parts[-1]
                if len(layout.parts) == before_count:
                    continue
                collided = False
                for other in layout.parts[:-1]:
                    if (trial.x < other.x + other.width - EPS
                            and other.x < trial.x + trial.width - EPS
                            and trial.y < other.y + other.height - EPS
                            and other.y < trial.y + trial.height - EPS):
                        collided = True
                        break
                if collided:
                    # Roll back: physical conflict with a part placed earlier
                    # in this scan (in a different, overlapping region).
                    layout.parts.pop()
                    if option_segment.parts and option_segment.parts[-1] is trial:
                        option_segment.parts.pop()
                    continue
                placement = trial
                region_class, source, segment = option_class, option_source, option_segment
                break

            if placement is None:
                # No region accepted this part without collision.
                continue
            after_length = _saved_used_length(layout)
            extension = max(0.0, after_length - before_length)
            if messages is not None:
                messages.append(
                    f"filler placement scan={scan_index + 1} part={part.name} "
                    f"filler={difficulty.is_filler} class={region_class} source={source} "
                    f"x={placement.x:.2f} y={placement.y:.2f} "
                    f"increasedUsedLength={extension > EPS} extension={extension:.2f} "
                    f"alternatives={len(fit_options)}"
                )
            placed_ids.add(id(part))
            placed_in_scan += 1

        if placed_ids:
            not_placed = [item for item in not_placed if id(item) not in placed_ids]

        if placed_in_scan == 0:
            if messages is not None and scan_index > 0:
                messages.append(f"filler refill converged after {scan_index} scans")
            break

    return not_placed


def _build_right_residual_region(
    layout: SheetLayout,
    remaining: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
) -> _ResidualRegion | None:
    if not layout.parts:
        return None
    segments = _layout_segment_bounds(layout)
    used_right = max((right for _, _, _, right in segments), default=layout.used_width)
    if used_right <= EPS:
        return None
    x = used_right + kerf
    width = layout.stock.width - margin - x
    y = margin
    height = layout.stock.height - margin * 2
    if width <= EPS or height <= EPS:
        return None
    rect = (x, y, width, height)
    fitting = sorted({part.name for part in remaining if _region_fit_variants(part, layout.stock, rect)})
    return _ResidualRegion(
        layout=layout,
        x=x,
        y=y,
        width=width,
        height=height,
        source="right residual strip",
        guillotine=True,
        useful=bool(fitting) or (width >= min_reusable_size - EPS and height >= min_reusable_size - EPS),
        fitting_part_names=tuple(fitting),
    )


def _ordered_recovery_parts(
    remaining: list[SheetPart],
    stock: SheetStock,
    region: _ResidualRegion,
    kerf: float,
    margin: float,
) -> list[SheetPart]:
    rect = (region.x, region.y, region.width, region.height)
    candidates = [part for part in remaining if _region_fit_variants(part, stock, rect)]

    def _recovery_sort_key(part: SheetPart) -> tuple:
        d = _part_difficulty(part, stock, kerf, margin, [rect])
        return (1 if d.is_filler else 0, -d.score, -max(part.width, part.height), -(part.width * part.height), part.name)

    return sorted(candidates, key=_recovery_sort_key)


def _best_recovery_strip_width(part: SheetPart, stock: SheetStock, region: _ResidualRegion) -> float | None:
    variants = _region_fit_variants(part, stock, (region.x, region.y, region.width, region.height))
    if not variants:
        return None
    return min(
        variants,
        key=lambda variant: (
            variant.width,
            -variant.height,
            variant.rotated,
            -variant.width * variant.height,
        ),
    ).width


def _recover_layout_right_strip(
    layout: SheetLayout,
    remaining: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    messages: list[str] | None = None,
) -> list[SheetPart]:
    if not remaining:
        return remaining

    not_placed = remaining[:]
    while not_placed:
        region = _build_right_residual_region(layout, not_placed, kerf, margin, min_reusable_size)
        if region is None:
            break
        if messages is not None:
            fits = ", ".join(region.fitting_part_names) or "none"
            messages.append(
                "residualRegion "
                f"sheet={layout.sheet_index} source={region.source} "
                f"x={region.x:.2f} y={region.y:.2f} w={region.width:.2f} h={region.height:.2f} "
                f"useful={region.useful} guillotine={region.guillotine} fits={fits}"
            )
        recovery_order = _ordered_recovery_parts(not_placed, layout.stock, region, kerf, margin)
        if not recovery_order:
            break
        strip_width = _best_recovery_strip_width(recovery_order[0], layout.stock, region)
        if strip_width is None or strip_width <= EPS:
            break

        segment = _Segment(
            index=max((index for index, *_ in _layout_segment_bounds(layout)), default=0) + 1,
            x=region.x,
            width=strip_width,
            height=region.height,
            margin=margin,
            free_rects=[(0.0, region.y, strip_width, region.height)],
        )
        placed_ids: set[int] = set()
        strategy = _Strategy("waste recovery pass", "difficulty_desc", "narrow")
        for part in recovery_order:
            if id(part) in placed_ids:
                continue
            if _try_place_in_segment(layout, segment, part, kerf, strategy):
                placed_ids.add(id(part))
        if not placed_ids:
            break

        layout.vertical_segments.append(
            {
                "index": segment.index,
                "x": round(segment.x, 3),
                "width": round(segment.width, 3),
                "right": round(segment.right, 3),
                "source": "waste-recovery",
            }
        )
        if messages is not None:
            placed_names = Counter(part.name for part in not_placed if id(part) in placed_ids)
            messages.append(
                f"waste recovery sheet={layout.sheet_index} strip={segment.width:.2f} "
                + ", ".join(f"{name}x{count}" for name, count in sorted(placed_names.items()))
            )
        not_placed = [part for part in not_placed if id(part) not in placed_ids]

    return not_placed


_SPARSE_SHEET_THRESHOLD = 3


_COMPACT_MIN_GAP_MM = 20.0  # only close gaps clearly larger than ~3× kerf


def _compact_column_gaps(layout: SheetLayout, kerf: float) -> bool:
    """Shift parts upward to close clearly-avoidable vertical gaps.

    For each part, the minimum legal y is determined by the highest *other*
    part that overlaps its x-range.  If a part sits below that floor by MORE
    THAN ``_COMPACT_MIN_GAP_MM`` (in addition to kerf), it is pulled up.
    Small "intentional" gaps (e.g., parts deliberately placed against the
    bottom of the sheet as a residual band) are left alone — the threshold
    only fires for clear strip-builder artefacts like the 50-100 mm gap a
    mixed-orientation column leaves between a single tall part and the rest.

    Returns True if any part was moved.  The caller should re-run guillotine
    annotation afterwards because positions changed.
    """
    if not layout.parts:
        return False

    changed = False
    parts_in_order = sorted(layout.parts, key=lambda p: (p.y, p.x))
    for p in parts_in_order:
        p_left = p.x
        p_right = p.x + p.width
        floor = 0.0
        for other in layout.parts:
            if other is p:
                continue
            if other.x + other.width <= p_left + EPS:
                continue
            if other.x >= p_right - EPS:
                continue
            if other.y + other.height <= p.y + EPS:
                candidate_floor = other.y + other.height + kerf
                if candidate_floor > floor:
                    floor = candidate_floor
        # Only close clearly excessive gaps; preserve intentional bottom-band
        # placements (where the original gap is small / by design).
        if p.y - floor > _COMPACT_MIN_GAP_MM + EPS:
            p.y = floor
            changed = True

    return changed


def _consolidate_sparse_sheets(
    result: OptimizationResult,
    kerf: float,
    margin: float,
    min_reusable_size: float = 80.0,
    max_part_count: int = _SPARSE_SHEET_THRESHOLD,
    try_right_strip: bool = False,
    debug: bool = False,
) -> OptimizationResult:
    """Move parts from under-utilised sheets into waste regions of denser ones.

    Sheets with fewer than *max_part_count* parts are candidates for elimination.
    When *try_right_strip* is True the consolidation also attempts to place parts
    in the right-side residual region (class 3), which lets non-filler parts move
    to the right waste strip of existing sheets — critical for eliminating sheets
    that carry only structural parts that would otherwise never enter a class-1/2
    region.
    """
    if len(result.sheet_layouts) < 2:
        return result

    if not any(0 < len(lay.parts) < max_part_count for lay in result.sheet_layouts):
        return result

    layouts = list(result.sheet_layouts)
    changed = True
    messages: list[str] | None = result.messages if debug else None

    while changed:
        changed = False
        layouts.sort(key=lambda lay: len(lay.parts))
        for sparse_idx, sparse_layout in enumerate(layouts):
            if len(sparse_layout.parts) >= max_part_count:
                break
            if len(sparse_layout.parts) == 0:
                continue

            donor_parts = [p.part for p in sparse_layout.parts]
            target_layouts = [lay for idx, lay in enumerate(layouts) if idx != sparse_idx and lay.parts]
            if not target_layouts:
                continue

            test_targets = [
                SheetLayout(
                    stock=lay.stock,
                    sheet_index=lay.sheet_index,
                    parts=list(lay.parts),
                    vertical_segments=deepcopy(lay.vertical_segments),
                )
                for lay in target_layouts
            ]

            remaining = list(donor_parts)
            for target in test_targets:
                if not remaining:
                    break
                remaining = _recover_internal_fillers(
                    target, remaining, kerf, margin,
                    filler_only=False, max_region_class=2,
                )
                if remaining and try_right_strip:
                    remaining = _recover_layout_right_strip(
                        target, remaining, kerf, margin, min_reusable_size,
                    )

            if remaining:
                continue

            if messages is not None:
                names = ", ".join(p.name for p in donor_parts)
                messages.append(
                    f"consolidation: eliminated sheet {sparse_layout.sheet_index} "
                    f"({len(donor_parts)} parts: {names}) by moving to denser sheets"
                )

            for target, original in zip(test_targets, target_layouts):
                original.parts = target.parts
                original.vertical_segments = target.vertical_segments
            layouts.pop(sparse_idx)
            changed = True
            break

    if len(layouts) < len(result.sheet_layouts):
        for new_idx, layout in enumerate(layouts, start=1):
            layout.sheet_index = new_idx
        result.sheet_layouts = layouts
        used_area = sum(lay.used_area for lay in layouts)
        total_area = sum(lay.consumed_area for lay in layouts)
        result.waste = max(0.0, total_area - used_area)
        result.utilization = used_area / total_area * 100.0 if total_area else 0.0
    return result


def _recover_candidate_waste(
    result: OptimizationResult,
    kerf: float,
    margin: float,
    min_reusable_size: float,
    debug: bool = False,
) -> OptimizationResult:
    remaining = result.unplaced_sheet_parts
    if not remaining:
        return result
    messages: list[str] | None = result.messages if debug else None
    # Pass 1: place fillers in internal waste regions (original behavior)
    for layout in result.sheet_layouts:
        if not remaining:
            break
        remaining = _recover_internal_fillers(layout, remaining, kerf, margin, messages)
        if not remaining:
            break
        remaining = _recover_layout_right_strip(layout, remaining, kerf, margin, min_reusable_size, messages)
    # Pass 2: place ALL remaining parts (including non-fillers) in internal
    # waste regions of class 1/2.  This prevents medium-sized parts from being
    # stranded on individual sheets when they physically fit in existing waste.
    if remaining:
        for layout in result.sheet_layouts:
            if not remaining:
                break
            remaining = _recover_internal_fillers(
                layout, remaining, kerf, margin, messages,
                filler_only=False, max_region_class=2,
            )
    result.unplaced_sheet_parts = remaining
    used_area = sum(layout.used_area for layout in result.sheet_layouts)
    total_area = sum(layout.consumed_area for layout in result.sheet_layouts)
    result.waste = max(0.0, total_area - used_area)
    result.utilization = used_area / total_area * 100.0 if total_area else 0.0
    return result


def _prune_empty_segment_metadata(layout: SheetLayout) -> None:
    kept: list[dict[str, object]] = []
    for segment in getattr(layout, "vertical_segments", []):
        try:
            left = float(segment.get("x", 0.0))
            right = float(segment.get("right", left + float(segment.get("width", 0.0))))
        except (AttributeError, TypeError, ValueError):
            continue
        if any(part.x + EPS >= left and part.x + part.width <= right + EPS for part in layout.parts):
            kept.append(segment)
    layout.vertical_segments = kept


def _set_single_segment_if_valid(layout: SheetLayout, kerf: float, margin: float) -> bool:
    if not layout.parts:
        return False
    original_segments = deepcopy(layout.vertical_segments)
    left = max(0.0, min(part.x for part in layout.parts) - margin)
    right = min(layout.stock.width, max(part.x + part.width for part in layout.parts))
    layout.vertical_segments = [
        {
            "index": 1,
            "x": round(left, 3),
            "width": round(right - left, 3),
            "right": round(right, 3),
            "source": "global-compaction",
        }
    ]
    valid, _errors = validate_guillotine_feasibility(layout, kerf)
    if valid:
        return True
    layout.vertical_segments = original_segments
    return False


def _tiny_filler_y_span(layout: SheetLayout) -> float:
    """Vertical span of medium-tiny filler placements; lower = more consolidated.

    Measures fillers in the area band [TINY_AREA_MIN, TINY_AREA_MAX):
      - Includes B(20×30=600mm²): these should go to the shared bottom waste band.
      - Excludes A(10×10=100mm²) and similar micro-fillers: they are so small that
        they naturally scatter into whatever gap is left; measuring them would mask
        the improvement from consolidating B.
      - Excludes C(100×100=10000mm²) and larger fillers that organise into own strips.

    When B is scattered through strip tails (y=210..1000, span=790) the score is
    large; after the repair pass puts B in the bottom band (y=905..1000, span=95)
    the score drops, allowing `_repair_layout_score` to accept the repair.
    """
    TINY_AREA_MIN = 300.0   # mm²: exclude micro-fillers (A: 100mm²)
    TINY_AREA_MAX = 5000.0  # mm²: exclude C-sized fillers (C: 10000mm²)
    placements = [
        p for p in layout.parts
        if _is_small_filler_part(p.part, layout.stock)
        and TINY_AREA_MIN <= p.part.width * p.part.height < TINY_AREA_MAX
    ]
    if not placements:
        return 0.0
    return max(p.y + p.height for p in placements) - min(p.y for p in placements)


def _repair_layout_score(layout: SheetLayout) -> tuple[float, ...]:
    return (
        _saved_used_length(layout),
        _saved_consumed_area(layout),
        layout.used_width * layout.used_height,
        _tiny_filler_y_span(layout),   # lower = tiny fillers consolidated in waste band
        layout.fragmentation_score,
        -layout.largest_reusable_offcut_area,
        -layout.reusable_offcut_area,
    )


def _repair_layout_fillers_waste_first(
    layout: SheetLayout,
    kerf: float,
    margin: float,
    min_reusable_size: float,
    messages: list[str] | None = None,
) -> SheetLayout | None:
    # Choose how many fillers to extract based on the bottom-band height.
    #
    # When the band below the non-filler parts is tall enough to hold the
    # largest filler dimension, every filler can be extracted and relocated there.
    # Example: A(40×900) on 3000×1500 leaves a 595 mm band → C(100 mm) fits → extract all.
    #
    # When the band is short (e.g. D(40×900) on 2000×1000 leaves only 95 mm →
    # C(100 mm) does NOT fit), extracting C would lead to rejection because neither
    # the band nor the right residual strip can absorb C.  In that case extract only
    # the truly tiny fillers (area < _REPAIR_TINY_AREA) that DO fit in the band.
    has_non_filler = any(
        not _is_small_filler_part(p.part, layout.stock) for p in layout.parts
    )
    if not has_non_filler:
        return None
    non_filler_bottom = max(
        p.y + p.height for p in layout.parts
        if not _is_small_filler_part(p.part, layout.stock)
    )
    expected_band_height = layout.stock.height - (non_filler_bottom + kerf)
    all_filler_max_dim = max(
        (max(p.part.width, p.part.height) for p in layout.parts
         if _is_small_filler_part(p.part, layout.stock)),
        default=0.0,
    )
    # If all fillers fit in the bottom band, extract them all; otherwise extract only
    # truly tiny ones so the repair doesn't get rejected for unplaceable large fillers.
    extract_all = expected_band_height >= all_filler_max_dim - EPS
    _REPAIR_TINY_AREA = float("inf") if extract_all else 5000.0

    def _is_repair_filler(part: SheetPart) -> bool:
        return _is_small_filler_part(part, layout.stock) and part.width * part.height < _REPAIR_TINY_AREA

    filler_parts = [
        placement.part
        for placement in layout.parts
        if _is_repair_filler(placement.part)
    ]
    if not filler_parts:
        return None
    # Process larger fillers first so they fill the proper bottom bands before
    # micro-fillers claim spots; without this, 10×10 parts interleaved with 20×30
    # parts disrupt the row packing and push 20×30 parts into tall strip tails.
    filler_parts.sort(key=lambda p: p.width * p.height, reverse=True)

    # "before" score uses the already-annotated original layout (annotate_result_metrics
    # is called by _build_result, so fragmentation_score / offcut metrics are populated)
    before_score = _repair_layout_score(layout)
    before_largest_offcut = layout.largest_reusable_offcut_area

    repaired = deepcopy(layout)
    repaired.parts = [
        placement
        for placement in repaired.parts
        if not _is_repair_filler(placement.part)
    ]
    _prune_empty_segment_metadata(repaired)
    if not repaired.parts:
        return None

    # First attempt: class 1/2 regions only (do NOT extend the saved axis).
    remaining = _recover_internal_fillers(repaired, filler_parts, kerf, margin, messages)
    # T2-4: If fillers remain after the class-1/2 pass, retry with class-3
    # (right strip) enabled.  Class 3 extends the saved axis, but the final
    # score check (after_score < before_score) still gates acceptance, so a
    # class-3 placement that actually worsens the layout is rejected anyway.
    # This unblocks scenarios where fillers can ride along in an already-
    # opened right strip without forcing a separate strip elsewhere.
    if remaining:
        remaining_before_class3 = list(remaining)
        remaining = _recover_internal_fillers(
            repaired, remaining, kerf, margin, messages,
            max_region_class=3,
        )
        if messages is not None and len(remaining) < len(remaining_before_class3):
            placed_in_class3 = len(remaining_before_class3) - len(remaining)
            messages.append(
                f"repair pass class-3 fallback sheet={layout.sheet_index} "
                f"placed_extra={placed_in_class3}"
            )
    if remaining:
        # Allow *partial* repair: if the majority of fillers were placed in waste
        # areas and the layout is likely shorter, keep the repaired layout and let
        # the unplaced remainder flow to a subsequent optimisation pass (e.g. the
        # missing-sheet pass in the worker).  This fixes cases where e.g. 952 B
        # (10×10) parts can almost all fit under C(400×550) but 19 overflow.
        placed_count = len(filler_parts) - len(remaining)
        placed_ratio = placed_count / max(1, len(filler_parts))
        if placed_ratio < 0.50:
            if messages is not None:
                messages.append(
                    f"repair pass rejected sheet={layout.sheet_index} "
                    f"reason=unfilled fillers count={len(remaining)}/{len(filler_parts)} placed_ratio={placed_ratio:.2f}"
                )
            return None
        # >= 50 % placed: continue with the partial repair.  Remaining parts are
        # stored on the layout so _repair_candidate_fillers can move them to
        # result.unplaced_sheet_parts after the score check.
        if messages is not None:
            messages.append(
                f"repair pass partial sheet={layout.sheet_index} "
                f"placed={placed_count}/{len(filler_parts)} remaining={len(remaining)}"
            )
        repaired._remaining_repair_fillers = remaining  # type: ignore[attr-defined]

    _set_single_segment_if_valid(repaired, kerf, margin)
    after_result = OptimizationResult(job_type="sheet", algorithm="repair-after", sheet_layouts=[repaired])
    annotate_result_metrics(after_result, kerf, min_reusable_size)
    annotate_guillotine_result(after_result, kerf, min_reusable_size)
    after_layout = after_result.sheet_layouts[0]
    if not after_layout.is_guillotine_feasible:
        if messages is not None:
            messages.append(
                "repair pass rejected "
                f"sheet={layout.sheet_index} reason=guillotine {after_layout.technology_warning}"
            )
        return None

    after_score = _repair_layout_score(after_layout)
    if after_score >= before_score:
        if messages is not None:
            messages.append(
                f"repair pass rejected sheet={layout.sheet_index} reason=not dominated before={before_score} after={after_score}"
            )
        return None

    if messages is not None:
        messages.append(
            "repair pass improved "
            f"sheet={layout.sheet_index} savedLength {before_score[0]:.2f}->{after_score[0]:.2f} "
            f"largestOffcut {before_largest_offcut:.0f}->{after_layout.largest_reusable_offcut_area:.0f}"
        )
    return after_layout


def _repair_candidate_fillers(
    result: OptimizationResult,
    kerf: float,
    margin: float,
    min_reusable_size: float,
    debug: bool = False,
) -> OptimizationResult:
    messages: list[str] | None = result.messages if debug else None
    repaired_any = False
    repaired_layouts: list[SheetLayout] = []
    extra_unplaced: list[SheetPart] = []  # fillers that couldn't fit in waste (partial repair)
    for layout in result.sheet_layouts:
        repaired = _repair_layout_fillers_waste_first(layout, kerf, margin, min_reusable_size, messages)
        if repaired is None:
            repaired_layouts.append(layout)
            continue
        repaired.sheet_index = layout.sheet_index
        repaired_layouts.append(repaired)
        repaired_any = True
        # Collect remaining fillers from partial repair (if any) so they are
        # promoted to unplaced_sheet_parts and can flow to missing-sheet passes.
        leftover: list[SheetPart] = getattr(repaired, "_remaining_repair_fillers", [])
        extra_unplaced.extend(leftover)
        if hasattr(repaired, "_remaining_repair_fillers"):
            del repaired._remaining_repair_fillers  # type: ignore[attr-defined]

    if not repaired_any:
        return result

    result.sheet_layouts = repaired_layouts
    if extra_unplaced:
        result.unplaced_sheet_parts = list(result.unplaced_sheet_parts) + extra_unplaced
    used_area = sum(layout.used_area for layout in result.sheet_layouts)
    total_area = sum(layout.consumed_area for layout in result.sheet_layouts)
    result.waste = max(0.0, total_area - used_area)
    result.utilization = used_area / total_area * 100.0 if total_area else 0.0
    return result


def _attach_segment_metadata(layout: SheetLayout, segments: list[_Segment]) -> None:
    layout.vertical_segments = [
        {
            "index": segment.index,
            "x": round(segment.x, 3),
            "width": round(segment.width, 3),
            "right": round(segment.right, 3),
        }
        for segment in segments
    ]


def _boundary_crossings(layout: SheetLayout, segments: list[_Segment]) -> list[str]:
    boundaries = sorted({round(segment.x, 4) for segment in segments} | {round(segment.right, 4) for segment in segments})
    crossings: list[str] = []
    for placement in layout.parts:
        left = placement.x
        right = placement.x + placement.width
        for boundary in boundaries:
            if left + EPS < boundary < right - EPS:
                crossings.append(
                    f"sheet={layout.sheet_index} part={placement.part.name} x={left:.2f}-{right:.2f} boundary={boundary:.2f}"
                )
    return crossings


def _segment_part_summary(segment: _Segment) -> str:
    counts: Counter[str] = Counter()
    for placement in segment.parts:
        rotation = "R" if placement.rotated else ""
        counts[f"{placement.width:.0f}x{placement.height:.0f}{rotation}"] += 1
    return ", ".join(f"{key} x{count}" for key, count in sorted(counts.items())) or "pusty"


def _debug_messages(layouts: list[SheetLayout], segments_by_layout: dict[int, list[_Segment]]) -> list[str]:
    messages = ["Vertical segmented debug:"]
    for layout in layouts:
        segments = segments_by_layout.get(id(layout), [])
        crossings = _boundary_crossings(layout, segments)
        for segment in segments:
            messages.append(
                f"Płyta {layout.sheet_index}, segment {segment.index}: "
                f"x={segment.x:.2f}, szerokość={segment.width:.2f}, formatki: {_segment_part_summary(segment)}"
            )
        messages.append(f"Płyta {layout.sheet_index}: przecięcia granic segmentów: {'TAK' if crossings else 'NIE'}")
        messages.extend(f"BŁĄD segmentu: {item}" for item in crossings)
    return messages


def _best_single_variant(part: SheetPart, stock: SheetStock, prefer_last_sheet: bool = False) -> _PartVariant | None:
    variants = [
        variant
        for variant in _part_variants(part, stock)
        if variant.width <= stock.width + EPS and variant.height <= stock.height + EPS
    ]
    if not variants:
        return None

    def score(variant: _PartVariant) -> tuple[float, float, float]:
        temp = SheetLayout(stock=stock, sheet_index=1)
        temp.parts.append(PlacedSheetPart(variant.part, 0.0, 0.0, variant.width, variant.height, variant.rotated))
        free_rects = collect_free_rectangles(temp, 0.0)
        largest = max((rect_area(rect) for rect in free_rects), default=0.0)
        if prefer_last_sheet:
            return (-largest, variant.width * variant.height, variant.width)
        return (variant.width, -variant.height, variant.width * variant.height)

    return min(variants, key=score)


def _layout_part_counter(layout: SheetLayout) -> Counter[str]:
    return Counter(placement.part.name for placement in layout.parts)


def _result_part_counts(result: OptimizationResult) -> list[Counter[str]]:
    return [_layout_part_counter(layout) for layout in result.sheet_layouts]


def _placed_part_count(result: OptimizationResult) -> int:
    return sum(len(layout.parts) for layout in result.sheet_layouts)


def _total_saved_used_length(result: OptimizationResult) -> float:
    return sum(_saved_used_length(layout) for layout in result.sheet_layouts)


def _count_unplaced_non_fillers(result: OptimizationResult) -> int:
    """Count unplaced parts that are NOT small fillers (structural parts).

    Fillers are classified using the first stock sheet's parameters.  When no
    sheet layouts are present (all parts unplaced), everything is treated as a
    non-filler.

    Used by the scorer to distinguish between:
      • structural parts left on the table (serious → 1e12 penalty each), and
      • filler parts deliberately deferred to missing-sheet waste bands (moderate
        → 1e9 penalty each).  When removing the filler-dedicated strip on the
        stock sheet frees enough width for one more structural strip, the lower
        filler penalty lets such "non-filler-first" candidates win.
    """
    if not result.unplaced_sheet_parts:
        return 0
    ref_stock = result.sheet_layouts[0].stock if result.sheet_layouts else None
    if ref_stock is None:
        return len(result.unplaced_sheet_parts)
    return sum(
        1 for p in result.unplaced_sheet_parts
        if not _is_small_filler_part(p, ref_stock)
    )


def _total_saved_consumed_area(result: OptimizationResult) -> float:
    return sum(_saved_consumed_area(layout) for layout in result.sheet_layouts)


def _long_axis_tiebreak(result: OptimizationResult) -> tuple[int, float, float]:
    """Prefer the display/work orientation with the longer stock axis on X.

    This is intentionally a late tie-breaker. It must never beat a candidate
    that places more parts or uses fewer sheets; it only makes physically
    equivalent stock orientations deterministic.
    """
    non_horizontal = sum(1 for layout in result.sheet_layouts if layout.stock.width + EPS < layout.stock.height)
    total_width = sum(layout.stock.width for layout in result.sheet_layouts)
    total_height = sum(layout.stock.height for layout in result.sheet_layouts)
    return (non_horizontal, -total_width, total_height)


def _candidate_debug_line(name: str, result: OptimizationResult, kerf: float, min_reusable_size: float) -> str:
    score = score_result(result, kerf, min_reusable_size)
    sheet_chunks: list[str] = []
    for layout in result.sheet_layouts:
        parts = ", ".join(f"{part}x{count}" for part, count in sorted(_layout_part_counter(layout).items())) or "empty"
        sheet_chunks.append(
            f"S{layout.sheet_index}[stock={layout.stock.width:.0f}x{layout.stock.height:.0f}; {parts}; "
            f"largest={layout.largest_reusable_offcut_area:.0f}; "
            f"usedLength={_saved_used_length(layout):.0f}; "
            f"bbox={layout.used_width * layout.used_height:.0f}; manuf={layout.manufacturing_score:.0f}]"
        )
    return f"Candidate {name}: sheets={len(result.sheet_layouts)} score={score.value} " + " | ".join(sheet_chunks)


def _winner_geometry_debug_lines(result: OptimizationResult, kerf: float) -> list[str]:
    lines: list[str] = [f"Winner geometry: kerf={kerf:.2f}"]
    for layout in result.sheet_layouts:
        bbox = _used_bounding_box(layout)
        lines.append(
            f"Winner sheet {layout.sheet_index}: "
            f"usedBoundingBox=(minX={bbox.min_x:.2f}, maxX={bbox.max_x:.2f}, "
            f"minY={bbox.min_y:.2f}, maxY={bbox.max_y:.2f}) "
            f"usedLengthX={bbox.length_x:.2f} usedLengthY={bbox.length_y:.2f} "
            f"savedAxis={_saved_axis(layout.stock)}"
        )

        by_name: dict[str, Counter[str]] = {}
        filler_bounds: dict[str, list[float]] = {}
        for placement in layout.parts:
            by_name.setdefault(placement.part.name, Counter())[
                f"{placement.width:.0f}x{placement.height:.0f}"
            ] += 1
            if _is_small_filler_part(placement.part, layout.stock):
                bounds = filler_bounds.setdefault(
                    placement.part.name,
                    [placement.x, placement.x + placement.width, placement.y, placement.y + placement.height],
                )
                bounds[0] = min(bounds[0], placement.x)
                bounds[1] = max(bounds[1], placement.x + placement.width)
                bounds[2] = min(bounds[2], placement.y)
                bounds[3] = max(bounds[3], placement.y + placement.height)

        for name, orientations in sorted(by_name.items()):
            rendered = ", ".join(f"{dims}={count}" for dims, count in sorted(orientations.items()))
            lines.append(f"Winner orientations {name}: {rendered}")
        for name, bounds in sorted(filler_bounds.items()):
            lines.append(
                f"Winner filler {name}: x={bounds[0]:.2f}..{bounds[1]:.2f} "
                f"y={bounds[2]:.2f}..{bounds[3]:.2f}"
            )
    return lines


def _rejected_candidate_debug_lines(
    candidates: list[OptimizationResult],
    best: OptimizationResult,
    kerf: float,
    min_reusable_size: float,
) -> list[str]:
    winner_length = _total_saved_used_length(best)
    lines: list[str] = ["Rejected candidate debug:"]
    for index, candidate in enumerate(candidates, start=1):
        if candidate is best:
            continue
        reasons: list[str] = []
        if candidate.unplaced_sheet_parts:
            reasons.append(f"unplaced={len(candidate.unplaced_sheet_parts)}")
        infeasible = [
            layout
            for layout in candidate.sheet_layouts
            if layout.parts and not getattr(layout, "is_guillotine_feasible", False)
        ]
        if infeasible:
            warning = getattr(infeasible[0], "technology_warning", "") or "guillotine validation failed"
            reasons.append(f"guillotine={warning}")
        candidate_length = _total_saved_used_length(candidate)
        if not reasons:
            if candidate_length > winner_length + EPS:
                reasons.append(f"longer saved length {candidate_length:.2f}>{winner_length:.2f}")
            else:
                reasons.append("lexicographic scoring dominated by winner")
        score = score_result(candidate, kerf, min_reusable_size).value
        lines.append(f"Candidate {index}/{candidate.algorithm}: {'; '.join(reasons)}; score={score}")
    return lines


def _part_difficulty_debug_lines(stock: list[SheetStock], parts: list[SheetPart], kerf: float, margin: float) -> list[str]:
    expanded_stock = _expand_stock(stock)
    representative = expanded_stock[0] if expanded_stock else (stock[0] if stock else None)
    if representative is None:
        return []
    rows: list[tuple[float, str]] = []
    for part in parts:
        difficulty = _part_difficulty(part, representative, kerf, margin)
        rows.append(
            (
                -difficulty.score,
                "difficulty "
                f"{part.name or f'{part.width:.0f}x{part.height:.0f}'} "
                f"{part.width:.0f}x{part.height:.0f} qty={part.quantity} "
                f"score={difficulty.score:.2f} filler={difficulty.is_filler} "
                f"orientations={difficulty.orientation_count} regions={difficulty.potential_region_count} "
                f"positions={difficulty.position_count} aspect={difficulty.aspect_ratio:.2f} "
                f"strategic={_strategic_dimension(representative):.0f}",
            )
        )
    return ["Part difficulty order:", *(line for _, line in sorted(rows, key=lambda item: item[0]))]


def _sport_score(result: OptimizationResult, kerf: float, min_reusable_size: float) -> tuple[float, ...]:
    annotate_result_metrics(result, kerf, min_reusable_size)
    layouts = result.sheet_layouts
    infeasible = sum(1 for layout in layouts if layout.parts and not getattr(layout, "is_guillotine_feasible", False))
    sheet_area = sum(layout.stock.width * layout.stock.height for layout in layouts)
    used_area = sum(layout.used_area for layout in layouts)
    waste_area = max(0.0, sheet_area - used_area)
    cut_count = sum(getattr(layout, "cut_count", 0) for layout in layouts)
    strip_count = sum(getattr(layout, "strip_count", 0) for layout in layouts)
    reusable = sum(getattr(layout, "reusable_offcut_area", 0.0) for layout in layouts)
    largest = max((getattr(layout, "largest_reusable_offcut_area", 0.0) for layout in layouts), default=0.0)
    compactness = sum(layout.used_width * layout.used_height for layout in layouts)
    same_size_bonus = 0
    for layout in layouts:
        counts = Counter((round(part.width, 3), round(part.height, 3)) for part in layout.parts)
        same_size_bonus += sum(count * count for count in counts.values() if count > 1)
    placed_count = _placed_part_count(result)
    # Split unplaced penalty: structural parts left on the table (huge penalty) vs
    # filler parts deferred to missing-sheet waste bands (moderate penalty).
    _nf_unplaced = _count_unplaced_non_fillers(result)
    _filler_unplaced = len(result.unplaced_sheet_parts) - _nf_unplaced
    _mixed_orientation_penalty = _mixed_orientation_group_penalty(result)
    return (
        _nf_unplaced * 100_000_000_000.0 + _filler_unplaced * 100_000_000.0,
        len(layouts) * 1_000_000.0,
        infeasible * 100_000_000.0,
        _constraining_rotation_count(result) * 20_000_000.0,
        # Material saved along the long axis is a production priority.
        # Mixed orientations remain legal and are only a readability
        # tie-breaker once board count and consumed material are comparable.
        _total_saved_used_length(result) * 5_000_000.0,
        _total_saved_consumed_area(result) * 250.0,
        _mixed_orientation_penalty * 500_000_000.0,
        -placed_count * 250_000.0,
        -_residual_recovery_rotation_count(result) * 20_000_000.0,
        waste_area * 10.0,
        cut_count * 2.0,
        strip_count * 1.0,
        compactness * 0.002,
        -reusable * 3.0,
        -largest * 8.0,
        -same_size_bonus * 2.0,
    )


def _comfort_secondary_rotation_count(result: OptimizationResult) -> int:
    group_area: Counter[str] = Counter()
    for layout in result.sheet_layouts:
        for placement in layout.parts:
            group_area[placement.part.name] += placement.part.width * placement.part.height
    if len(group_area) <= 1:
        return 0

    largest_group_area = max(group_area.values(), default=0.0)
    count = 0
    for layout in result.sheet_layouts:
        for placement in layout.parts:
            if placement.rotated and group_area[placement.part.name] < largest_group_area - EPS:
                count += 1
    return count


def _constraining_rotation_count(result: OptimizationResult) -> int:
    grouped: dict[tuple[object, ...], list[PlacedSheetPart]] = {}
    for layout in result.sheet_layouts:
        strategic = max(_strategic_dimension(layout.stock), EPS)
        for placement in layout.parts:
            longest = max(placement.part.width, placement.part.height)
            shortest = max(min(placement.part.width, placement.part.height), EPS)
            if longest < strategic * 0.80:
                continue
            key = (
                placement.part.name,
                round(placement.part.width, 4),
                round(placement.part.height, 4),
                placement.part.material,
                round(placement.part.thickness, 4),
            )
            grouped.setdefault(key, []).append(placement)

    excessive = 0
    for placements in grouped.values():
        rotated = sum(1 for placement in placements if placement.rotated)
        tolerated_recovery = max(2, math.ceil(len(placements) * 0.50))
        excessive += max(0, rotated - tolerated_recovery)
    return excessive


def _residual_recovery_rotation_count(result: OptimizationResult) -> int:
    grouped: dict[tuple[object, ...], list[PlacedSheetPart]] = {}
    for layout in result.sheet_layouts:
        strategic = max(_strategic_dimension(layout.stock), EPS)
        for placement in layout.parts:
            if not placement.rotated:
                continue
            longest = max(placement.part.width, placement.part.height)
            shortest = max(min(placement.part.width, placement.part.height), EPS)
            if longest < strategic * 0.80:
                continue
            key = (
                placement.part.name,
                round(placement.part.width, 4),
                round(placement.part.height, 4),
                placement.part.material,
                round(placement.part.thickness, 4),
            )
            grouped.setdefault(key, []).append(placement)

    recovered = 0
    for placements in grouped.values():
        total = sum(
            1
            for layout in result.sheet_layouts
            for placement in layout.parts
            if (
                placement.part.name,
                round(placement.part.width, 4),
                round(placement.part.height, 4),
                placement.part.material,
                round(placement.part.thickness, 4),
            )
            == (
                placements[0].part.name,
                round(placements[0].part.width, 4),
                round(placements[0].part.height, 4),
                placements[0].part.material,
                round(placements[0].part.thickness, 4),
            )
        )
        rotated = len(placements)
        if total >= 6 and rotated <= max(2, math.ceil(total * 0.20)):
            recovered += rotated
    return recovered


def _mixed_orientation_group_penalty(result: OptimizationResult) -> int:
    penalty = 0
    for layout in result.sheet_layouts:
        grouped: dict[tuple[object, ...], list[PlacedSheetPart]] = {}
        for placement in layout.parts:
            key = (
                placement.part.name,
                round(placement.part.width, 4),
                round(placement.part.height, 4),
                placement.part.material,
                round(placement.part.thickness, 4),
            )
            grouped.setdefault(key, []).append(placement)
        for placements in grouped.values():
            orientations = {
                (round(placement.width, 4), round(placement.height, 4))
                for placement in placements
            }
            if len(orientations) > 1:
                penalty += (len(orientations) - 1) * len(placements)
    return penalty


def _total_strip_count(result: OptimizationResult) -> int:
    return sum(getattr(layout, "strip_count", 0) for layout in result.sheet_layouts)


def _comfort_score(result: OptimizationResult, kerf: float, min_reusable_size: float) -> tuple[float, ...]:
    base = score_result(result, kerf, min_reusable_size).value
    infeasible = sum(
        1
        for layout in result.sheet_layouts
        if layout.parts and getattr(layout, "is_guillotine_feasible", False) is False
    )
    secondary_rotations = _comfort_secondary_rotation_count(result)
    recovered_rotations = _residual_recovery_rotation_count(result)
    # Split unplaced penalty: structural parts left on the table (huge penalty) vs
    # filler parts deferred to missing-sheet waste bands (moderate penalty).
    _nf_unplaced = _count_unplaced_non_fillers(result)
    _filler_unplaced = len(result.unplaced_sheet_parts) - _nf_unplaced
    orientation_penalty = _long_axis_tiebreak(result)[0]
    mixed_orientation_penalty = _mixed_orientation_group_penalty(result)
    strip_count = _total_strip_count(result)
    return (
        infeasible * 900_000_000_000_000.0,
        _nf_unplaced * 1_000_000_000_000.0 + _filler_unplaced * 1_000_000_000.0,
        len(result.sheet_layouts) * 1_000_000_000.0,
        _total_saved_used_length(result) * 5_000_000.0,
        _total_saved_consumed_area(result) * 250.0,
        orientation_penalty * 75_000_000.0,
        # A mixed orientation is fully legal. Prefer a shorter consumed board
        # first; only use this as a late readability tie-breaker.
        mixed_orientation_penalty * 50_000_000.0,
        strip_count * 100_000.0,
        -recovered_rotations * 120_000_000.0,
        secondary_rotations * 20_000_000.0,
        -_placed_part_count(result) * 500_000.0,
        *base[4:],
    )


def _pack_group_into_strips(
    variants: list[_PartVariant],
    strip_width: float,
    max_strips: int,
    stock_height: float,
    kerf: float,
) -> list[_StripStack] | None:
    ordered = sorted(variants, key=lambda variant: (-variant.height, -variant.width, variant.part.name))

    def try_pack(strip_count: int, compact_remainder: bool) -> list[_StripStack] | None:
        stacks = [_StripStack(strip_width) for _ in range(strip_count)]
        for variant in ordered:
            candidates = [stack for stack in stacks if stack.can_add(variant, stock_height, kerf)]
            if not candidates:
                return None
            if compact_remainder:
                stack = max(candidates, key=lambda item: (item.height, len(item.variants)))
            else:
                stack = min(candidates, key=lambda item: (item.height, len(item.variants)))
            stack.add(variant, kerf)
        return [stack for stack in stacks if stack.variants]

    for strip_count in range(1, max_strips + 1):
        packed = try_pack(strip_count, compact_remainder=True)
        if packed is not None:
            return packed
        packed = try_pack(strip_count, compact_remainder=False)
        if packed is not None:
            return packed
    return None


def _pack_filler_rows_into_stacks(
    stacks: list[_StripStack],
    remaining: list[SheetPart],
    stock: SheetStock,
    stock_height: float,
    kerf: float,
) -> tuple[set[int], list[str]]:
    placed_ids: set[int] = set()
    messages: list[str] = []
    if not remaining:
        return placed_ids, messages

    for stack in sorted(stacks, key=lambda item: (-item.width, item.height)):
        while True:
            row_space_height = stock_height - stack.height - (kerf if stack.variants else 0.0)
            if row_space_height <= EPS:
                break
            candidate_variants: list[_PartVariant] = []
            for part in remaining:
                if id(part) in placed_ids:
                    continue
                variants = _small_filler_variants(part, stock, stack.width, row_space_height)
                if variants:
                    candidate_variants.append(variants[0])
            if not candidate_variants:
                break

            height_groups: dict[float, list[_PartVariant]] = {}
            for variant in candidate_variants:
                height_groups.setdefault(round(variant.height, 4), []).append(variant)
            best_row: list[tuple[_PartVariant, float]] = []
            best_height = 0.0
            best_score: tuple[float, float, float] | None = None
            for row_height, variants in height_groups.items():
                cursor = 0.0
                row: list[tuple[_PartVariant, float]] = []
                for variant in sorted(variants, key=lambda item: (-item.width, item.part.name)):
                    next_right = cursor + variant.width
                    if next_right <= stack.width + EPS:
                        row.append((variant, cursor))
                        cursor = next_right + kerf
                if not row:
                    continue
                row_area = sum(variant.width * variant.height for variant, _ in row)
                row_width = max(offset + variant.width for variant, offset in row)
                score = (row_area, len(row), row_width)
                if best_score is None or score > best_score:
                    best_score = score
                    best_row = row
                    best_height = float(row_height)

            if not best_row or not stack.can_add_row(best_height, stock_height, kerf):
                break
            stack.add_row(best_row, best_height, kerf)
            for variant, _ in best_row:
                placed_ids.add(id(variant.part))
            messages.append(
                f"tail fill in {stack.width:.0f} strip: "
                + "+".join(f"{variant.width:.0f}x{variant.height:.0f}" for variant, _ in best_row)
            )

    return placed_ids, messages


def _stack_signature(stack: _StripStack) -> tuple[tuple[float, float], ...]:
    return tuple((round(variant.width, 3), round(variant.height, 3)) for variant in stack.variants)


def _order_variants_for_height_axis_cut(stacks: list[_StripStack], stock_height: float) -> None:
    height_counts = Counter(round(variant.height, 3) for stack in stacks for variant in stack.variants)
    if not height_counts:
        return
    primary_height, primary_count = height_counts.most_common(1)[0]
    if primary_count < 2 or primary_height < min(600.0, stock_height * 0.25):
        return
    for stack in stacks:
        stack.variants.sort(
            key=lambda variant: (
                0 if abs(round(variant.height, 3) - primary_height) < EPS else 1,
                variant.height,
                variant.part.name,
            )
        )


def _ordered_stacks_for_manufacturing(stacks: list[_StripStack]) -> list[_StripStack]:
    def stack_key(stack: _StripStack) -> tuple[float, int, tuple[tuple[float, float], ...]]:
        signature = _stack_signature(stack)
        repeated = len(set(signature)) == 1
        if repeated:
            return (0, -len(stack.variants), -stack.height, signature)
        return (1, stack.height, 0, signature)

    return sorted(stacks, key=stack_key)


def _stack_order_variants(stacks: list[_StripStack]) -> list[list[_StripStack]]:
    if len(stacks) <= 1:
        return [stacks]
    if len(stacks) <= 4:
        raw_orders = permutations(stacks)
    else:
        ordered = _ordered_stacks_for_manufacturing(stacks)
        raw_orders = (tuple(stacks), tuple(ordered), tuple(reversed(ordered)))

    variants: list[list[_StripStack]] = []
    seen: set[tuple[tuple[tuple[float, float], ...], ...]] = set()
    for order in raw_orders:
        key = tuple(_stack_signature(stack) for stack in order)
        if key in seen:
            continue
        seen.add(key)
        variants.append(list(order))
    return variants


def _best_stack_order_for_manufacturing(
    stock: SheetStock,
    stacks: list[_StripStack],
    kerf: float,
    margin: float,
) -> list[_StripStack]:
    _order_variants_for_height_axis_cut(stacks, stock.height - margin * 2)
    uniform_dimensions = {
        (round(variant.width, 3), round(variant.height, 3))
        for stack in stacks
        for variant in stack.variants
    }
    if len(uniform_dimensions) == 1:
        return _ordered_stacks_for_manufacturing(stacks)
    best_order = _ordered_stacks_for_manufacturing(stacks)
    best_score = manufacturing_score(_draw_strip_sheet(stock, 1, best_order, kerf, margin))
    for order in _stack_order_variants(stacks):
        score = manufacturing_score(_draw_strip_sheet(stock, 1, order, kerf, margin))
        if score > best_score + EPS:
            best_score = score
            best_order = order
    return best_order


def _draw_strip_sheet(
    stock: SheetStock,
    sheet_index: int,
    stacks: list[_StripStack],
    kerf: float,
    margin: float,
) -> SheetLayout:
    layout = SheetLayout(stock=stock, sheet_index=sheet_index)
    x = margin
    for index, stack in enumerate(stacks, start=1):
        y = margin
        segment_parts: list[PlacedSheetPart] = []
        rows = stack.rows or [[(variant, 0.0)] for variant in stack.variants]
        for row in rows:
            row_height = max((variant.height for variant, _ in row), default=0.0)
            for variant, offset_x in row:
                placement = PlacedSheetPart(
                    part=variant.part,
                    x=x + offset_x,
                    y=y,
                    width=variant.width,
                    height=variant.height,
                    rotated=variant.rotated,
                )
                segment_parts.append(placement)
                layout.parts.append(placement)
            y += row_height + kerf
        layout.vertical_segments.append(
            {
                "index": index,
                "x": round(x, 3),
                "width": round(stack.width, 3),
                "right": round(x + stack.width, 3),
            }
        )
        x += stack.width + kerf
    return layout


def _candidate_width_groups(parts: list[SheetPart], stock: SheetStock) -> dict[float, list[_PartVariant]]:
    groups: dict[float, list[_PartVariant]] = {}
    for part in parts:
        for variant in _part_variants(part, stock):
            if variant.width <= stock.width + EPS and variant.height <= stock.height + EPS:
                groups.setdefault(round(variant.width, 4), []).append(variant)
    return groups


def _build_vertical_strip_candidate_for_stock(
    stock_items: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    orientation_name: str,
    min_reusable_size: float,
) -> OptimizationResult | None:
    available_stock = _expand_stock(stock_items)
    if not available_stock:
        return None
    expanded_parts = _expand_parts(parts)
    layouts: list[SheetLayout] = []
    remaining = expanded_parts[:]
    candidate_width_messages: list[str] = []

    while remaining and available_stock:
        stock = available_stock.pop(0)
        usable_width = stock.width - margin * 2
        usable_height = stock.height - margin * 2
        sheet_stacks: list[_StripStack] = []
        used_width = 0.0

        while remaining:
            free_width = usable_width - used_width - (kerf if sheet_stacks else 0.0)
            if free_width <= EPS:
                break

            groups = _candidate_width_groups(remaining, stock)
            best_choice: tuple[tuple[float, ...], float, list[_PartVariant], list[_StripStack]] | None = None
            for width, variants in groups.items():
                if width > free_width + EPS or len(variants) < 2:
                    continue
                max_strips = int((free_width + kerf) // (width + kerf))
                if max_strips < 1:
                    continue
                variants_by_part_id: dict[int, _PartVariant] = {}
                for variant in variants:
                    current = variants_by_part_id.get(id(variant.part))
                    if current is None or (variant.rotated, variant.height) < (current.rotated, current.height):
                        variants_by_part_id[id(variant.part)] = variant
                selected_variants = list(variants_by_part_id.values())
                if len(selected_variants) < 2:
                    continue
                stacks = _pack_group_into_strips(selected_variants, width, max_strips, usable_height, kerf)
                if not stacks:
                    continue
                packed_count = sum(len(stack.variants) for stack in stacks)
                packed_area = sum(variant.width * variant.height for stack in stacks for variant in stack.variants)
                stack_width = len(stacks) * width + max(0, len(stacks) - 1) * kerf
                total_height = max(stack.height for stack in stacks)
                tiny_group = all(_is_small_filler_variant(variant) for variant in selected_variants)
                choice_score = (1 if tiny_group else 0, -packed_area, -packed_count, stack_width, len(stacks), total_height)
                if best_choice is None or choice_score < best_choice[0]:
                    best_choice = (choice_score, width, selected_variants, stacks)

            if best_choice is None:
                single_options: list[tuple[tuple[float, ...], _PartVariant]] = []
                for part in remaining:
                    for variant in _part_variants(part, stock):
                        if variant.width <= free_width + EPS and variant.height <= usable_height + EPS:
                            temp = SheetLayout(stock=stock, sheet_index=1)
                            temp.parts.append(PlacedSheetPart(variant.part, 0.0, 0.0, variant.width, variant.height, variant.rotated))
                            free_rects = collect_free_rectangles(temp, 0.0)
                            largest = max((rect_area(rect) for rect in free_rects), default=0.0)
                            single_options.append(((-variant.width * variant.height, -largest, variant.width, variant.height), variant))
                if not single_options:
                    break
                _, variant = min(single_options, key=lambda item: item[0])
                stacks = [_StripStack(variant.width)]
                stacks[0].add(variant, kerf)
                selected_variants = [variant]
                width = variant.width
            else:
                _, width, selected_variants, stacks = best_choice

            selected_primary_ids = {id(variant.part) for variant in selected_variants}
            filler_pool = [part for part in remaining if id(part) not in selected_primary_ids]
            filler_ids, filler_messages = _pack_filler_rows_into_stacks(stacks, filler_pool, stock, usable_height, kerf)
            stacks = _best_stack_order_for_manufacturing(stock, stacks, kerf, margin)
            sheet_stacks.extend(stacks)
            added_width = sum(stack.width for stack in stacks) + max(0, len(stacks) - 1) * kerf
            used_width += (kerf if used_width > EPS else 0.0) + added_width
            placed_ids = selected_primary_ids | filler_ids
            remaining = [part for part in remaining if id(part) not in placed_ids]
            candidate_width_messages.append(
                f"strip width {width:.0f} on sheet {len(layouts) + 1}: "
                + " | ".join("+".join(f"{variant.width:.0f}x{variant.height:.0f}" for variant in stack.variants) for stack in stacks)
            )
            candidate_width_messages.extend(filler_messages)

        if not sheet_stacks:
            break
        layout = _draw_strip_sheet(stock, len(layouts) + 1, sheet_stacks, kerf, margin)
        layouts.append(layout)

    unplaced = remaining
    used_area = sum(layout.used_area for layout in layouts)
    total_area = sum(layout.consumed_area for layout in layouts)
    result = OptimizationResult(
        job_type="sheet",
        algorithm="Vertical Strip Candidate",
        sheet_layouts=layouts,
        unplaced_sheet_parts=unplaced,
        total_cost=sum(layout.stock.price for layout in layouts),
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        messages=[
            f"Selected packing candidate: mandatory vertical strip / {orientation_name}",
            "Vertical strip candidate debug:",
            *candidate_width_messages,
        ],
    )
    annotate_result_metrics(result, kerf, min_reusable_size)
    return result


def _is_horizontal_row_candidate(part: SheetPart, stock: SheetStock) -> tuple[bool, "_PartVariant | None"]:
    """Return (True, wide_variant) when the part strongly benefits from being
    placed as a flat horizontal row rather than a tall vertical strip.

    Criteria (all must hold):
    * The part's widest variant has aspect ratio >= 4.0  (strongly elongated)
    * Its wide width >= 60% of the shorter stock dimension  (long enough that
      lying flat saves meaningful plate length)
    * Its row height (height when wide) <= 35% of the stock height  (a
      manageable number of rows fit in one plate without wasting too much of
      the usable height on row spacing)

    Note: G/H-type parts (e.g. 753×160) have aspect ratio ~4.7 but wide_width
    753mm < 0.6 × 1500mm = 900mm, so they are correctly excluded and placed as
    normal vertical strips instead.
    """
    variants = _part_variants(part, stock)
    if not variants:
        return False, None
    wide = max(variants, key=lambda v: (v.width, -v.height))
    if wide.width <= EPS or wide.height <= EPS:
        return False, None
    shorter_dim = min(stock.width, stock.height)
    if (
        wide.width / wide.height >= 4.0
        and wide.width >= 0.60 * shorter_dim
        and wide.height <= 0.35 * stock.height
    ):
        return True, wide
    return False, None


def _build_horizontal_block_candidate_for_stock(
    stock_items: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    orientation_name: str,
    min_reusable_size: float,
) -> "OptimizationResult | None":
    """Candidate that lays tall-thin parts (e.g. 1080×200 boards) as
    horizontal rows on the left portion of the plate, then packs remaining
    parts as vertical strips on the right.

    This saves significant plate length when many long narrow parts (aspect
    ratio ≥ 4, width ≥ 60% of the short plate dimension) are present:
    placing 8 such parts as 8 vertical 200mm-wide strips uses ~1635mm, while
    7 horizontal rows of up to 1124mm use only ~1124mm for the same group.
    The remaining parts and the 8th piece then fill a much smaller right zone,
    bringing total used plate length down by ~300mm in typical scenarios.
    """
    available = _expand_stock(stock_items)
    if not available:
        return None
    stock = available[0]
    usable_h = stock.height - 2 * margin
    usable_w = stock.width - 2 * margin

    expanded = _expand_parts(parts)

    # ── 1. Identify tall-thin (horizontal-row) candidates ─────────────────
    row_candidates: list[tuple[SheetPart, "_PartVariant"]] = []
    other_parts: list[SheetPart] = []
    for part in expanded:
        ok, wide_var = _is_horizontal_row_candidate(part, stock)
        if ok and wide_var is not None:
            row_candidates.append((part, wide_var))
        else:
            other_parts.append(part)

    if len(row_candidates) < 3:
        # Too few long parts to justify this layout; normal strip candidate
        # handles these cases adequately.
        return None

    # ── 2. Sort: widest-first so the block width is determined early ───────
    row_candidates.sort(key=lambda pv: (-pv[1].width, pv[1].height, pv[0].name))
    block_width = max(pv[1].width for pv in row_candidates)
    if block_width > usable_w + EPS:
        return None

    # ── 3. Place rows top-to-bottom until the plate height is exhausted ────
    placed_rows: list["PlacedSheetPart"] = []
    unfit_row_parts: list[SheetPart] = []
    y_cursor = margin

    for part, variant in row_candidates:
        row_h = variant.height
        if y_cursor + row_h > stock.height - margin + EPS:
            unfit_row_parts.append(part)
            continue
        placed_rows.append(
            PlacedSheetPart(
                part=part,
                x=margin,
                y=y_cursor,
                width=variant.width,
                height=row_h,
                rotated=variant.rotated,
            )
        )
        y_cursor += row_h + kerf

    if len(placed_rows) < 2:
        return None  # Degenerate — not useful

    placed_ids: set[int] = {id(pl.part) for pl in placed_rows}

    # ── 4. Pack remaining parts as vertical strips to the right ───────────
    right_start = margin + block_width + kerf
    right_usable_w = stock.width - margin - right_start
    # All parts not in the horizontal block: unfit rows + other parts
    remaining_for_right = unfit_row_parts + other_parts
    right_stacks: list["_StripStack"] = []

    if remaining_for_right and right_usable_w > kerf:
        used_right_w = 0.0
        todo = remaining_for_right[:]

        while todo:
            free_w = right_usable_w - used_right_w - (kerf if right_stacks else 0.0)
            if free_w <= EPS:
                break

            groups = _candidate_width_groups(todo, stock)
            best_choice: "tuple | None" = None

            for width, variants in groups.items():
                if width > free_w + EPS or len(variants) < 2:
                    continue
                max_strips = max(1, int((free_w + kerf) / (width + kerf)))
                by_id: dict[int, "_PartVariant"] = {}
                for v in variants:
                    cur = by_id.get(id(v.part))
                    if cur is None or (v.rotated, v.height) < (cur.rotated, cur.height):
                        by_id[id(v.part)] = v
                sel = list(by_id.values())
                if not sel:
                    continue
                stacks = _pack_group_into_strips(sel, width, max_strips, usable_h, kerf)
                if not stacks:
                    continue
                packed_area = sum(v.width * v.height for s in stacks for v in s.variants)
                packed_count = sum(len(s.variants) for s in stacks)
                tiny = all(_is_small_filler_variant(v) for v in sel)
                score: tuple = (1 if tiny else 0, -packed_area, -packed_count)
                if best_choice is None or score < best_choice[0]:
                    best_choice = (score, width, sel, stacks)

            if best_choice is None:
                # Single-part fallback
                best_single: "tuple | None" = None
                for part in todo:
                    for v in _part_variants(part, stock):
                        if v.width <= free_w + EPS and v.height <= usable_h + EPS:
                            score_s: tuple = (-v.width * v.height, v.width, v.height)
                            if best_single is None or score_s < best_single[0]:
                                best_single = (score_s, v)
                if best_single is None:
                    break
                v = best_single[1]
                stk = _StripStack(v.width)
                stk.add(v, kerf)
                best_choice = ((0,), v.width, [v], [stk])

            _, width, sel, stacks = best_choice
            sel_ids = {id(v.part) for v in sel}
            stacks = _best_stack_order_for_manufacturing(stock, stacks, kerf, margin)
            right_stacks.extend(stacks)
            placed_ids.update(sel_ids)
            added_w = sum(s.width for s in stacks) + max(0, len(stacks) - 1) * kerf
            used_right_w += (kerf if used_right_w > EPS else 0.0) + added_w
            todo = [p for p in todo if id(p) not in placed_ids]

    # ── 5. Assemble the SheetLayout ────────────────────────────────────────
    layout = SheetLayout(stock=stock, sheet_index=1)
    layout.parts.extend(placed_rows)

    # Segment 1: the horizontal-row block.  Parts of different widths live in
    # the same segment (each row is effectively its own guillotine band).  The
    # guillotine validator accepts parts that are narrower than the segment
    # width — the width difference becomes a small trim cut per row.
    layout.vertical_segments.append({
        "index": 1,
        "x": round(margin, 3),
        "width": round(block_width, 3),
        "right": round(margin + block_width, 3),
    })

    # Segments 2+: right-side vertical strips, drawn from right_start.
    x_offset = right_start
    seg_idx = 2
    for stack in right_stacks:
        y_off = margin
        rows_data = stack.rows or [[(v, 0.0)] for v in stack.variants]
        for row in rows_data:
            row_h = max((v.height for v, _ in row), default=0.0)
            for variant, offset_x in row:
                layout.parts.append(
                    PlacedSheetPart(
                        part=variant.part,
                        x=x_offset + offset_x,
                        y=y_off,
                        width=variant.width,
                        height=variant.height,
                        rotated=variant.rotated,
                    )
                )
                placed_ids.add(id(variant.part))
            y_off += row_h + kerf
        layout.vertical_segments.append({
            "index": seg_idx,
            "x": round(x_offset, 3),
            "width": round(stack.width, 3),
            "right": round(x_offset + stack.width, 3),
        })
        x_offset += stack.width + kerf
        seg_idx += 1

    unplaced = [p for p in expanded if id(p) not in placed_ids]

    used_area = layout.used_area
    total_area = layout.consumed_area
    result = OptimizationResult(
        job_type="sheet",
        algorithm="Horizontal Block Candidate",
        sheet_layouts=[layout],
        unplaced_sheet_parts=unplaced,
        total_cost=stock.price,
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        messages=[
            f"Selected packing candidate: horizontal block / {orientation_name}",
            (
                f"horizontal block: {len(placed_rows)} rows, "
                f"block_width={block_width:.0f}mm, "
                f"right_strips={len(right_stacks)}"
            ),
        ],
    )
    annotate_result_metrics(result, kerf, min_reusable_size)
    return result


def _build_horizontal_block_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = True,
) -> list[OptimizationResult]:
    """Generate horizontal-block candidates for all stock orientations."""
    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        candidate = _build_horizontal_block_candidate_for_stock(
            oriented_stock, parts, kerf, margin, orientation_name, min_reusable_size
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _pack_exact_small_guillotine_sheet(
    stock: SheetStock,
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    node_budget: int = 80_000,
) -> tuple[list[PlacedSheetPart], set[int]]:
    """Find a compact guillotine layout for a small set of concrete parts.

    This is deliberately a bounded exhaustive fallback, not the primary solver.
    The regular strip heuristics are far faster on normal BOMs, but their search
    space misses simple horizontal-band layouts.  For a small number of part
    instances we can safely enumerate both guillotine split orders after every
    placement and retain the most complete legal layout.
    """
    usable_width = stock.width - margin * 2
    usable_height = stock.height - margin * 2
    if usable_width <= EPS or usable_height <= EPS or not parts:
        return [], set()

    best_parts: list[PlacedSheetPart] = []
    best_ids: set[int] = set()
    best_area = 0.0
    nodes = 0
    seen: set[tuple[tuple[int, ...], tuple[tuple[int, int, int, int], ...]]] = set()

    def update(placed: list[PlacedSheetPart], placed_ids: set[int]) -> None:
        nonlocal best_parts, best_ids, best_area
        area = sum(item.width * item.height for item in placed)
        if len(placed) > len(best_parts) or (len(placed) == len(best_parts) and area > best_area + EPS):
            best_parts = list(placed)
            best_ids = set(placed_ids)
            best_area = area

    def state_key(remaining: list[SheetPart], rects: list[Rect]) -> tuple[tuple[int, ...], tuple[tuple[int, int, int, int], ...]]:
        return (
            tuple(sorted(id(part) for part in remaining)),
            tuple(sorted(tuple(round(value * 1000) for value in rect) for rect in rects)),
        )

    def descend(remaining: list[SheetPart], rects: list[Rect], placed: list[PlacedSheetPart], placed_ids: set[int]) -> None:
        nonlocal nodes
        nodes += 1
        update(placed, placed_ids)
        if not remaining or nodes >= node_budget or len(best_parts) == len(parts):
            return
        if len(placed) + len(remaining) <= len(best_parts):
            return
        key = state_key(remaining, rects)
        if key in seen:
            return
        seen.add(key)

        # Largest-first finds a strong incumbent early; every concrete part is
        # still explored, so a smaller horizontal band can precede it when that
        # is the only complete solution.
        ordered = sorted(
            enumerate(remaining),
            key=lambda item: (-(item[1].width * item[1].height), -max(item[1].width, item[1].height), item[1].name, id(item[1])),
        )
        for part_index, part in ordered:
            for rect_index, (x, y, width, height) in enumerate(rects):
                for variant in _part_variants(part, stock):
                    if variant.width > width + EPS or variant.height > height + EPS:
                        continue
                    next_remaining = remaining[:part_index] + remaining[part_index + 1 :]
                    placement = PlacedSheetPart(part, x, y, variant.width, variant.height, variant.rotated)
                    # A guillotine cut can first separate either the right band
                    # or the bottom band.  Enumerating both keeps this fallback
                    # independent of the vertical-strip heuristic.
                    residual_sets: list[list[Rect]] = []
                    right_width = width - variant.width - kerf
                    bottom_height = height - variant.height - kerf
                    horizontal_first = list(rects[:rect_index] + rects[rect_index + 1 :])
                    if bottom_height > EPS:
                        horizontal_first.append((x, y + variant.height + kerf, width, bottom_height))
                    if right_width > EPS:
                        horizontal_first.append((x + variant.width + kerf, y, right_width, variant.height))
                    residual_sets.append(horizontal_first)

                    vertical_first = list(rects[:rect_index] + rects[rect_index + 1 :])
                    if right_width > EPS:
                        vertical_first.append((x + variant.width + kerf, y, right_width, height))
                    if bottom_height > EPS:
                        vertical_first.append((x, y + variant.height + kerf, variant.width, bottom_height))
                    residual_sets.append(vertical_first)

                    for next_rects in residual_sets:
                        descend(next_remaining, next_rects, [*placed, placement], placed_ids | {id(part)})
                        if len(best_parts) == len(parts) or nodes >= node_budget:
                            return

    descend(parts, [(margin, margin, usable_width, usable_height)], [], set())
    return best_parts, best_ids


def _build_exact_small_guillotine_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = True,
) -> list[OptimizationResult]:
    """Add bounded complete-search candidates for small sheet-cutting jobs."""
    expanded = _expand_parts(parts)
    # Keep the exhaustive fallback tightly bounded.  Larger BOMs are handled by
    # the production heuristics and would turn the candidate search exponential.
    if not expanded or len(expanded) > 8:
        return []

    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        remaining = list(expanded)
        layouts: list[SheetLayout] = []
        for stock_item in _expand_stock(oriented_stock):
            if not remaining:
                break
            placements, placed_ids = _pack_exact_small_guillotine_sheet(
                stock_item, remaining, kerf, margin, node_budget=20_000
            )
            if not placements:
                continue
            layout = SheetLayout(stock=stock_item, sheet_index=len(layouts) + 1, parts=placements)
            layout.vertical_segments = [{
                "index": 1,
                "x": round(margin, 3),
                "width": round(stock_item.width - margin * 2, 3),
                "right": round(stock_item.width - margin, 3),
                "source": "exact-small-guillotine",
            }]
            layouts.append(layout)
            remaining = [part for part in remaining if id(part) not in placed_ids]
        if not layouts:
            continue
        result = OptimizationResult(
            job_type="sheet",
            algorithm="Exact Small Guillotine Candidate",
            sheet_layouts=layouts,
            unplaced_sheet_parts=remaining,
            total_cost=sum(layout.stock.price for layout in layouts),
            messages=[f"Selected packing candidate: bounded exact guillotine / {orientation_name}"],
        )
        annotate_result_metrics(result, kerf, min_reusable_size)
        candidates.append(result)
    return candidates


def _build_vertical_strip_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = True,
) -> list[OptimizationResult]:
    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        candidate = _build_vertical_strip_candidate_for_stock(oriented_stock, parts, kerf, margin, orientation_name, min_reusable_size)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _strip_total_width(stacks: list[_StripStack], kerf: float) -> float:
    return sum(stack.width for stack in stacks) + max(0, len(stacks) - 1) * kerf


def _span_size(count: int, size: float, kerf: float) -> float:
    if count <= 0:
        return 0.0
    return count * size + max(0, count - 1) * kerf


def _uniform_grid_part_key(part: SheetPart) -> tuple[object, ...]:
    return (
        part.name,
        round(part.width, 4),
        round(part.height, 4),
        part.material,
        round(part.thickness, 4),
        part.allow_rotation,
        part.grain_direction,
    )


def _build_uniform_grid_candidate_for_stock(
    stock_items: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    orientation_name: str,
    min_reusable_size: float,
) -> OptimizationResult | None:
    expanded_parts = _expand_parts(parts)
    if len(expanded_parts) < 4:
        return None
    if len({_uniform_grid_part_key(part) for part in expanded_parts}) != 1:
        return None

    available_stock = _expand_stock(stock_items)
    if not available_stock:
        return None

    remaining = expanded_parts[:]
    layouts: list[SheetLayout] = []
    messages: list[str] = [f"Selected packing candidate: uniform grid / {orientation_name}"]

    while remaining and available_stock:
        stock = available_stock.pop(0)
        usable_width = stock.width - margin * 2
        usable_height = stock.height - margin * 2
        sample = remaining[0]
        options: list[tuple[tuple[float, ...], _PartVariant, int, int, int]] = []
        for variant in _part_variants(sample, stock):
            if variant.width > usable_width + EPS or variant.height > usable_height + EPS:
                continue
            cols = max(0, int((usable_width + kerf + EPS) // (variant.width + kerf)))
            rows = max(0, int((usable_height + kerf + EPS) // (variant.height + kerf)))
            capacity = cols * rows
            if capacity <= 0:
                continue
            place_count = min(capacity, len(remaining))
            used_width = _span_size(min(cols, place_count), variant.width, kerf)
            used_rows = math.ceil(place_count / max(1, cols))
            used_height = _span_size(used_rows, variant.height, kerf)
            saved_length = used_width if _saved_axis(stock) == "x" else used_height
            strategic_fill = used_height if _saved_axis(stock) == "x" else used_width
            options.append(
                (
                    (
                        -place_count,
                        -strategic_fill,
                        saved_length,
                        1 if variant.rotated else 0,
                        variant.width * variant.height,
                    ),
                    variant,
                    cols,
                    rows,
                    place_count,
                )
            )
        if not options:
            break

        _, variant, cols, _rows, place_count = min(options, key=lambda item: item[0])
        layout = SheetLayout(stock=stock, sheet_index=len(layouts) + 1)
        for index in range(place_count):
            col = index % cols
            row = index // cols
            part = remaining[index]
            layout.parts.append(
                PlacedSheetPart(
                    part=part,
                    x=margin + col * (variant.width + kerf),
                    y=margin + row * (variant.height + kerf),
                    width=variant.width,
                    height=variant.height,
                    rotated=variant.rotated,
                )
            )
        used_cols = min(cols, place_count)
        for col in range(used_cols):
            left = margin + col * (variant.width + kerf)
            layout.vertical_segments.append(
                {
                    "index": col + 1,
                    "x": round(left, 3),
                    "width": round(variant.width, 3),
                    "right": round(left + variant.width, 3),
                    "source": "uniform-grid",
                }
            )
        layouts.append(layout)
        remaining = remaining[place_count:]
        messages.append(
            f"uniform grid sheet={layout.sheet_index} "
            f"{used_cols} cols x {math.ceil(place_count / max(1, cols))} rows "
            f"variant={variant.width:.0f}x{variant.height:.0f} placed={place_count}"
        )

    if not layouts:
        return None

    used_area = sum(layout.used_area for layout in layouts)
    total_area = sum(layout.consumed_area for layout in layouts)
    result = OptimizationResult(
        job_type="sheet",
        algorithm="Uniform Grid Candidate",
        sheet_layouts=layouts,
        unplaced_sheet_parts=remaining,
        total_cost=sum(layout.stock.price for layout in layouts),
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        messages=messages,
    )
    annotate_result_metrics(result, kerf, min_reusable_size)
    return result


def _build_uniform_grid_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = True,
) -> list[OptimizationResult]:
    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        candidate = _build_uniform_grid_candidate_for_stock(
            oriented_stock, parts, kerf, margin, orientation_name, min_reusable_size
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _repeated_mixed_grid_key(part: SheetPart) -> tuple[object, ...]:
    """Identity used by the mixed two-zone grid candidate.

    This candidate deliberately handles one repeated, rotatable formatka.  It
    complements the strip builders for cases where a row of one orientation
    over two rows of the other leaves a narrow, useful side strip.  The old
    candidates missed that topology and could leave a whole additional part
    off a 3000 x 1500 sheet.
    """
    return (
        part.name,
        round(part.width, 4),
        round(part.height, 4),
        part.material,
        round(part.thickness, 4),
        part.allow_rotation,
        part.grain_direction,
    )


def _draw_repeated_mixed_grid_layout(
    stock: SheetStock,
    sheet_index: int,
    parts: list[SheetPart],
    narrow: _PartVariant,
    wide: _PartVariant,
    top_columns: int,
    wide_columns: int,
    wide_rows: int,
    side_columns: int,
    side_rows: int,
    kerf: float,
    margin: float,
) -> SheetLayout | None:
    """Draw a guillotine-safe mixed grid with a separate narrow side strip."""
    main_width = max(
        _span_size(top_columns, narrow.width, kerf),
        _span_size(wide_columns, wide.width, kerf),
    )
    main_has_parts = top_columns > 0 or (wide_columns > 0 and wide_rows > 0)
    if not main_has_parts and side_columns <= 0:
        return None

    layout = SheetLayout(stock=stock, sheet_index=sheet_index)
    part_index = 0

    def take_part() -> SheetPart | None:
        nonlocal part_index
        if part_index >= len(parts):
            return None
        part = parts[part_index]
        part_index += 1
        return part

    if main_has_parts:
        y = margin
        if top_columns:
            for column in range(top_columns):
                part = take_part()
                if part is None:
                    break
                layout.parts.append(
                    PlacedSheetPart(
                        part, margin + column * (narrow.width + kerf), y,
                        narrow.width, narrow.height, narrow.rotated,
                    )
                )
            y += narrow.height + (kerf if wide_columns and wide_rows else 0.0)
        for row in range(wide_rows):
            for column in range(wide_columns):
                part = take_part()
                if part is None:
                    break
                layout.parts.append(
                    PlacedSheetPart(
                        part, margin + column * (wide.width + kerf), y,
                        wide.width, wide.height, wide.rotated,
                    )
                )
            if part_index >= len(parts):
                break
            y += wide.height + kerf
        layout.vertical_segments.append(
            {
                "index": 1,
                "x": round(margin, 3),
                "width": round(main_width, 3),
                "right": round(margin + main_width, 3),
                "source": "repeated-mixed-grid-main",
            }
        )

    side_x = margin + main_width + (kerf if main_has_parts else 0.0)
    for side_column in range(side_columns):
        if part_index >= len(parts):
            break
        x = side_x + side_column * (narrow.width + kerf)
        before = part_index
        for row in range(side_rows):
            part = take_part()
            if part is None:
                break
            layout.parts.append(
                PlacedSheetPart(
                    part, x, margin + row * (narrow.height + kerf),
                    narrow.width, narrow.height, narrow.rotated,
                )
            )
        if part_index > before:
            layout.vertical_segments.append(
                {
                    "index": len(layout.vertical_segments) + 1,
                    "x": round(x, 3),
                    "width": round(narrow.width, 3),
                    "right": round(x + narrow.width, 3),
                    "source": "repeated-mixed-grid-side",
                }
            )

    return layout if layout.parts else None


def _best_repeated_mixed_grid_layout(
    stock: SheetStock,
    parts: list[SheetPart],
    kerf: float,
    margin: float,
) -> SheetLayout | None:
    if not parts:
        return None
    variants = _part_variants(parts[0], stock)
    if len(variants) < 2:
        return None
    narrow = min(variants, key=lambda variant: (variant.width, -variant.height, variant.rotated))
    wide = max(variants, key=lambda variant: (variant.width, -variant.height, not variant.rotated))
    if narrow.width >= wide.width - EPS or narrow.height <= wide.height + EPS:
        return None
    # Extremely elongated pieces are structural strips, not grid tiles.  The
    # dedicated vertical-strip candidates retain their production-friendly
    # residual bands; forcing such pieces through this two-zone grid can make
    # a valid long-strip layout needlessly taller.
    aspect_ratio = max(wide.width, wide.height) / max(min(wide.width, wide.height), EPS)
    if aspect_ratio > 4.0:
        return None
    # Near-square parts are already covered optimally by the uniform-grid
    # candidate.  Searching split strips for them adds many equivalent plans
    # without freeing a meaningful side band.
    if aspect_ratio < 1.20:
        return None

    usable_width = stock.width - margin * 2
    usable_height = stock.height - margin * 2
    max_narrow_columns = int((usable_width + kerf + EPS) // (narrow.width + kerf))
    max_wide_columns = int((usable_width + kerf + EPS) // (wide.width + kerf))
    max_side_rows = int((usable_height + kerf + EPS) // (narrow.height + kerf))
    if max_narrow_columns <= 0 or max_wide_columns <= 0 or max_side_rows <= 0:
        return None

    def grid_values(maximum: int) -> list[int]:
        """Keep the topology search dense for small sheets and bounded for grids."""
        if maximum <= 8:
            return list(range(maximum + 1))
        step = max(1, math.ceil(maximum / 6))
        values = {0, 1, 2, 3, maximum, maximum - 1, maximum - 2}
        values.update(range(0, maximum + 1, step))
        return sorted(value for value in values if 0 <= value <= maximum)

    best_layout: SheetLayout | None = None
    best_score: tuple[float, ...] | None = None
    # The dimensions bound the search to a small grid (typically below 1,000
    # plans) while covering all meaningful strip/row combinations.
    for top_columns in grid_values(max_narrow_columns):
        top_height = narrow.height if top_columns else 0.0
        for wide_columns in grid_values(max_wide_columns):
            if not top_columns and not wide_columns:
                continue
            main_width = max(
                _span_size(top_columns, narrow.width, kerf),
                _span_size(wide_columns, wide.width, kerf),
            )
            if main_width > usable_width + EPS:
                continue
            available_height = usable_height - top_height - (kerf if top_columns and wide_columns else 0.0)
            max_wide_rows = (
                int((available_height + kerf + EPS) // (wide.height + kerf))
                if wide_columns and available_height >= wide.height - EPS
                else 0
            )
            for wide_rows in grid_values(max_wide_rows):
                if not top_columns and not wide_rows:
                    continue
                used_main_height = (
                    top_height
                    + (kerf if top_columns and wide_rows else 0.0)
                    + _span_size(wide_rows, wide.height, kerf)
                )
                if used_main_height > usable_height + EPS:
                    continue
                side_space = usable_width - main_width - (kerf if main_width > EPS else 0.0)
                max_side_columns = int((side_space + kerf + EPS) // (narrow.width + kerf))
                for side_columns in grid_values(max(0, max_side_columns)):
                    capacity = top_columns + wide_columns * wide_rows + side_columns * max_side_rows
                    if capacity <= 0:
                        continue
                    layout = _draw_repeated_mixed_grid_layout(
                        stock, 1, parts[: min(len(parts), capacity)], narrow, wide,
                        top_columns, wide_columns, wide_rows, side_columns, max_side_rows,
                        kerf, margin,
                    )
                    if layout is None:
                        continue
                    valid, _errors = validate_guillotine_feasibility(layout, kerf)
                    if not valid:
                        continue
                    placed = len(layout.parts)
                    # First fill the sheet.  Once equal, use exactly the
                    # production axis chosen by _saved_axis, then favour a
                    # compact bounding box and fewer strips.
                    score = (
                        -placed,
                        _saved_used_length(layout),
                        _saved_consumed_area(layout),
                        layout.used_width * layout.used_height,
                        len(layout.vertical_segments),
                    )
                    if best_score is None or score < best_score:
                        best_layout = layout
                        best_score = score
    return best_layout


def _build_repeated_mixed_grid_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = True,
) -> list[OptimizationResult]:
    """Build multi-sheet candidates for repeated parts with both orientations."""
    expanded = _expand_parts(parts)
    if len(expanded) < 2 or len({_repeated_mixed_grid_key(part) for part in expanded}) != 1:
        return []

    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        remaining = list(expanded)
        layouts: list[SheetLayout] = []
        for item in _expand_stock(oriented_stock):
            layout = _best_repeated_mixed_grid_layout(item, remaining, kerf, margin)
            if layout is None:
                continue
            layout.sheet_index = len(layouts) + 1
            layouts.append(layout)
            remaining = remaining[len(layout.parts):]
            if not remaining:
                break
        if not layouts:
            continue
        used_area = sum(layout.used_area for layout in layouts)
        total_area = sum(layout.consumed_area for layout in layouts)
        result = OptimizationResult(
            job_type="sheet",
            algorithm="Repeated Mixed Grid Candidate",
            sheet_layouts=layouts,
            unplaced_sheet_parts=remaining,
            total_cost=sum(layout.stock.price for layout in layouts),
            waste=max(0.0, total_area - used_area),
            utilization=used_area / total_area * 100.0 if total_area else 0.0,
            messages=[
                f"Selected packing candidate: repeated mixed grid / {orientation_name}",
                "mixed grid combines a top narrow row, wide lower rows and a narrow side strip",
            ],
        )
        annotate_result_metrics(result, kerf, min_reusable_size)
        candidates.append(result)
    return candidates


def _split_counts(total: int) -> list[int]:
    if total <= 0:
        return []
    counts = {0, total}
    step = max(1, min(5, total // 10 or 1))
    counts.update(range(step, total, step))
    for value in (1, 2, 3, total - 3, total - 2, total - 1):
        if 0 <= value <= total:
            counts.add(value)
    return sorted(counts)


def _natural_partition_counts(
    n_total: int,
    tall: _PartVariant,
    wide: _PartVariant,
    stock: SheetStock,
    kerf: float,
) -> list[int]:
    """Candidate wide_count values based on natural tier-filling geometry.

    Complements :func:`_split_counts` by discovering split points that
    correspond to filling complete rows or columns of the stock sheet:

    * How many *tall*-oriented parts fill one complete horizontal row
      (spanning ``stock.width``)? Each full-row tier yields a natural
      ``tall_count``; the complement becomes a ``wide_count`` candidate.

    * How many *wide*-oriented parts stack in one complete vertical column
      (spanning ``stock.height``)? Each such column count becomes a
      ``wide_count`` candidate directly.

    Example: 55 B parts (40x500) on a 2000x1000mm stock, kerf=5mm.
      tall orientation: width=40 → per_row = floor(2005 / 45) = 44
      → tall_count=44, wide_count=11 is a natural split (the 2-tier layout).
      wide orientation: height=40 → per_col = floor(1005 / 45) = 22
      → wide_count=22, 44 are further candidates.
    """
    counts: set[int] = set()

    # How many tall-oriented parts fill a complete horizontal row?
    if tall.width > EPS:
        per_row = int((stock.width + kerf) / (tall.width + kerf))
        if per_row > 0:
            for tiers in range(1, n_total):
                tall_count = per_row * tiers
                if tall_count >= n_total:
                    break
                wide_count = n_total - tall_count
                if 0 < wide_count < n_total:
                    counts.add(wide_count)

    # How many wide-oriented parts stack in a complete vertical column?
    if wide.height > EPS:
        per_col = int((stock.height + kerf) / (wide.height + kerf))
        if per_col > 0:
            for cols in range(1, n_total):
                wide_count = per_col * cols
                if wide_count >= n_total:
                    break
                if 0 < wide_count < n_total:
                    counts.add(wide_count)

    return sorted(counts)


def _draw_mixed_split_layout(
    stock: SheetStock,
    sheet_index: int,
    wide_parts: list[SheetPart],
    tall_parts: list[SheetPart],
    wide: _PartVariant,
    tall: _PartVariant,
    kerf: float,
    margin: float,
    layout_mode: str,
) -> SheetLayout | None:
    usable_width = stock.width - margin * 2
    usable_height = stock.height - margin * 2
    layout = SheetLayout(stock=stock, sheet_index=sheet_index)

    if layout_mode == "wide-left-tall-right":
        wide_capacity = int((usable_height + kerf) // (wide.height + kerf))
        if wide_parts and wide_capacity <= 0:
            return None
        wide_strip_count = math.ceil(len(wide_parts) / wide_capacity) if wide_parts else 0
        wide_width = _span_size(wide_strip_count, wide.width, kerf)
        tall_start_x = margin + wide_width + (kerf if wide_strip_count and tall_parts else 0.0)
        tall_capacity = int((usable_width - (tall_start_x - margin) + kerf) // (tall.width + kerf)) if tall_parts else 0
        if tall_capacity < len(tall_parts):
            return None

        cursor_x = margin
        part_index = 0
        for strip_index in range(wide_strip_count):
            y = margin
            strip_parts = wide_parts[part_index : part_index + wide_capacity]
            for part in strip_parts:
                layout.parts.append(PlacedSheetPart(part, cursor_x, y, wide.width, wide.height, wide.rotated))
                y += wide.height + kerf
            layout.vertical_segments.append(
                {"index": len(layout.vertical_segments) + 1, "x": round(cursor_x, 3), "width": round(wide.width, 3), "right": round(cursor_x + wide.width, 3)}
            )
            cursor_x += wide.width + kerf
            part_index += len(strip_parts)

        cursor_x = tall_start_x
        for part in tall_parts:
            layout.parts.append(PlacedSheetPart(part, cursor_x, margin, tall.width, tall.height, tall.rotated))
            layout.vertical_segments.append(
                {"index": len(layout.vertical_segments) + 1, "x": round(cursor_x, 3), "width": round(tall.width, 3), "right": round(cursor_x + tall.width, 3)}
            )
            cursor_x += tall.width + kerf
        return layout

    if layout_mode == "tall-bottom-wide-top":
        tall_capacity = int((usable_width + kerf) // (tall.width + kerf))
        if tall_parts and tall_capacity < len(tall_parts):
            return None
        top_y = margin + tall.height + (kerf if tall_parts and wide_parts else 0.0)
        top_height = stock.height - margin - top_y
        wide_capacity = int((top_height + kerf) // (wide.height + kerf)) if wide_parts else 0
        wide_strip_count = math.ceil(len(wide_parts) / wide_capacity) if wide_parts and wide_capacity > 0 else 0
        if wide_parts and (wide_capacity <= 0 or _span_size(wide_strip_count, wide.width, kerf) > usable_width + EPS):
            return None

        cursor_x = margin
        for part in tall_parts:
            layout.parts.append(PlacedSheetPart(part, cursor_x, margin, tall.width, tall.height, tall.rotated))
            cursor_x += tall.width + kerf

        cursor_x = margin
        part_index = 0
        for _strip_index in range(wide_strip_count):
            y = top_y
            strip_parts = wide_parts[part_index : part_index + wide_capacity]
            for part in strip_parts:
                layout.parts.append(PlacedSheetPart(part, cursor_x, y, wide.width, wide.height, wide.rotated))
                y += wide.height + kerf
            cursor_x += wide.width + kerf
            part_index += len(strip_parts)
        if layout.parts:
            layout.vertical_segments = [
                {
                    "index": 1,
                    "x": round(margin, 3),
                    "width": round(layout.used_width - margin, 3),
                    "right": round(layout.used_width, 3),
                }
            ]
            # Analytically compute the large free area to the RIGHT of the wide
            # strips in the upper band.  collect_free_rectangles() cannot see
            # this internal space (it only looks at the outer bounding box), so
            # we set waste_rects here to give score_result() a realistic offcut
            # estimate before guillotine validation produces the final value.
            if tall_parts and wide_parts and wide_strip_count > 0:
                wide_right = margin + _span_size(wide_strip_count, wide.width, kerf)
                free_x = wide_right + kerf
                free_w = usable_width - _span_size(wide_strip_count, wide.width, kerf) - kerf
                free_h = top_height  # height of the top band
                if free_w > EPS and free_h > EPS:
                    layout.waste_rects = [(free_x, top_y, free_w, free_h)]
        return layout

    return None


def _build_orientation_split_candidates_for_stock(
    stock_items: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    orientation_name: str,
    min_reusable_size: float,
) -> list[OptimizationResult]:
    available_stock = _expand_stock(stock_items)
    if not available_stock:
        return []
    stock = available_stock[0]
    expanded = _expand_parts(parts)
    grouped: dict[tuple[str, float, float], list[SheetPart]] = {}
    for part in expanded:
        grouped.setdefault((part.name, round(part.width, 4), round(part.height, 4)), []).append(part)

    candidates: list[OptimizationResult] = []
    for group_parts in grouped.values():
        if len(group_parts) < 2:
            continue
        variants = _part_variants(group_parts[0], stock)
        if len(variants) < 2:
            continue
        wide = max(variants, key=lambda variant: (variant.width, -variant.height))
        tall = max(variants, key=lambda variant: (variant.height, -variant.width))
        if wide.width <= tall.width + EPS or tall.height <= wide.height + EPS:
            continue

        # Merge step-based and geometry-derived split counts so the optimizer
        # also tests natural tier-fill splits (e.g. 44+11 for B=40x500 on
        # a 2000x1000 stock) that _split_counts() misses with its fixed step.
        natural = _natural_partition_counts(len(group_parts), tall, wide, stock, kerf)
        all_split_counts = sorted(set(_split_counts(len(group_parts))) | set(natural))
        for wide_count in all_split_counts:
            tall_count = len(group_parts) - wide_count
            if wide_count in (0, len(group_parts)):
                continue
            wide_parts = group_parts[:wide_count]
            tall_parts = group_parts[wide_count:]
            for layout_mode in ("wide-left-tall-right", "tall-bottom-wide-top"):
                layout = _draw_mixed_split_layout(
                    stock,
                    1,
                    wide_parts,
                    tall_parts,
                    wide,
                    tall,
                    kerf,
                    margin,
                    layout_mode,
                )
                if layout is None or not layout.parts:
                    continue
                used_ids = {id(placement.part) for placement in layout.parts}
                remaining = [part for part in expanded if id(part) not in used_ids]
                messages = [
                    f"Selected packing candidate: orientation split / {orientation_name}",
                    (
                        f"orientation split group {group_parts[0].name}: "
                        f"wide={wide_count} tall={tall_count} mode={layout_mode} kerf={kerf:.2f}"
                    ),
                ]
                remaining = _recover_internal_fillers(
                    layout,
                    remaining,
                    kerf,
                    margin,
                    messages if os.environ.get("SIEKACZ_DEBUG_CANDIDATES") else None,
                )
                result = OptimizationResult(
                    job_type="sheet",
                    algorithm="Orientation Split Candidate",
                    sheet_layouts=[layout],
                    unplaced_sheet_parts=remaining,
                    total_cost=stock.price,
                    messages=messages,
                )
                used_area = sum(item.used_area for item in result.sheet_layouts)
                total_area = sum(item.consumed_area for item in result.sheet_layouts)
                result.waste = max(0.0, total_area - used_area)
                result.utilization = used_area / total_area * 100.0 if total_area else 0.0
                annotate_result_metrics(result, kerf, min_reusable_size)
                candidates.append(result)
    return candidates


def _build_orientation_split_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = True,
) -> list[OptimizationResult]:
    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        candidates.extend(_build_orientation_split_candidates_for_stock(oriented_stock, parts, kerf, margin, orientation_name, min_reusable_size))
    return candidates


def _build_mixed_orientation_candidate_for_stock(
    stock_items: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    orientation_name: str,
    min_reusable_size: float,
) -> OptimizationResult | None:
    available_stock = _expand_stock(stock_items)
    if not available_stock:
        return None
    stock = available_stock.pop(0)
    usable_width = stock.width - margin * 2
    usable_height = stock.height - margin * 2
    expanded = _expand_parts(parts)
    grouped: dict[tuple[str, float, float], list[SheetPart]] = {}
    for part in expanded:
        key = (part.name, round(part.width, 4), round(part.height, 4))
        grouped.setdefault(key, []).append(part)

    best_layout: SheetLayout | None = None
    best_unplaced: list[SheetPart] = expanded
    best_message = ""

    for group_parts in grouped.values():
        if len(group_parts) < 2:
            continue
        variants = _part_variants(group_parts[0], stock)
        if len(variants) < 2:
            continue
        wide_template = max(variants, key=lambda item: (item.width, -item.height))
        tall_template = min(variants, key=lambda item: (item.width, -item.height))
        if wide_template.width <= tall_template.width + EPS or wide_template.height >= tall_template.height - EPS:
            continue
        if (
            wide_template.width > usable_width + EPS
            or wide_template.height > usable_height + EPS
            or tall_template.width > usable_width + EPS
            or tall_template.height > usable_height + EPS
        ):
            continue

        wide_capacity = int((usable_height + kerf) // (wide_template.height + kerf))
        if wide_capacity <= 0 or wide_capacity >= len(group_parts):
            continue
        wide_count = min(wide_capacity, len(group_parts) - 1)
        used_ids: set[int] = set()
        stacks: list[_StripStack] = []

        wide_stack = _StripStack(wide_template.width)
        for part in group_parts[:wide_count]:
            wide_stack.add(_PartVariant(part, wide_template.width, wide_template.height, wide_template.rotated), kerf)
            used_ids.add(id(part))
        stacks.append(wide_stack)

        tall_stack: _StripStack | None = None
        for part in group_parts[wide_count:]:
            variant = _PartVariant(part, tall_template.width, tall_template.height, tall_template.rotated)
            if tall_stack is None or not tall_stack.can_add(variant, usable_height, kerf):
                tall_stack = _StripStack(tall_template.width)
                stacks.append(tall_stack)
            tall_stack.add(variant, kerf)
            used_ids.add(id(part))

        remaining = [part for part in expanded if id(part) not in used_ids]
        filler_ids, _ = _pack_filler_rows_into_stacks(stacks, remaining, stock, usable_height, kerf)
        used_ids.update(filler_ids)

        while True:
            remaining = [part for part in expanded if id(part) not in used_ids]
            if not remaining:
                break
            free_width = usable_width - _strip_total_width(stacks, kerf) - (kerf if stacks else 0.0)
            if free_width <= EPS:
                break
            candidate_part = next((part for part in remaining if any(v.width <= free_width + EPS and v.height <= usable_height + EPS for v in _part_variants(part, stock))), None)
            if candidate_part is None:
                break
            variant_options = [
                variant
                for variant in _part_variants(candidate_part, stock)
                if variant.width <= free_width + EPS and variant.height <= usable_height + EPS
            ]
            if not variant_options:
                break
            first_variant = min(variant_options, key=lambda item: (item.width, item.height, item.rotated))
            stack = _StripStack(first_variant.width)
            for part in list(remaining):
                compatible = [
                    variant
                    for variant in _part_variants(part, stock)
                    if abs(variant.width - stack.width) < EPS and stack.can_add(variant, usable_height, kerf)
                ]
                if not compatible:
                    continue
                variant = min(compatible, key=lambda item: (item.height, item.rotated))
                stack.add(variant, kerf)
                used_ids.add(id(part))
            if stack.variants:
                stacks.append(stack)
            else:
                break

        if _strip_total_width(stacks, kerf) > usable_width + EPS:
            continue
        ordered_stacks = _best_stack_order_for_manufacturing(stock, stacks, kerf, margin)
        layout = _draw_strip_sheet(stock, 1, ordered_stacks, kerf, margin)
        unplaced = [part for part in expanded if id(part) not in {id(placement.part) for placement in layout.parts}]
        if best_layout is None:
            best_layout = layout
            best_unplaced = unplaced
            best_message = f"mixed orientation group {group_parts[0].name}: {wide_count} wide + {len(group_parts) - wide_count} tall"
            continue
        current_key = (len(unplaced), layout.used_width, layout.used_width * layout.used_height, -layout.used_area)
        best_key = (len(best_unplaced), best_layout.used_width, best_layout.used_width * best_layout.used_height, -best_layout.used_area)
        if current_key < best_key:
            best_layout = layout
            best_unplaced = unplaced
            best_message = f"mixed orientation group {group_parts[0].name}: {wide_count} wide + {len(group_parts) - wide_count} tall"

    if best_layout is None:
        return None
    used_area = best_layout.used_area
    total_area = best_layout.consumed_area
    result = OptimizationResult(
        job_type="sheet",
        algorithm="Mixed Orientation Strip Candidate",
        sheet_layouts=[best_layout],
        unplaced_sheet_parts=best_unplaced,
        total_cost=best_layout.stock.price,
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        messages=[
            f"Selected packing candidate: mixed orientation strip / {orientation_name}",
            best_message,
        ],
    )
    annotate_result_metrics(result, kerf, min_reusable_size)
    return result


def _build_mixed_orientation_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = False,
) -> list[OptimizationResult]:
    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        candidate = _build_mixed_orientation_candidate_for_stock(oriented_stock, parts, kerf, margin, orientation_name, min_reusable_size)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _best_bottom_strip_plan(
    group_parts: list[SheetPart],
    stock: SheetStock,
    usable_width: float,
    usable_height: float,
    kerf: float,
) -> tuple[_PartVariant, _PartVariant, int, int, int] | None:
    if len(group_parts) < 2:
        return None
    variants = _part_variants(group_parts[0], stock)
    if len(variants) < 2:
        return None
    tall = max(variants, key=lambda item: (item.height, -item.width))
    wide = max(variants, key=lambda item: (item.width, -item.height))
    if tall.height <= wide.height + EPS or wide.width <= tall.width + EPS:
        return None
    if tall.width > usable_width + EPS or tall.height > usable_height + EPS:
        return None
    if wide.width > usable_width + EPS or wide.height > usable_height + EPS:
        return None

    best: tuple[int, int, int, float] | None = None
    best_score: tuple[int, float, int] | None = None
    max_rows = int((usable_height + kerf + EPS) // (wide.height + kerf))
    for bottom_rows in range(1, max_rows + 1):
        bottom_height = bottom_rows * wide.height + max(0, bottom_rows - 1) * kerf
        top_height = usable_height - bottom_height - kerf
        if top_height + EPS < tall.height:
            continue
            
        top_capacity = int((usable_width + kerf + EPS) // (tall.width + kerf))
        bottom_cols = int((usable_width + kerf + EPS) // (wide.width + kerf))
        bottom_capacity = bottom_rows * bottom_cols
        
        max_possible_placed = min(len(group_parts), top_capacity + bottom_capacity)
        
        for placed in range(1, max_possible_placed + 1):
            min_bottom = max(0, placed - top_capacity)
            max_bottom = min(placed, bottom_capacity)
            
            for bottom_count in range(min_bottom, max_bottom + 1):
                top_count = placed - bottom_count
                top_width = top_count * tall.width + max(0, top_count - 1) * kerf if top_count > 0 else 0.0
                
                if bottom_count > 0:
                    b_cols_used = (bottom_count + bottom_rows - 1) // bottom_rows
                    bottom_width = b_cols_used * wide.width + max(0, b_cols_used - 1) * kerf
                else:
                    bottom_width = 0.0
                    
                used_width = max(top_width, bottom_width)
                if bottom_count == 0:
                    continue
                
                score = (placed, -used_width, bottom_count)
                if best is None or best_score is None or score > best_score:
                    best = (placed, bottom_count, bottom_rows, used_width)
                    best_score = score

    if best is None:
        return None
    placed, bottom_count, bottom_rows, used_width = best
    
    top_only_capacity = int((usable_width + kerf + EPS) // (tall.width + kerf))
    if placed <= top_only_capacity:
        top_only_width = placed * tall.width + max(0, placed - 1) * kerf
        if used_width >= top_only_width - EPS:
            return None

    top_count = placed - bottom_count
    return tall, wide, top_count, bottom_count, bottom_rows


def _build_sport_bottom_strip_candidate_for_stock(
    stock_items: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    orientation_name: str,
    min_reusable_size: float,
) -> OptimizationResult | None:
    available_stock = _expand_stock(stock_items)
    if not available_stock:
        return None
    remaining = _expand_parts(parts)
    layouts: list[SheetLayout] = []
    messages: list[str] = [f"Selected packing candidate: sport bottom strip / {orientation_name}"]
    used_bottom_plan = False

    while remaining and available_stock:
        stock = available_stock.pop(0)
        usable_width = stock.width - margin * 2
        usable_height = stock.height - margin * 2
        grouped: dict[tuple[str, float, float], list[SheetPart]] = {}
        for part in remaining:
            grouped.setdefault((part.name, round(part.width, 4), round(part.height, 4)), []).append(part)

        best_choice: tuple[tuple[int, int, float], list[SheetPart], tuple[_PartVariant, _PartVariant, int, int, int]] | None = None
        for group_parts in grouped.values():
            plan = _best_bottom_strip_plan(group_parts, stock, usable_width, usable_height, kerf)
            if plan is None:
                continue
            tall, wide, top_count, bottom_count, bottom_rows = plan
            placed = top_count + bottom_count
            score = (placed, bottom_count, tall.width * tall.height)
            if best_choice is None or score > best_choice[0]:
                best_choice = (score, group_parts, plan)

        if best_choice is None:
            available_stock.insert(0, stock)
            break

        _, group_parts, (tall, wide, top_count, bottom_count, bottom_rows) = best_choice
        layout = SheetLayout(stock=stock, sheet_index=len(layouts) + 1)
        used_ids: set[int] = set()
        bottom_height = bottom_rows * wide.height + max(0, bottom_rows - 1) * kerf
        top_height = usable_height - bottom_height - kerf

        x = margin
        for part in group_parts[:top_count]:
            layout.parts.append(PlacedSheetPart(part, x, margin, tall.width, tall.height, tall.rotated))
            used_ids.add(id(part))
            x += tall.width + kerf

        bottom_y = margin + top_height + kerf
        bottom_index = 0
        bottom_cols = int((usable_width + kerf) // (wide.width + kerf))
        for part in group_parts[top_count : top_count + bottom_count]:
            row = bottom_index // max(1, bottom_cols)
            col = bottom_index % max(1, bottom_cols)
            px = margin + col * (wide.width + kerf)
            py = bottom_y + row * (wide.height + kerf)
            layout.parts.append(PlacedSheetPart(part, px, py, wide.width, wide.height, wide.rotated))
            used_ids.add(id(part))
            bottom_index += 1

        if layout.parts:
            used_bottom_plan = True
            layout.vertical_segments = [
                {"index": 1, "x": round(margin, 3), "width": round(layout.used_width - margin, 3), "right": round(layout.used_width, 3)}
            ]
            layouts.append(layout)
            messages.append(
                f"bottom strip sheet {layout.sheet_index}: {top_count}x {tall.width:.0f}x{tall.height:.0f} + "
                f"{bottom_count}x {wide.width:.0f}x{wide.height:.0f}"
            )
        remaining = [part for part in remaining if id(part) not in used_ids]
        remaining = _recover_internal_fillers(
            layout,
            remaining,
            kerf,
            margin,
            messages if os.environ.get("SIEKACZ_DEBUG_CANDIDATES") else None,
        )
        remaining = _recover_layout_right_strip(
            layout,
            remaining,
            kerf,
            margin,
            min_reusable_size,
            messages if os.environ.get("SIEKACZ_DEBUG_CANDIDATES") else None,
        )

    if remaining and available_stock:
        fallback = _build_result(available_stock, remaining, kerf, margin, "minimize_waste", _DEFAULT_STRATEGY)
        offset = len(layouts)
        for layout in fallback.sheet_layouts:
            layout.sheet_index += offset
        layouts.extend(fallback.sheet_layouts)
        remaining = fallback.unplaced_sheet_parts
        messages.extend(fallback.messages[:3])

    if not used_bottom_plan:
        return None
    if not layouts:
        return None
    used_area = sum(layout.used_area for layout in layouts)
    total_area = sum(layout.consumed_area for layout in layouts)
    result = OptimizationResult(
        job_type="sheet",
        algorithm="Sport Bottom Strip Candidate",
        sheet_layouts=layouts,
        unplaced_sheet_parts=remaining,
        total_cost=sum(layout.stock.price for layout in layouts),
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        messages=messages,
    )
    annotate_result_metrics(result, kerf, min_reusable_size)
    return result


def _build_sport_bottom_strip_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    min_reusable_size: float,
    include_rotated_stock: bool = False,
) -> list[OptimizationResult]:
    candidates: list[OptimizationResult] = []
    for orientation_name, oriented_stock in _stock_orientation_sets(stock):
        if not include_rotated_stock and orientation_name != "stock 0deg":
            continue
        candidate = _build_sport_bottom_strip_candidate_for_stock(
            oriented_stock,
            parts,
            kerf,
            margin,
            orientation_name,
            min_reusable_size,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _strategy_set() -> list[_Strategy]:
    preferred = [
        _Strategy("area descending / narrow bands", "area_desc", "narrow"),
        _Strategy("longest side descending / narrow bands", "longest_desc", "narrow"),
        _Strategy("width descending / original orientation", "width_desc", "original"),
        _Strategy("height descending / narrow bands", "height_desc", "narrow"),
        _Strategy("grouped by similar width / original orientation", "similar_width", "original"),
        _Strategy("grouped by similar height / narrow bands", "similar_height", "narrow"),
        _Strategy("width group by area / original orientation", "width_group_area", "original"),
        _Strategy("height ascending consolidation / original orientation", "height_asc", "original"),
    ]
    orders = [
        ("difficulty scoring", "difficulty_desc"),
        ("constrained first", "constrained_desc"),
        ("long thin first", "long_thin_desc"),
        ("strategic dimension first", "strategic_desc"),
        ("filler last by area", "filler_last_area"),
        ("filler last by longest side", "filler_last_longest"),
        ("fewest positions first", "positions_asc"),
        ("area descending", "area_desc"),
        ("longest side descending", "longest_desc"),
        ("width descending", "width_desc"),
        ("height descending", "height_desc"),
        ("similar width", "similar_width"),
        ("similar height", "similar_height"),
        ("width group area", "width_group_area"),
        ("height ascending", "height_asc"),
    ]
    anchors = [
        ("narrow bands", "narrow"),
        ("original orientation", "original"),
        ("wide bands", "wide"),
        ("low rows", "low"),
    ]
    strategies = preferred[:]
    seen = {(strategy.order, strategy.anchor) for strategy in strategies}
    for order_label, order in orders:
        for anchor_label, anchor in anchors:
            key = (order, anchor)
            if key in seen:
                continue
            seen.add(key)
            strategies.append(_Strategy(f"{order_label} / {anchor_label}", order, anchor))
    return strategies


def _build_strategy_candidates(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
) -> list[OptimizationResult]:
    strategies = _strategy_set()
    total_units = sum(max(0, int(part.quantity)) for part in parts)
    filler_units = 0
    representative_stock = stock[0] if stock else None
    if representative_stock is not None:
        filler_units = sum(
            max(0, int(part.quantity))
            for part in parts
            if _is_small_filler_part(part, representative_stock)
        )
    if total_units >= 500 and filler_units >= total_units * 0.65:
        focused_orders = {
            "difficulty_desc",
            "constrained_desc",
            "long_thin_desc",
            "strategic_desc",
            "filler_last_area",
            "filler_last_longest",
            "area_desc",
            "width_desc",
            "height_desc",
            "similar_width",
            "width_group_area",
        }
        focused_anchors = {"narrow", "original"}
        strategies = [
            strategy for index, strategy in enumerate(strategies)
            if index < 8 or (strategy.order in focused_orders and strategy.anchor in focused_anchors)
        ]
    force_parallel = os.environ.get("SIEKACZ_PARALLEL_STRATEGIES") == "1"
    disable_parallel = (
        os.environ.get("SIEKACZ_DISABLE_VERTICAL_CANDIDATE_POOL") == "1"
        or os.environ.get("SIEKACZ_IN_ENSEMBLE") == "1"
    )
    should_parallel = (
        not disable_parallel
        and len(strategies) > 4
        and (force_parallel or total_units >= 1500)
    )
    if should_parallel:
        return build_strategy_candidates_parallel(stock, parts, kerf, margin, mode, strategies)
    return [_build_result(stock, parts, kerf, margin, mode, strategy) for strategy in strategies]


def _build_result(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float = 3.0,
    margin: float = 0.0,
    mode: str = "minimize_waste",
    strategy: _Strategy = _DEFAULT_STRATEGY,
) -> OptimizationResult:
    layouts: list[SheetLayout] = []
    segments_by_layout: dict[int, list[_Segment]] = {}
    available_stock = _expand_stock(stock)
    unplaced: list[SheetPart] = []
    representative_stock = available_stock[0] if available_stock else (stock[0] if stock else None)

    for part in _ordered_parts(_expand_parts(parts), strategy, representative_stock, kerf, margin):
        if _place_existing(layouts, segments_by_layout, part, kerf, strategy):
            continue
        if _place_new_segment(layouts, segments_by_layout, part, kerf, margin, strategy):
            continue
        if _open_new_sheet(layouts, segments_by_layout, available_stock, part, kerf, margin, strategy):
            continue
        unplaced.append(part)

    for layout in layouts:
        segments = segments_by_layout[id(layout)]
        if len(layout.parts) <= 240:
            _compress_layout(layout, segments, kerf)
        _attach_segment_metadata(layout, segments)

    used_area = sum(layout.used_area for layout in layouts)
    total_area = sum(layout.consumed_area for layout in layouts)
    messages = _debug_messages(layouts, segments_by_layout)
    messages.insert(0, f"Selected packing candidate: {strategy.name}")
    if unplaced:
        messages.append(f"{len(unplaced)} sheet part(s) could not be placed with available stock.")

    result = OptimizationResult(
        job_type="sheet",
        algorithm="Vertical Segmented Guillotine",
        sheet_layouts=layouts,
        unplaced_sheet_parts=unplaced,
        total_cost=sum(layout.stock.price for layout in layouts),
        waste=max(0.0, total_area - used_area),
        utilization=used_area / total_area * 100.0 if total_area else 0.0,
        messages=messages,
    )
    # Fallback to MIN_REUSABLE_REMNANT_MM (80.0) if stock doesn't specify, or use the stock's min offcut.
    min_size = max(80.0, min((item.min_offcut_width for item in stock), default=80.0))
    annotate_result_metrics(result, kerf, min_size)
    result.reusable_offcuts = build_reusable_offcuts(result.sheet_layouts, min_size)
    return result


def optimize_2d_vertical_segmented(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float = 3.0,
    margin: float = 0.0,
    mode: str = "minimize_waste",
    min_reusable_size: float = 200.0,
    cutting_mode: str = "hybrid",
    optimization_mode: str = "comfort",
) -> OptimizationResult:
    _difficulty_cache.clear()
    # Canonicalize stock orientations up front so the optimizer sees the same
    # input regardless of which way the user typed the dimensions. Without
    # this, _build_result paths that consume the raw `stock` list directly
    # produce a different layout for (W, H) vs (H, W) even when allow_rotation
    # is True. Candidates that use _stock_orientation_sets internally were
    # already symmetric, but this guarantees the rest is too.
    stock = [_canonicalize_stock_orientation(item) for item in stock]
    unmatched_parts = [
        part
        for part in parts
        if not any(
            materials_are_compatible(item.material, part.material) and abs(item.thickness - part.thickness) < EPS
            for item in stock
        )
    ]
    if unmatched_parts:
        details = ", ".join(
            f"{part.material or 'bez nazwy'} gr. {part.thickness:g} mm"
            for part in unmatched_parts
        )
        return OptimizationResult(
            job_type="sheet",
            algorithm="Vertical Segmented Guillotine",
            unplaced_sheet_parts=_expand_parts(parts),
            messages=[f"Brak zgodnej płyty dla formatek: {details}."],
        )
    candidates: list[OptimizationResult] = []
    production_mode = (cutting_mode or "hybrid").lower() != "free"
    drive_mode = (optimization_mode or "comfort").lower()
    candidates.extend(
        _build_mixed_orientation_candidates(
            stock,
            parts,
            kerf,
            margin,
            min_reusable_size,
            include_rotated_stock=True,
        )
    )
    candidates.extend(
        _build_orientation_split_candidates(
            stock,
            parts,
            kerf,
            margin,
            min_reusable_size,
            include_rotated_stock=True,
        )
    )
    candidates.extend(
        _build_uniform_grid_candidates(
            stock,
            parts,
            kerf,
            margin,
            min_reusable_size,
            include_rotated_stock=True,
        )
    )
    candidates.extend(
        _build_repeated_mixed_grid_candidates(
            stock,
            parts,
            kerf,
            margin,
            min_reusable_size,
            include_rotated_stock=True,
        )
    )
    candidates.extend(
        _build_vertical_strip_candidates(
            stock,
            parts,
            kerf,
            margin,
            min_reusable_size,
            include_rotated_stock=True,
        )
    )
    candidates.extend(
        _build_horizontal_block_candidates(
            stock,
            parts,
            kerf,
            margin,
            min_reusable_size,
            include_rotated_stock=True,
        )
    )
    candidates.extend(_build_strategy_candidates(stock, parts, kerf, margin, mode))
    candidates.extend(
        _build_sport_bottom_strip_candidates(
            stock,
            parts,
            kerf,
            margin,
            min_reusable_size,
            include_rotated_stock=True,
        )
    )
    if not production_mode:
        try:
            from algorithms.two_d_guillotine import optimize_2d_guillotine
            from algorithms.two_d_maxrects import optimize_2d_maxrects

            for algorithm in (optimize_2d_maxrects, optimize_2d_guillotine):
                candidate = algorithm(stock, parts, kerf, margin, mode)
                candidate.algorithm = f"{candidate.algorithm} candidate"
                annotate_result_metrics(candidate, kerf, min_reusable_size)
                candidates.append(candidate)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning("Non-guillotine fallback candidates failed: %s", exc)

    debug_enabled = bool(os.environ.get("SIEKACZ_DEBUG_CANDIDATES"))
    for candidate in candidates:
        _recover_candidate_waste(candidate, kerf, margin, min_reusable_size, debug=debug_enabled)
        _repair_candidate_fillers(candidate, kerf, margin, min_reusable_size, debug=debug_enabled)
        _consolidate_sparse_sheets(candidate, kerf, margin, debug=debug_enabled)

    # === Non-filler-first candidates ===
    # Build strip candidates using ONLY structural (non-filler) parts, then run
    # waste-first refill to place fillers into the waste areas of the current sheet
    # (e.g. B(10×10) filling waste under C(400×550)).  Only the overflow that genuinely
    # cannot fit in any waste area stays as unplaced and flows to missing-sheet passes.
    # Repair is NOT applied — parts that made it into waste areas are already in good positions.
    _ref_stock_nf = stock[0] if stock else None
    if _ref_stock_nf is not None:
        _non_filler_parts_nf = [p for p in parts if not _is_small_filler_part(p, _ref_stock_nf)]
        _filler_only_parts_nf = [p for p in parts if _is_small_filler_part(p, _ref_stock_nf)]
        if _filler_only_parts_nf and _non_filler_parts_nf and len(_non_filler_parts_nf) < len(parts):
            _nf_strip_cands = _build_vertical_strip_candidates(
                stock, _non_filler_parts_nf, kerf, margin, min_reusable_size, include_rotated_stock=True,
            )
            _filler_expanded_nf = _expand_parts(_filler_only_parts_nf)
            for _nf_cand in _nf_strip_cands:
                # Add fillers as unplaced (expanded so each copy is tracked individually),
                # then immediately try to fill current-sheet waste regions (class 1/2:
                # below C, right residual strip, etc.) with them.
                _nf_cand.unplaced_sheet_parts = list(_nf_cand.unplaced_sheet_parts) + _filler_expanded_nf
                _nf_cand.algorithm = _nf_cand.algorithm + " [non-filler-first]"
                # Waste-first refill: place as many fillers as possible in waste areas of
                # the structural layout before scoring.  Overflow stays in unplaced and
                # flows to the missing-sheet optimizer.
                _recover_candidate_waste(_nf_cand, kerf, margin, min_reusable_size, debug=debug_enabled)
                _consolidate_sparse_sheets(_nf_cand, kerf, margin, debug=debug_enabled)
            candidates.extend(_nf_strip_cands)

    # T2-5: Iterative reorder rescue pass.  Bounded by both `parts` count and
    # attempt count to keep stress scenarios fast.  Only runs when the *best*
    # candidate so far still has structural parts unplaced — i.e. the heuristics
    # are clearly stuck in a local minimum.  Limits:
    #   - skip entirely if parts count >= _T2_5_PART_BUDGET (rescue is for hard
    #     small/medium scenarios; for huge BOMs the strategy diversity already
    #     in the base pass is enough)
    #   - 2 swap perturbations max
    #   - one strategy per perturbation (difficulty_desc / narrow)
    #   - early-exit on first successful structural placement
    _T2_5_PART_BUDGET = 60
    if (
        candidates
        and 4 <= len(parts) < _T2_5_PART_BUDGET
        and min((_count_unplaced_non_fillers(c) for c in candidates), default=0) > 0
    ):
        rescue_rng = _random.Random(0xC07_5EED)  # noqa: S311 — repeatability not security
        rescue_strategy = _Strategy("T2-5 reorder rescue", "difficulty_desc", "narrow")
        initial_count = len(candidates)
        for attempt in range(2):
            perturbed = list(parts)
            half = max(1, len(perturbed) // 2)
            i = rescue_rng.randrange(0, half)
            j = rescue_rng.randrange(half, len(perturbed))
            perturbed[i], perturbed[j] = perturbed[j], perturbed[i]
            cand = _build_result(stock, perturbed, kerf, margin, mode, rescue_strategy)
            cand.algorithm = f"{cand.algorithm} [T2-5 reorder #{attempt + 1}]"
            _recover_candidate_waste(cand, kerf, margin, min_reusable_size, debug=debug_enabled)
            _repair_candidate_fillers(cand, kerf, margin, min_reusable_size, debug=debug_enabled)
            candidates.append(cand)
            if _count_unplaced_non_fillers(cand) == 0:
                if debug_enabled:
                    cand.messages.append(
                        f"T2-5 reorder rescue placed all structural parts on attempt {attempt + 1}"
                    )
                break
        if debug_enabled:
            added = len(candidates) - initial_count
            if added:
                candidates[-1].messages.append(
                    f"T2-5 reorder rescue added {added} extra candidates"
                )

    # Invoke the bounded exhaustive topology search only as a rescue.  Running
    # it for every small, already-complete order adds cost without improving the
    # production result; it is needed precisely when all regular candidates
    # leave at least one required formatka unplaced.
    if (
        sum(max(0, int(part.quantity)) for part in parts) <= 8
        and candidates
        and not any(not candidate.unplaced_sheet_parts for candidate in candidates)
    ):
        candidates.extend(
            _build_exact_small_guillotine_candidates(
                stock,
                parts,
                kerf,
                margin,
                min_reusable_size,
                include_rotated_stock=True,
            )
        )

    for candidate in candidates:
        annotate_guillotine_result(candidate, kerf, min_reusable_size)

    # Candidate pruning uses generic remnant metrics. A mixed-orientation
    # candidate needs protection only when it improves the primary production
    # objectives over every uniform candidate. This keeps compact mixed layouts
    # alive without letting them displace a genuinely better uniform cut plan.
    pre_prune_pool = candidates
    if production_mode:
        feasible_before_prune = [
            candidate
            for candidate in candidates
            if all(layout.is_guillotine_feasible for layout in candidate.sheet_layouts)
        ]
        if feasible_before_prune:
            pre_prune_pool = feasible_before_prune
    score_before_prune = _sport_score if drive_mode == "sport" else _comfort_score
    mixed_candidates = [
        candidate
        for candidate in pre_prune_pool
        if _mixed_orientation_group_penalty(candidate) > 0
    ]
    uniform_candidates = [
        candidate
        for candidate in pre_prune_pool
        if _mixed_orientation_group_penalty(candidate) == 0
    ]

    def protection_priority(candidate: OptimizationResult) -> tuple[float, ...]:
        return (
            float(sum(1 for layout in candidate.sheet_layouts if layout.parts and not layout.is_guillotine_feasible)),
            float(_count_unplaced_non_fillers(candidate)),
            float(len(candidate.unplaced_sheet_parts)),
            float(len(candidate.sheet_layouts)),
            _total_saved_used_length(candidate),
            _total_saved_consumed_area(candidate),
        )

    def protection_key(candidate: OptimizationResult) -> tuple[object, ...]:
        return (
            protection_priority(candidate),
            score_before_prune(candidate, kerf, min_reusable_size),
            _long_axis_tiebreak(candidate),
        )

    best_mixed = min(mixed_candidates, key=protection_key) if mixed_candidates else None
    best_uniform = min(uniform_candidates, key=protection_key) if uniform_candidates else None
    protected_candidate = (
        best_mixed
        if best_mixed is not None
        and (best_uniform is None or protection_priority(best_mixed) < protection_priority(best_uniform))
        else None
    )

    generated_count = len(candidates)
    candidates, pruned_count = prune_dominated_candidates(
        candidates,
        kerf,
        min_reusable_size,
        _count_unplaced_non_fillers,
        _total_saved_used_length,
        _total_saved_consumed_area,
    )
    if protected_candidate is not None and not any(candidate is protected_candidate for candidate in candidates):
        candidates.append(protected_candidate)
        pruned_count = max(0, pruned_count - 1)

    candidate_debug = [
        _candidate_debug_line(f"{index + 1}/{candidate.algorithm}", candidate, kerf, min_reusable_size)
        for index, candidate in enumerate(candidates)
    ]

    selectable = candidates
    if production_mode:
        feasible_layout_candidates = [
            candidate
            for candidate in candidates
            if all(layout.is_guillotine_feasible for layout in candidate.sheet_layouts)
        ]
        # Use minimum non-filler unplaced instead of "zero unplaced" so that
        # non-filler-first candidates (fillers deliberately deferred to missing-sheet
        # waste bands) are not excluded from the selectable pool.  When a non-filler-first
        # candidate places the same number of structural parts as zero-unplaced regulars,
        # _min_nf_unplaced==0 and both sets are included; the scorer's split penalty
        # (non-filler=1e12, filler=1e9) then picks the better one.
        feasible_candidates = feasible_layout_candidates
        if drive_mode == "sport":
            selectable = feasible_candidates or feasible_layout_candidates or candidates
        else:
            perfect_strip_candidates = [
                candidate
                for candidate in feasible_candidates
                if (
                    "strip candidate" in candidate.algorithm.lower()
                    or "mandatory vertical strip" in candidate.algorithm.lower()
                    or "uniform grid" in candidate.algorithm.lower()
                    or "orientation split" in candidate.algorithm.lower()
                    or "non-filler-first" in candidate.algorithm.lower()
                    or "horizontal block" in candidate.algorithm.lower()
                )
            ]
            selectable = perfect_strip_candidates or feasible_candidates or feasible_layout_candidates or candidates
            # Comfort prefers clean, readable strips — but that preference must
            # not be allowed to pick a badly under-filled layout over a clearly
            # better-packed one.  If the best non-strip feasible candidate beats
            # the best clean-strip candidate on the dominant objectives (fewer
            # unplaced / fewer sheets, or the same but a much shorter real used
            # length), fall back to the full feasible pool so the denser layout
            # can win.  Guards the "38%-full overflow sheet vs 88%-full" case
            # where the strip filter otherwise discards the good candidate.
            if perfect_strip_candidates and feasible_candidates and (
                len(perfect_strip_candidates) < len(feasible_candidates)
            ):
                best_strip = min(
                    perfect_strip_candidates,
                    key=lambda c: _comfort_score(c, kerf, min_reusable_size),
                )
                best_any = min(
                    feasible_candidates,
                    key=lambda c: _comfort_score(c, kerf, min_reusable_size),
                )
                if best_any is not best_strip:
                    strip_primary = _comfort_score(best_strip, kerf, min_reusable_size)[:3]
                    any_primary = _comfort_score(best_any, kerf, min_reusable_size)[:3]
                    strip_len = _total_saved_used_length(best_strip)
                    dense_len = _total_saved_used_length(best_any)
                    if any_primary < strip_primary or (
                        any_primary == strip_primary and dense_len <= strip_len * 0.90
                    ) or (
                        any_primary == strip_primary
                        and dense_len <= strip_len + EPS
                        and _long_axis_tiebreak(best_any) < _long_axis_tiebreak(best_strip)
                    ):
                        selectable = feasible_candidates

    if protected_candidate is not None and not any(candidate is protected_candidate for candidate in selectable):
        selectable = [*selectable, protected_candidate]

    if drive_mode == "sport":
        selected = min(
            enumerate(selectable),
            key=lambda item: (_sport_score(item[1], kerf, min_reusable_size), _long_axis_tiebreak(item[1]), item[0]),
        )
    else:
        selected = min(
            enumerate(selectable),
            key=lambda item: (_comfort_score(item[1], kerf, min_reusable_size), _long_axis_tiebreak(item[1]), item[0]),
        )
    best = selected[1]
    # Post-winner aggressive consolidation: try to eliminate any sheet with up to
    # 20 parts by moving them into the internal waste AND the right-side residual
    # strip of other sheets.  This catches the common case where structural (non-
    # filler) parts end up on separate overflow sheets that could all be absorbed
    # into the right-side waste of denser sheets (class-3 placement, which extends
    # the saved axis but saves an entire sheet — a worthwhile trade-off).
    _consolidate_sparse_sheets(
        best, kerf, margin, min_reusable_size,
        max_part_count=20, try_right_strip=True, debug=debug_enabled,
    )
    # Close avoidable vertical gaps inside columns of each sheet — purely
    # cosmetic shift-up that eliminates ugly empty rectangles caused by
    # mixed-orientation strips.  Compaction is rolled back per-sheet if it
    # accidentally breaks guillotine feasibility.  Skipped for very large
    # layouts (N>120 parts) to keep stress-test time within bounds — the
    # gaps these visual fixes target only appear on small/medium sheets.
    from algorithms.guillotine_technology import validate_guillotine_feasibility as _validate_for_compact
    for _layout in best.sheet_layouts:
        if len(_layout.parts) > 120:
            continue
        _saved_positions = [(p, p.y) for p in _layout.parts]
        if _compact_column_gaps(_layout, kerf):
            _post_ok, _ = _validate_for_compact(_layout, kerf, allow_slicing_tree=False)
            if not _post_ok:
                for _p, _orig_y in _saved_positions:
                    _p.y = _orig_y
    winner_number = next((index + 1 for index, candidate in enumerate(candidates) if candidate is best), selected[0] + 1)
    winner_score = _sport_score(best, kerf, min_reusable_size) if drive_mode == "sport" else _comfort_score(best, kerf, min_reusable_size)
    annotate_guillotine_result(best, kerf, min_reusable_size)
    best.algorithm = "Vertical Segmented Guillotine - SPORT" if drive_mode == "sport" else "Vertical Segmented Guillotine - COMFORT"
    if not production_mode:
        best.messages.append(
            "Ten tryb może dać wyższe wykorzystanie powierzchni, ale układ może być trudny lub niemożliwy do wykonania na pile. "
            "Do produkcji zalecany jest tryb Hybrid / Guillotine."
        )
    else:
        if all(layout.is_guillotine_feasible for layout in best.sheet_layouts):
            if drive_mode == "sport":
                best.messages.append("SPORT: wybrano agresywniejszy układ gilotynowy nastawiony na mniejszą liczbę płyt.")
            else:
                best.messages.append("COMFORT: wybrano układ technologicznie wygodny, z prostymi pasami i czytelną kolejnością cięcia.")
        else:
            best.messages.append("Uwaga: nie udało się zbudować pełnego drzewa cięć gilotynowych dla wszystkich płyt.")
    if debug_enabled:
        warnings = [
            getattr(layout, "technology_warning", "")
            for candidate in candidates
            for layout in candidate.sheet_layouts
            if layout.parts and not getattr(layout, "is_guillotine_feasible", False)
        ]
        collision_rejected = sum("nachodzi" in warning for warning in warnings)
        kerf_rejected = sum("rzaz" in warning for warning in warnings)
        guillotine_rejected = len(warnings)
        refill_improved = sum(
            1
            for candidate in candidates
            for message in candidate.messages
            if "waste recovery" in message or "filler placement part=" in message
        )
        repair_improved = sum(
            1
            for candidate in candidates
            for message in candidate.messages
            if "repair pass improved" in message
        )
        best.messages.extend(
            [
                f"Optimizer debug: generated candidates={generated_count}",
                f"Optimizer debug: pruned dominated candidates={pruned_count}; remaining={len(candidates)}",
                (
                    "Optimizer debug rejection summary: "
                    f"collisions={collision_rejected}, kerf={kerf_rejected}, "
                    f"guillotine={guillotine_rejected}, "
                    f"wasteFirstRefillPlacements={refill_improved}, "
                    f"repairPassImproved={repair_improved}"
                ),
                *_part_difficulty_debug_lines(stock, parts, kerf, margin),
                (
                    f"Winner candidate={winner_number}/{len(candidates)} "
                    f"mode={drive_mode} score={winner_score}"
                ),
                *_winner_geometry_debug_lines(best, kerf),
            ]
        )
        if best.unplaced_sheet_parts:
            best.messages.append(
                f"New/missing sheet reason: {len(best.unplaced_sheet_parts)} parts did not fit any legal residual region after waste recovery."
            )
        best.messages.extend(["Candidate debug:", *candidate_debug])
        best.messages.extend(_rejected_candidate_debug_lines(candidates, best, kerf, min_reusable_size))
        best.messages.append(
            "Offcut score: "
            f"largest reusable={best.largest_reusable_offcut_area:.0f} mm2, "
            f"total reusable={best.total_reusable_offcut_area:.0f} mm2, "
            f"fragmentation={best.fragmentation_score:.0f}"
        )
    best.messages.extend(layout.cutting_explanation for layout in best.sheet_layouts if layout.cutting_explanation)
    return best

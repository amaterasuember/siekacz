from __future__ import annotations

"""Operator-facing cutting metrics: saw travel, cut count, area and a realistic
time estimate broken down per formatka and per whole job.

Time model (sliding panel / table saw — *practical shop throughput*, not the raw
blade stroke).  Verified against published panel-saw feed rates (6–22 m/min for
chipboard / melamine / MDF); we use a conservative effective feed and add the
handling overhead that actually dominates real cutting time:

    time = saw_travel / feed                      # blade in the cut
         + cuts        * reposition_per_cut        # set fence, push stroke
         + pieces      * handling_per_piece         # pick / align / offload
         + rotations   * rotation_per_piece         # turn a panel 90°

All constants are tunable here in one place.
"""

import math
from dataclasses import dataclass, field

from core.models import OptimizationResult, PlacedSheetPart, SheetLayout
from algorithms.layout_grouping import group_identical_layouts
from algorithms.layout_scoring import collect_free_rectangles, rect_area, reusable_rectangles

# ── Tunable time constants ────────────────────────────────────────────────────
FEED_M_PER_MIN: float = 12.0          # effective through-cut feed (≈200 mm/s)
REPOSITION_S_PER_CUT: float = 3.0     # fence set + push per cut line
HANDLING_S_PER_PIECE: float = 4.0     # pick / align / offload each finished piece
ROTATION_S_PER_PIECE: float = 3.0     # extra for turning a panel 90°

_FEED_MM_PER_S = FEED_M_PER_MIN * 1000.0 / 60.0
MIN_REUSABLE_REMNANT_MM: float = 80.0


@dataclass
class FormatCutMetrics:
    name: str
    width: float            # nominal formatka width (mm)
    height: float           # nominal formatka height (mm)
    material: str = ""      # board material/group this format belongs to
    thickness: float = 0.0
    pieces: int = 0
    rotated: int = 0
    cuts: int = 0
    saw_m: float = 0.0      # attributed saw travel (m)
    area_m2: float = 0.0    # net area of these pieces (m²)
    waste_m2: float = 0.0
    reusable_m2: float = 0.0
    gross_m2: float = 0.0
    # Effective supplier rate of the board(s) that actually produced this
    # format. It is carried from SheetStock.price, not from a stale UI rate.
    catalog_price_m2: float = 0.0
    time_s: float = 0.0     # attributed cutting time for this format (s)


@dataclass
class CutSummary:
    formats: list[FormatCutMetrics] = field(default_factory=list)
    total_pieces: int = 0
    total_rotated: int = 0
    total_cuts: int = 0
    total_saw_m: float = 0.0       # whole-job saw travel incl. shared rip cuts (m)
    total_area_m2: float = 0.0     # net pieces area (m²)
    total_board_area_m2: float = 0.0
    total_stock_area_m2: float = 0.0
    total_waste_m2: float = 0.0
    total_reusable_m2: float = 0.0
    total_time_s: float = 0.0


def _orig_dims(placement: PlacedSheetPart) -> tuple[float, float]:
    part = placement.part
    return float(getattr(part, "width", placement.width)), float(getattr(part, "height", placement.height))


def _iter_layouts(result: OptimizationResult) -> list[SheetLayout]:
    return list(result.sheet_layouts) + list(result.missing_sheet_layouts)


def _stack_time_multipliers(layouts: list[SheetLayout]) -> dict[int, float]:
    """Return how many machine passes each physical layout consumes.

    A stack only reduces time when its boards have the same cutting layout.
    Four identical boards in stacks of two therefore require two machine passes
    (factor 0.5 for every physical board), while material and piece counts stay
    unchanged.
    """
    multipliers: dict[int, float] = {}
    for group in group_identical_layouts(layouts):
        stack_size = max(1, int(getattr(group.representative.stock, "stack_size", 1) or 1))
        passes = math.ceil(group.count / stack_size)
        factor = passes / group.count
        for index in group.indices:
            multipliers[id(layouts[index])] = factor
    return multipliers


def _layout_stock_area_m2(layout: SheetLayout) -> float:
    return (float(layout.stock.width) * float(layout.stock.height)) / 1_000_000.0


def _layout_reusable_area_m2(layout: SheetLayout) -> float:
    if not layout.parts:
        return 0.0
    
    annotated_area = float(getattr(layout, "reusable_offcut_area", 0.0) or 0.0)
    if annotated_area > 0.0:
        return annotated_area / 1_000_000.0
        
    rects = list(getattr(layout, "offcuts", []) or [])
    min_size = max(
        MIN_REUSABLE_REMNANT_MM,
        max(0.0, float(getattr(layout.stock, "min_offcut_width", 0.0) or 0.0)),
        max(0.0, float(getattr(layout.stock, "min_offcut_height", 0.0) or 0.0)),
    )
    rects = reusable_rectangles(rects, min_size)
    if not rects:
        rects = reusable_rectangles(collect_free_rectangles(layout, 0.0, min_size), min_size)
    return sum(rect_area(rect) for rect in rects) / 1_000_000.0


def compute_cut_summary(result: OptimizationResult, feed_m_per_min: float | None = None) -> CutSummary:
    """Aggregate per-format and whole-job cutting metrics from a result."""
    if feed_m_per_min is None:
        feed_m_per_min = float(getattr(result, "saw_feed_m_per_min", FEED_M_PER_MIN) or FEED_M_PER_MIN)
    feed_mm_per_s = max(1.0, float(feed_m_per_min) * 1000.0 / 60.0)
    by_key: dict[tuple, FormatCutMetrics] = {}
    order: list[tuple] = []
    layouts = _iter_layouts(result)
    stack_multipliers = _stack_time_multipliers(layouts)
    weighted_by_key: dict[tuple, list[float]] = {}
    catalog_rate_by_key: dict[tuple, list[float]] = {}

    for layout in layouts:
        time_factor = stack_multipliers.get(id(layout), 1.0)
        for placement in layout.parts:
            ow, oh = _orig_dims(placement)
            name = getattr(placement.part, "name", "") or "—"
            material = getattr(placement.part, "material", "") or ""
            thickness = float(getattr(placement.part, "thickness", 0.0) or 0.0)
            key = (material, round(thickness, 3), name, round(ow, 1), round(oh, 1))
            metrics = by_key.get(key)
            if metrics is None:
                metrics = FormatCutMetrics(
                    name=name,
                    width=ow,
                    height=oh,
                    material=material,
                    thickness=thickness,
                )
                by_key[key] = metrics
                order.append(key)
            metrics.pieces += 1
            if placement.rotated:
                metrics.rotated += 1
            # Per-piece attribution: two edge cuts of (w + h) saw travel.
            metrics.cuts += 2
            metrics.saw_m += (placement.width + placement.height) / 1000.0
            metrics.area_m2 += (placement.width * placement.height) / 1_000_000.0
            nominal_width = float(getattr(layout.stock, "nominal_width", 0.0) or layout.stock.width)
            nominal_height = float(getattr(layout.stock, "nominal_height", 0.0) or layout.stock.height)
            board_area_m2 = (nominal_width * nominal_height) / 1_000_000.0
            board_rate = float(getattr(layout.stock, "price", 0.0) or 0.0) / board_area_m2 if board_area_m2 > 0 else 0.0
            if board_rate > 0:
                price_weight = catalog_rate_by_key.setdefault(key, [0.0, 0.0])
                part_area_m2 = (placement.width * placement.height) / 1_000_000.0
                price_weight[0] += board_rate * part_area_m2
                price_weight[1] += part_area_m2
            weighted = weighted_by_key.setdefault(key, [0.0, 0.0, 0.0, 0.0])
            weighted[0] += (placement.width + placement.height) / 1000.0 * time_factor
            weighted[1] += 2.0 * time_factor
            weighted[2] += time_factor
            weighted[3] += (1.0 if placement.rotated else 0.0) * time_factor

    summary = CutSummary(formats=[by_key[k] for k in order])
    for key, metrics in by_key.items():
        weighted_rate, weighted_area = catalog_rate_by_key.get(key, [0.0, 0.0])
        if weighted_area > 0:
            metrics.catalog_price_m2 = weighted_rate / weighted_area
    summary.total_pieces = sum(f.pieces for f in summary.formats)
    summary.total_rotated = sum(f.rotated for f in summary.formats)
    summary.total_area_m2 = sum(f.area_m2 for f in summary.formats)

    # Billing follows the panel-saw cut: the used X strip is consumed across
    # the whole board height. Space below a part inside that strip is customer
    # scrap; only the untouched tail beyond the strip remains in warehouse.
    used_layouts = [layout for layout in layouts if layout.parts]
    summary.total_stock_area_m2 = sum(_layout_stock_area_m2(layout) for layout in used_layouts)
    summary.total_board_area_m2 = sum(
        max(0.0, float(layout.consumed_area)) / 1_000_000.0
        for layout in used_layouts
    )
    summary.total_reusable_m2 = max(0.0, summary.total_stock_area_m2 - summary.total_board_area_m2)
    summary.total_waste_m2 = max(
        0.0,
        summary.total_board_area_m2 - summary.total_area_m2,
    )

    # Whole-job saw travel and cut count: prefer the layout-level totals that
    # already include the shared rip/cross boundary cuts (more accurate than the
    # per-piece attribution above), falling back to the attributed sum.
    layout_saw_mm = sum(
        getattr(layout, "total_cut_length", 0.0) * stack_multipliers.get(id(layout), 1.0)
        for layout in layouts
    )
    layout_cuts = sum(
        int(getattr(layout, "cut_count", 0)) * stack_multipliers.get(id(layout), 1.0)
        for layout in layouts
    )
    summary.total_saw_m = (layout_saw_mm / 1000.0) if layout_saw_mm > 0 else sum(values[0] for values in weighted_by_key.values())
    summary.total_cuts = int(round(layout_cuts)) if layout_cuts > 0 else int(round(sum(values[1] for values in weighted_by_key.values())))
    effective_pieces = sum(values[2] for values in weighted_by_key.values())
    effective_rotated = sum(values[3] for values in weighted_by_key.values())

    summary.total_time_s = estimate_job_time_seconds(
        saw_m=summary.total_saw_m,
        cuts=summary.total_cuts,
        pieces=effective_pieces,
        rotations=effective_rotated,
        feed_mm_per_s=feed_mm_per_s,
    )

    # Attribute scrap and reusable remnants proportionally to net area.  Gross
    # stays net + non-reusable scrap, while reusable_m2 is informational stock.
    net_total = summary.total_area_m2
    raw_saw_m = sum(values[0] for values in weighted_by_key.values())
    raw_cuts = sum(values[1] for values in weighted_by_key.values())
    
    cuts_remainders = []

    for idx, fmt in enumerate(summary.formats):
        share = (fmt.area_m2 / net_total) if net_total > 0 else 0.0
        fmt.waste_m2 = summary.total_waste_m2 * share
        fmt.reusable_m2 = summary.total_reusable_m2 * share
        fmt.gross_m2 = fmt.area_m2 + fmt.waste_m2

        weighted = weighted_by_key.get(order[idx], [0.0, 0.0, 0.0, 0.0])
        saw_share = (weighted[0] / raw_saw_m) if raw_saw_m > 0 else 0.0
        fmt.saw_m = summary.total_saw_m * saw_share
        
        cuts_exact = summary.total_cuts * ((weighted[1] / raw_cuts) if raw_cuts > 0 else 0.0)
        fmt.cuts = int(cuts_exact)
        cuts_remainders.append((cuts_exact - fmt.cuts, idx))
        
        # Per-format time attribution: independent estimate from this format's
        # scaled saw travel / cuts / pieces / rotations, which we will scale again.
        fmt.time_s = estimate_job_time_seconds(
            saw_m=fmt.saw_m,
            cuts=cuts_exact,
            pieces=weighted[2],
            rotations=weighted[3],
            feed_mm_per_s=feed_mm_per_s,
        )

    # Distribute remaining cuts rounding differences
    cuts_to_add = summary.total_cuts - sum(f.cuts for f in summary.formats)
    cuts_remainders.sort(reverse=True)
    for i in range(int(cuts_to_add)):
        idx = cuts_remainders[i][1]
        summary.formats[idx].cuts += 1
        
    # Scale time_s to perfectly match total_time_s
    raw_time_s = sum(f.time_s for f in summary.formats)
    for fmt in summary.formats:
        time_share = (fmt.time_s / raw_time_s) if raw_time_s > 0 else 0.0
        fmt.time_s = summary.total_time_s * time_share
    return summary


def estimate_job_time_seconds(
    saw_m: float,
    cuts: int,
    pieces: int,
    rotations: int,
    feed_mm_per_s: float = _FEED_MM_PER_S,
    reposition_s: float = REPOSITION_S_PER_CUT,
    handling_s: float = HANDLING_S_PER_PIECE,
    rotation_s: float = ROTATION_S_PER_PIECE,
) -> float:
    if saw_m <= 0 and cuts <= 0 and pieces <= 0:
        return 0.0
    blade = (max(0.0, saw_m) * 1000.0) / max(1.0, feed_mm_per_s)
    overhead = (
        max(0, cuts) * max(0.0, reposition_s)
        + max(0, pieces) * max(0.0, handling_s)
        + max(0, rotations) * max(0.0, rotation_s)
    )
    return blade + overhead

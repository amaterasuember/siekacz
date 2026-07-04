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

from dataclasses import dataclass, field

from core.models import OptimizationResult, PlacedSheetPart, SheetLayout
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
    pieces: int = 0
    rotated: int = 0
    cuts: int = 0
    saw_m: float = 0.0      # attributed saw travel (m)
    area_m2: float = 0.0    # net area of these pieces (m²)
    waste_m2: float = 0.0
    reusable_m2: float = 0.0
    gross_m2: float = 0.0
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


def _layout_stock_area_m2(layout: SheetLayout) -> float:
    return (float(layout.stock.width) * float(layout.stock.height)) / 1_000_000.0


def _layout_reusable_area_m2(layout: SheetLayout) -> float:
    if not layout.parts:
        return 0.0
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


def compute_cut_summary(result: OptimizationResult) -> CutSummary:
    """Aggregate per-format and whole-job cutting metrics from a result."""
    by_key: dict[tuple, FormatCutMetrics] = {}
    order: list[tuple] = []

    for layout in _iter_layouts(result):
        for placement in layout.parts:
            ow, oh = _orig_dims(placement)
            name = getattr(placement.part, "name", "") or "—"
            material = getattr(placement.part, "material", "") or ""
            key = (material, name, round(ow, 1), round(oh, 1))
            metrics = by_key.get(key)
            if metrics is None:
                metrics = FormatCutMetrics(name=name, width=ow, height=oh, material=material)
                by_key[key] = metrics
                order.append(key)
            metrics.pieces += 1
            if placement.rotated:
                metrics.rotated += 1
            # Per-piece attribution: two edge cuts of (w + h) saw travel.
            metrics.cuts += 2
            metrics.saw_m += (placement.width + placement.height) / 1000.0
            metrics.area_m2 += (placement.width * placement.height) / 1_000_000.0

    summary = CutSummary(formats=[by_key[k] for k in order])
    summary.total_pieces = sum(f.pieces for f in summary.formats)
    summary.total_rotated = sum(f.rotated for f in summary.formats)
    summary.total_area_m2 = sum(f.area_m2 for f in summary.formats)

    # Full stock area is reported, but reusable remnants are returned to stock.
    # They are not scrap and should not inflate per-format material cost.
    used_layouts = [layout for layout in _iter_layouts(result) if layout.parts]
    summary.total_stock_area_m2 = sum(_layout_stock_area_m2(layout) for layout in used_layouts)
    summary.total_reusable_m2 = min(
        max(0.0, summary.total_stock_area_m2 - summary.total_area_m2),
        sum(_layout_reusable_area_m2(layout) for layout in used_layouts),
    )
    summary.total_waste_m2 = max(
        0.0,
        summary.total_stock_area_m2 - summary.total_area_m2 - summary.total_reusable_m2,
    )
    summary.total_board_area_m2 = summary.total_area_m2 + summary.total_waste_m2

    # Attribute scrap and reusable remnants proportionally to net area.  Gross
    # stays net + non-reusable scrap, while reusable_m2 is informational stock.
    net_total = summary.total_area_m2
    for fmt in summary.formats:
        share = (fmt.area_m2 / net_total) if net_total > 0 else 0.0
        fmt.waste_m2 = summary.total_waste_m2 * share
        fmt.reusable_m2 = summary.total_reusable_m2 * share
        fmt.gross_m2 = fmt.area_m2 + fmt.waste_m2
        # Per-format time attribution: independent estimate from this format's
        # own saw travel / cuts / pieces / rotations.  These attributions sum to
        # slightly more than the whole-job time (which shares rip cuts), so the
        # headline total_time_s below stays the authoritative figure.
        fmt.time_s = estimate_job_time_seconds(
            saw_m=fmt.saw_m,
            cuts=fmt.cuts,
            pieces=fmt.pieces,
            rotations=fmt.rotated,
        )

    # Whole-job saw travel and cut count: prefer the layout-level totals that
    # already include the shared rip/cross boundary cuts (more accurate than the
    # per-piece attribution above), falling back to the attributed sum.
    layout_saw_mm = sum(getattr(l, "total_cut_length", 0.0) for l in _iter_layouts(result))
    layout_cuts = sum(int(getattr(l, "cut_count", 0)) for l in _iter_layouts(result))
    summary.total_saw_m = (layout_saw_mm / 1000.0) if layout_saw_mm > 0 else sum(f.saw_m for f in summary.formats)
    summary.total_cuts = layout_cuts if layout_cuts > 0 else sum(f.cuts for f in summary.formats)

    summary.total_time_s = estimate_job_time_seconds(
        saw_m=summary.total_saw_m,
        cuts=summary.total_cuts,
        pieces=summary.total_pieces,
        rotations=summary.total_rotated,
    )
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

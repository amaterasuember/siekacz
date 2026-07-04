from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core.models import OptimizationResult, SheetLayout

Rect = tuple[float, float, float, float]
EPS = 0.001
DEFAULT_MIN_REUSABLE_SIZE = 200.0

# ---------------------------------------------------------------------------
# Cut-time model (T2-6).  Conservative defaults representative of a small
# vertical panel saw / beam saw:
#   - 5 m/min linear feed = ~83 mm/s
#   - 1.5 s per cut for blade lift/positioning/clamping
#
# These are *estimates* shown to the operator — not used for scoring.  Real
# machine times vary with material, blade quality and operator speed.
# ---------------------------------------------------------------------------
CUT_SPEED_MM_PER_S: float = 83.0
REPOSITION_SECONDS_PER_CUT: float = 1.5


def estimate_cut_time_seconds(
    cut_length_mm: float,
    cut_count: int,
    speed_mm_per_s: float = CUT_SPEED_MM_PER_S,
    reposition_s_per_cut: float = REPOSITION_SECONDS_PER_CUT,
) -> float:
    """Return a rough machine-time estimate for a layout.

    formula = (length / speed) + (cuts * reposition_overhead)
    """
    if cut_length_mm <= 0.0 and cut_count <= 0:
        return 0.0
    linear = max(0.0, cut_length_mm) / max(1.0, speed_mm_per_s)
    overhead = max(0, int(cut_count)) * max(0.0, reposition_s_per_cut)
    return linear + overhead


def format_cut_time(seconds: float) -> str:
    """Human-friendly ``"~3 min 42 s"`` / ``"~12 s"`` formatting."""
    seconds = max(0.0, float(seconds or 0.0))
    if seconds < 1.0:
        return "<1 s"
    if seconds < 60.0:
        return f"~{int(round(seconds))} s"
    minutes = int(seconds // 60)
    rem_s = int(round(seconds - minutes * 60))
    if rem_s == 60:
        minutes += 1
        rem_s = 0
    if rem_s == 0:
        return f"~{minutes} min"
    return f"~{minutes} min {rem_s} s"


@dataclass(frozen=True)
class LayoutScore:
    value: tuple[float, ...]
    reusable_rects: list[Rect]
    reusable_area: float
    largest_area: float
    offcut_quality: float
    fragmentation: float


def rect_area(rect: Rect) -> float:
    return max(0.0, rect[2]) * max(0.0, rect[3])


def aspect_penalty(rect: Rect) -> float:
    width = max(rect[2], EPS)
    height = max(rect[3], EPS)
    ratio = max(width / height, height / width)
    if ratio <= 4.0:
        return 1.0
    if ratio <= 8.0:
        return 0.72
    return 0.42


def _clean(rects: Iterable[Rect]) -> list[Rect]:
    cleaned: list[Rect] = []
    seen: set[tuple[int, int, int, int]] = set()
    for x, y, width, height in rects:
        if width <= EPS or height <= EPS:
            continue
        key = (round(x * 1000), round(y * 1000), round(width * 1000), round(height * 1000))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((x, y, width, height))
    return cleaned


def reusable_rectangles(rects: Iterable[Rect], min_reusable_size: float = DEFAULT_MIN_REUSABLE_SIZE) -> list[Rect]:
    min_size = max(0.0, float(min_reusable_size or 0.0))
    return [rect for rect in _clean(rects) if rect[2] >= min_size - EPS and rect[3] >= min_size - EPS]


def reusable_score(rects: Iterable[Rect], min_reusable_size: float = DEFAULT_MIN_REUSABLE_SIZE) -> float:
    return sum(rect_area(rect) ** 2 * aspect_penalty(rect) for rect in reusable_rectangles(rects, min_reusable_size))


def _segment_bottom_rects(layout: SheetLayout, kerf: float) -> list[Rect]:
    rects: list[Rect] = []
    for segment in layout.vertical_segments:
        try:
            segment_x = float(segment.get("x", 0.0))
            segment_width = float(segment.get("width", 0.0))
        except (TypeError, ValueError, AttributeError):
            continue
        segment_parts = [
            part
            for part in layout.parts
            if part.x + EPS >= segment_x and part.x + part.width <= segment_x + segment_width + EPS
        ]
        if not segment_parts:
            continue
        bottom_y = min(layout.stock.height, max(part.y + part.height for part in segment_parts) + kerf)
        if bottom_y < layout.stock.height - EPS:
            rects.append((segment_x, bottom_y, segment_width, layout.stock.height - bottom_y))
    return rects


def collect_free_rectangles(layout: SheetLayout, kerf: float = 0.0, min_reusable_size: float = DEFAULT_MIN_REUSABLE_SIZE) -> list[Rect]:
    if not layout.parts:
        return [(0.0, 0.0, layout.stock.width, layout.stock.height)]

    used_width = layout.used_width
    used_height = layout.used_height
    right_x = min(layout.stock.width, used_width + kerf)
    bottom_y = min(layout.stock.height, used_height + kerf)

    vertical_first: list[Rect] = []
    if right_x < layout.stock.width - EPS:
        vertical_first.append((right_x, 0.0, layout.stock.width - right_x, layout.stock.height))
    if bottom_y < layout.stock.height - EPS and used_width > EPS:
        vertical_first.append((0.0, bottom_y, min(used_width, layout.stock.width), layout.stock.height - bottom_y))

    horizontal_first: list[Rect] = []
    if bottom_y < layout.stock.height - EPS:
        horizontal_first.append((0.0, bottom_y, layout.stock.width, layout.stock.height - bottom_y))
    if right_x < layout.stock.width - EPS and used_height > EPS:
        horizontal_first.append((right_x, 0.0, layout.stock.width - right_x, min(used_height, layout.stock.height)))

    segmented = list(_segment_bottom_rects(layout, kerf))
    if right_x < layout.stock.width - EPS:
        segmented.append((right_x, 0.0, layout.stock.width - right_x, layout.stock.height))

    options = [_clean(vertical_first), _clean(horizontal_first), _clean(segmented)]
    return max(
        options,
        key=lambda option: (
            reusable_score(option, min_reusable_size),
            max((rect_area(rect) for rect in reusable_rectangles(option, min_reusable_size)), default=0.0),
            sum(rect_area(rect) for rect in reusable_rectangles(option, min_reusable_size)),
        ),
    )


def estimate_cut_metrics(layout: SheetLayout, kerf: float = 0.0) -> tuple[float, int]:
    if not layout.parts:
        return 0.0, 0
    cut_count = 0
    cut_length = 0.0
    boundaries: set[float] = set()
    for segment in layout.vertical_segments:
        try:
            x = float(segment.get("x", 0.0))
            right = float(segment.get("right", 0.0))
        except (TypeError, ValueError, AttributeError):
            continue
        for boundary in (x, right):
            if EPS < boundary < layout.stock.width - EPS:
                boundaries.add(round(boundary, 3))
    cut_count += len(boundaries)
    cut_length += len(boundaries) * layout.stock.height
    for part in layout.parts:
        cut_count += 2
        cut_length += part.width + part.height
    return cut_length, cut_count


def _stack_signature(parts: list[object]) -> tuple[tuple[float, float], ...]:
    return tuple((round(getattr(part, "width"), 3), round(getattr(part, "height"), 3)) for part in parts)


def manufacturing_score(layout: SheetLayout) -> float:
    if not layout.parts or not layout.vertical_segments:
        return 0.0

    score = 0.0
    segments = sorted(layout.vertical_segments, key=lambda segment: float(segment.get("x", 0.0)))
    strip_parts: list[list[object]] = []
    strip_heights: list[float] = []
    strip_signatures: list[tuple[tuple[float, float], ...]] = []

    for segment in segments:
        try:
            left = float(segment.get("x", 0.0))
            right = float(segment.get("right", left + float(segment.get("width", 0.0))))
        except (TypeError, ValueError, AttributeError):
            continue
        parts = [
            placement
            for placement in layout.parts
            if placement.x + EPS >= left and placement.x + placement.width <= right + EPS
        ]
        if not parts:
            continue
        parts.sort(key=lambda placement: (placement.y, placement.x))
        strip_parts.append(parts)
        strip_heights.append(max(placement.y + placement.height for placement in parts) - min(placement.y for placement in parts))
        strip_signatures.append(_stack_signature(parts))

    if len(strip_parts) < 2:
        return score

    score += 1000.0
    repeated_signatures = {signature for signature in strip_signatures if strip_signatures.count(signature) > 1}
    if repeated_signatures:
        score += 500.0

    max_height = max(strip_heights)
    min_height = min(strip_heights)
    unique_indices = [
        index
        for index, height in enumerate(strip_heights)
        if strip_heights.count(height) == 1 and height >= max_height - EPS and max_height > min_height + EPS
    ]
    if unique_indices:
        unique_index = unique_indices[-1]
        if unique_index == len(strip_heights) - 1:
            score += 1500.0
        elif unique_index == 0:
            score -= 1500.0

    if strip_heights == sorted(strip_heights):
        score += 1000.0
    else:
        score -= 500.0

    first_signature = strip_signatures[0]
    if all(size == first_signature[0] for size in first_signature):
        score += 500.0

    return score


def annotate_layout_metrics(layout: SheetLayout, kerf: float = 0.0, min_reusable_size: float = DEFAULT_MIN_REUSABLE_SIZE) -> None:
    source_rects = layout.waste_rects if getattr(layout, "waste_rects", None) else collect_free_rectangles(layout, kerf, min_reusable_size)
    rects = reusable_rectangles(source_rects, min_reusable_size)
    layout.offcuts = rects
    layout.reusable_offcut_area = sum(rect_area(rect) for rect in rects)
    layout.largest_reusable_offcut_area = max((rect_area(rect) for rect in rects), default=0.0)
    layout.fragmentation_score = float(len(rects))
    layout.offcut_quality = layout.largest_reusable_offcut_area / layout.reusable_offcut_area * 100.0 if layout.reusable_offcut_area else 0.0
    if not getattr(layout, "cut_operations", None):
        layout.total_cut_length, layout.cut_count = estimate_cut_metrics(layout, kerf)
    layout.manufacturing_score = manufacturing_score(layout)
    # T2-6: estimated machine cut time (informational, not used for scoring).
    layout.estimated_cut_time_s = estimate_cut_time_seconds(layout.total_cut_length, layout.cut_count)


def build_reusable_offcuts(layouts: list[SheetLayout], min_reusable_size: float = DEFAULT_MIN_REUSABLE_SIZE) -> list[dict[str, object]]:
    reusable: list[dict[str, object]] = []
    for layout in layouts:
        for x, y, width, height in reusable_rectangles(layout.offcuts, min_reusable_size):
            reusable.append(
                {
                    "type": "sheet",
                    "material": layout.stock.material,
                    "thickness": layout.stock.thickness,
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "area": round(width * height, 2),
                    "source": f"Sheet {layout.sheet_index}",
                    "x": round(x, 2),
                    "y": round(y, 2),
                }
            )
    return reusable


def annotate_result_metrics(
    result: OptimizationResult,
    kerf: float = 0.0,
    min_reusable_size: float = DEFAULT_MIN_REUSABLE_SIZE,
) -> OptimizationResult:
    for layout in result.sheet_layouts:
        annotate_layout_metrics(layout, kerf, min_reusable_size)
    result.reusable_offcuts = build_reusable_offcuts(result.sheet_layouts, min_reusable_size)
    result.total_reusable_offcut_area = sum(layout.reusable_offcut_area for layout in result.sheet_layouts)
    result.largest_reusable_offcut_area = max((layout.largest_reusable_offcut_area for layout in result.sheet_layouts), default=0.0)
    result.fragmentation_score = sum(layout.fragmentation_score for layout in result.sheet_layouts)
    result.manufacturing_score = sum(layout.manufacturing_score for layout in result.sheet_layouts)
    # T2-6: aggregate per-sheet cut-time estimate into the result-level total.
    result.total_estimated_cut_time_s = sum(
        getattr(layout, "estimated_cut_time_s", 0.0) for layout in result.sheet_layouts
    ) + sum(
        getattr(layout, "estimated_cut_time_s", 0.0) for layout in result.missing_sheet_layouts
    )
    result.offcut_quality = (
        result.largest_reusable_offcut_area / result.total_reusable_offcut_area * 100.0
        if result.total_reusable_offcut_area
        else 0.0
    )
    return result


def score_result(
    result: OptimizationResult,
    kerf: float = 0.0,
    min_reusable_size: float = DEFAULT_MIN_REUSABLE_SIZE,
) -> LayoutScore:
    annotate_result_metrics(result, kerf, min_reusable_size)
    layouts = result.sheet_layouts
    reusable_rects = [rect for layout in layouts for rect in reusable_rectangles(layout.offcuts, min_reusable_size)]
    reusable_area = sum(rect_area(rect) for rect in reusable_rects)
    largest_area = max((rect_area(rect) for rect in reusable_rects), default=0.0)
    last_largest = layouts[-1].largest_reusable_offcut_area if layouts else 0.0
    reusable_power = reusable_score(reusable_rects, min_reusable_size)
    fragmentation = float(len(reusable_rects))
    cut_length = sum(layout.total_cut_length for layout in layouts)
    cut_count = sum(layout.cut_count for layout in layouts)
    non_vertical_penalty = sum(1 for layout in layouts if layout.parts and not layout.vertical_segments) * 500_000_000.0
    infeasible_penalty = sum(
        1
        for layout in layouts
        if layout.parts and getattr(layout, "is_guillotine_feasible", False) is False
    ) * 900_000_000_000.0
    manufacturing = sum(layout.manufacturing_score for layout in layouts)
    used_width_pressure = sum(layout.used_width / layout.stock.width for layout in layouts if layout.stock.width)
    last_layout = layouts[-1] if layouts else None
    last_used_area = last_layout.used_area if last_layout else 0.0
    last_bounding_area = last_layout.used_width * last_layout.used_height if last_layout else 0.0
    last_panel_count = len(last_layout.parts) if last_layout else 0
    last_sheet_area = layouts[-1].area if layouts else 1.0
    last_usage_ratio = last_used_area / last_sheet_area if last_sheet_area else 1.0

    value = (
        len(result.unplaced_sheet_parts) * 1_000_000_000_000.0,
        len(layouts) * 1_000_000_000.0,
        infeasible_penalty,
        non_vertical_penalty,
        -reusable_power / 1_000_000.0,
        -last_largest * 180.0,
        last_bounding_area * 18.0,
        last_panel_count * 22_000_000.0,
        -manufacturing * 1_000_000.0,
        fragmentation * 50_000.0,
        -largest_area * 20.0,
        cut_length * 0.1,
        cut_count * 100.0,
        used_width_pressure * 10_000.0,
        last_usage_ratio * 100_000.0,
    )
    quality = largest_area / reusable_area * 100.0 if reusable_area else 0.0
    return LayoutScore(value, reusable_rects, reusable_area, largest_area, quality, fragmentation)

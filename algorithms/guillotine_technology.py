from __future__ import annotations

from collections import defaultdict
from typing import Any

from algorithms.guillotine_slicing import build_slicing_tree
from core.layout_validation import layout_geometry_errors
from core.models import CutOperation, OptimizationResult, PlacedSheetPart, SheetLayout

Rect = tuple[float, float, float, float]
EPS = 0.001
SLICING_TREE_PART_LIMIT = 60


def _rect_area(rect: Rect) -> float:
    return max(0.0, rect[2]) * max(0.0, rect[3])


def _clean_rects(rects: list[Rect]) -> list[Rect]:
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


def _best_metric_offcuts(layout: SheetLayout, strip_waste: list[Rect], kerf: float, min_reusable_size: float) -> list[Rect]:
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

    def reusable(rects: list[Rect]) -> list[Rect]:
        return [rect for rect in _clean_rects(rects) if rect[2] >= min_reusable_size - EPS and rect[3] >= min_reusable_size - EPS]

    options = [_clean_rects(strip_waste), _clean_rects(vertical_first), _clean_rects(horizontal_first)]
    return max(
        options,
        key=lambda rects: (
            max((_rect_area(rect) for rect in reusable(rects)), default=0.0),
            sum(_rect_area(rect) for rect in reusable(rects)),
            -len(reusable(rects)),
        ),
    )


def _segment_bounds(layout: SheetLayout) -> list[tuple[int, float, float, float]]:
    bounds: list[tuple[int, float, float, float]] = []
    for index, segment in enumerate(layout.vertical_segments, start=1):
        try:
            x = float(segment.get("x", 0.0))
            width = float(segment.get("width", 0.0))
            right = float(segment.get("right", x + width))
        except (AttributeError, TypeError, ValueError):
            continue
        if width > EPS and right > x + EPS:
            bounds.append((int(segment.get("index", index)), x, width, right))
    return sorted(bounds, key=lambda item: item[1])


def _part_label(part: PlacedSheetPart) -> str:
    suffix = " R" if part.rotated else ""
    return f"{part.part.name}{suffix}"


def _find_segment_for_part(
    part: PlacedSheetPart,
    segments: list[tuple[int, float, float, float]],
) -> tuple[int, float, float, float] | None:
    for segment in segments:
        _, left, _, right = segment
        if part.x + EPS >= left and part.x + part.width <= right + EPS:
            return segment
    return None


def _check_bounds_and_overlaps(layout: SheetLayout, kerf: float) -> list[str]:
    return layout_geometry_errors(layout, kerf)


def _bottom_band_part_ids(parts: list[PlacedSheetPart], kerf: float) -> set[int]:
    """Identify parts that sit in a 'separated bottom band' that a single
    horizontal guillotine cut could isolate from everything they x-overlap.

    A part P is in such a band when every other part Q whose x-range overlaps
    P's x-range has its bottom edge (Q.y + Q.height) at least `kerf` above P.y.
    For such a P, a horizontal cut at the band's top edge produces an
    independent sub-region in which P (and its row siblings) may use a
    different vertical-strip configuration than the parts above it — fully
    guillotine-valid.
    """
    ids: set[int] = set()
    if len(parts) > 120:
        coords = sorted(
            {
                round(value, 4)
                for part in parts
                for value in (part.x, part.x + part.width)
            }
        )
        if len(coords) < 2:
            return ids
        cell_tops: list[list[tuple[float, int]]] = [[] for _ in range(len(coords) - 1)]

        def remember(cell: list[tuple[float, int]], value: tuple[float, int]) -> None:
            cell.append(value)
            cell.sort(key=lambda item: item[0], reverse=True)
            del cell[2:]

        for part in parts:
            part_id = id(part)
            bottom = part.y + part.height
            left = part.x
            right = part.x + part.width
            for index in range(len(coords) - 1):
                if coords[index] < right - EPS and left < coords[index + 1] - EPS:
                    remember(cell_tops[index], (bottom, part_id))

        for part in parts:
            part_id = id(part)
            left = part.x
            right = part.x + part.width
            max_other_bottom: float | None = None
            for index in range(len(coords) - 1):
                if not (coords[index] < right - EPS and left < coords[index + 1] - EPS):
                    continue
                for bottom, other_id in cell_tops[index]:
                    if other_id == part_id:
                        continue
                    max_other_bottom = bottom if max_other_bottom is None else max(max_other_bottom, bottom)
                    break
            if max_other_bottom is not None and part.y + EPS >= max_other_bottom + kerf - EPS:
                ids.add(part_id)
        return ids

    for part in parts:
        overlapping = [
            q for q in parts
            if q is not part
            and q.x < part.x + part.width - EPS
            and part.x < q.x + q.width - EPS
        ]
        if not overlapping:
            # Lone part — leave to the normal segment validator so existing
            # bugs (e.g. forgotten segment) aren't silently accepted.
            continue
        max_other_bottom = max(q.y + q.height for q in overlapping)
        if part.y + EPS >= max_other_bottom + kerf - EPS:
            ids.add(id(part))
    return ids


def _validate_bottom_band(parts: list[PlacedSheetPart], kerf: float) -> list[str]:
    """Row-style validation for parts in a separated bottom band.

    Parts at the same (y, height) form a row; within a row they must be
    spaced by at least `kerf` and not overlap.  Rows themselves may stack
    vertically if separated by ≥ kerf.
    """
    errors: list[str] = []
    rows: dict[tuple[int, int], list[PlacedSheetPart]] = defaultdict(list)
    for p in parts:
        rows[(round(p.y * 1000), round(p.height * 1000))].append(p)
    for row_parts in rows.values():
        row_parts.sort(key=lambda r: r.x)
        for i in range(len(row_parts) - 1):
            gap = row_parts[i + 1].x - (row_parts[i].x + row_parts[i].width)
            if gap < kerf - EPS:
                errors.append(
                    f"Dolny pas: za maly rzaz miedzy {row_parts[i].part.name} a {row_parts[i + 1].part.name}"
                )
    return errors


def validate_guillotine_feasibility(
    layout: SheetLayout,
    kerf: float = 0.0,
    allow_slicing_tree: bool = True,
) -> tuple[bool, list[str]]:
    errors = _check_bounds_and_overlaps(layout, kerf)
    if errors:
        return False, errors
    if not layout.parts:
        return True, errors

    segments = _segment_bounds(layout)
    slicing_proof = None
    if allow_slicing_tree and (not segments or len(layout.parts) <= SLICING_TREE_PART_LIMIT):
        slicing_proof = build_slicing_tree(layout, kerf)
    if not segments:
        reason = slicing_proof.reason if slicing_proof is not None else "wylaczony dla tej walidacji"
        errors.append(f"Brak pionowych pasow / segmentow; slicing-tree: {reason}.")
        return False, errors

    # Parts in a separated bottom-band can use a different vertical-strip
    # configuration than the parts they x-overlap — a single horizontal cut
    # separates the band first, after which independent vertical cuts apply.
    bottom_band_ids = _bottom_band_part_ids(layout.parts, kerf)

    # Only consider a part as "bottom-band exempt" if it would actually be
    # rejected by the strip-based rules (it crosses some boundary OR doesn't
    # belong to any existing segment).  Strip-conforming parts stay on the
    # original validation path so we don't lose row-stacking checks.
    boundaries = sorted({round(left, 4) for _, left, _, _ in segments} | {round(right, 4) for _, _, _, right in segments})
    exempt_ids: set[int] = set()
    for part in layout.parts:
        if id(part) not in bottom_band_ids:
            continue
        crosses = any(part.x + EPS < b < part.x + part.width - EPS for b in boundaries)
        has_segment = _find_segment_for_part(part, segments) is not None
        if crosses or not has_segment:
            exempt_ids.add(id(part))

    for part in layout.parts:
        if id(part) in exempt_ids:
            continue
        for boundary in boundaries:
            if part.x + EPS < boundary < part.x + part.width - EPS:
                errors.append(f"{part.part.name}: przecina pionowa granice pasa {boundary:.2f}")

    parts_by_segment: dict[int, list[PlacedSheetPart]] = defaultdict(list)
    for part in layout.parts:
        if id(part) in exempt_ids:
            continue
        segment = _find_segment_for_part(part, segments)
        if segment is None:
            errors.append(f"{part.part.name}: nie nalezy do zadnego pasa")
            continue
        parts_by_segment[segment[0]].append(part)

    # Validate the exempt (bottom-band) parts as their own sub-problem
    if exempt_ids:
        band_parts = [p for p in layout.parts if id(p) in exempt_ids]
        errors.extend(_validate_bottom_band(band_parts, kerf))

    for segment_index, segment_x, segment_width, _ in segments:
        segment_parts = sorted(parts_by_segment.get(segment_index, []), key=lambda item: (item.y, item.x))
        rows: dict[tuple[int, int], list[PlacedSheetPart]] = defaultdict(list)
        for part in segment_parts:
            rows[(round(part.y * 1000), round(part.height * 1000))].append(part)

        accepted_rows: list[tuple[float, float, float, float]] = []
        for (_, _), row_parts in sorted(rows.items(), key=lambda item: (item[1][0].y, item[1][0].x)):
            y = row_parts[0].y
            height = row_parts[0].height
            row_parts.sort(key=lambda item: item.x)
            row_left = min(part.x for part in row_parts)
            row_right = max(part.x + part.width for part in row_parts)
            row_bottom = y + height
            for previous_y, previous_bottom, previous_left, previous_right in accepted_rows:
                y_overlap = y < previous_bottom - EPS and previous_y < row_bottom - EPS
                x_separated = row_right + kerf <= previous_left + EPS or previous_right + kerf <= row_left + EPS
                if y_overlap and not x_separated:
                    errors.append(f"Pas {segment_index}: wiersze nachodza na siebie")
            cursor = segment_x
            for part in row_parts:
                if abs(part.y - y) > EPS or abs(part.height - height) > EPS:
                    errors.append(f"Pas {segment_index}: mieszane wysokosci w jednym wierszu")
                if part.x + EPS < cursor:
                    errors.append(f"Pas {segment_index}: element {part.part.name} blokuje kolejnosc ciecia")
                cursor = max(cursor, part.x + part.width + kerf)
            accepted_rows.append((y, row_bottom + kerf, row_left, row_right))

        if segment_parts:
            widest = max(part.width for part in segment_parts)
            if widest > segment_width + EPS:
                errors.append(f"Pas {segment_index}: formatka szersza niz pas")

    return not errors, errors


def _build_cut_data(layout: SheetLayout, kerf: float) -> tuple[dict[str, Any], list[CutOperation], list[Rect]]:
    segments = _segment_bounds(layout)
    operations: list[CutOperation] = []
    waste_rects: list[Rect] = []
    step = 1
    strips: list[dict[str, Any]] = []

    for segment_index, segment_x, segment_width, segment_right in segments:
        if EPS < segment_right < layout.stock.width - EPS:
            operations.append(
                CutOperation(
                    sheet_index=layout.sheet_index,
                    step=step,
                    orientation="vertical",
                    x=segment_right,
                    y=0.0,
                    length=layout.stock.height,
                    position=segment_right,
                    kind="rip",
                    description=f"Odetnij pas {segment_width:.0f} mm",
                )
            )
            step += 1

        segment_parts = [
            part
            for part in layout.parts
            if part.x + EPS >= segment_x and part.x + part.width <= segment_right + EPS
        ]
        rows: dict[tuple[int, int], list[PlacedSheetPart]] = defaultdict(list)
        for part in segment_parts:
            rows[(round(part.y * 1000), round(part.height * 1000))].append(part)

        row_nodes: list[dict[str, Any]] = []
        for row_parts in sorted(rows.values(), key=lambda items: (items[0].y, items[0].x)):
            row_parts.sort(key=lambda item: item.x)
            y = row_parts[0].y
            height = row_parts[0].height
            row_bottom = y + height
            if EPS < row_bottom < layout.stock.height - EPS:
                operations.append(
                    CutOperation(
                        sheet_index=layout.sheet_index,
                        step=step,
                        orientation="horizontal",
                        x=segment_x,
                        y=row_bottom,
                        length=segment_width,
                        position=row_bottom,
                        kind="cross",
                        description=f"Pas {segment_index}: tnij poprzecznie na {height:.0f} mm",
                    )
                )
                step += 1

            cell_nodes: list[dict[str, Any]] = []
            for part in row_parts:
                part_right = part.x + part.width
                if part_right < segment_right - EPS:
                    operations.append(
                        CutOperation(
                            sheet_index=layout.sheet_index,
                            step=step,
                            orientation="vertical",
                            x=part_right,
                            y=y,
                            length=height,
                            position=part_right,
                            kind="trim",
                            description=f"Pas {segment_index}: docinka {part.width:.0f} x {height:.0f}",
                        )
                    )
                    step += 1
                cell_nodes.append(
                    {
                        "x": part.x,
                        "y": part.y,
                        "width": part.width,
                        "height": part.height,
                        "part": _part_label(part),
                        "rotated": part.rotated,
                    }
                )
            row_used_right = max((part.x + part.width for part in row_parts), default=segment_x)
            if row_used_right + kerf < segment_right - EPS:
                waste_rects.append((row_used_right + kerf, y, segment_right - row_used_right - kerf, height))
            row_nodes.append({"y": y, "height": height, "parts": cell_nodes})

        bottom = max((part.y + part.height for part in segment_parts), default=0.0)
        if bottom + kerf < layout.stock.height - EPS:
            waste_rects.append((segment_x, bottom + kerf, segment_width, layout.stock.height - bottom - kerf))
        strips.append({"index": segment_index, "x": segment_x, "width": segment_width, "rows": row_nodes})

    used_right = max((segment[3] for segment in segments if any(_find_segment_for_part(part, [segment]) for part in layout.parts)), default=0.0)
    if used_right + kerf < layout.stock.width - EPS:
        waste_rects.append((used_right + kerf, 0.0, layout.stock.width - used_right - kerf, layout.stock.height))

    cut_tree = {
        "x": 0.0,
        "y": 0.0,
        "width": layout.stock.width,
        "height": layout.stock.height,
        "type": "strip-first",
        "strips": strips,
    }
    return cut_tree, operations, _clean_rects(waste_rects)


def explain_layout_decision(layout: SheetLayout) -> str:
    rotated_count = sum(1 for part in layout.parts if part.rotated)
    feasible = "TAK" if layout.is_guillotine_feasible else "NIE"
    largest = layout.largest_reusable_offcut_area
    return (
        f"Układ pasowy: {layout.strip_count} pasów, {len(layout.cut_operations)} cięć, "
        f"obrócone formatki: {rotated_count}, największy użyteczny odpad: {largest:.0f} mm2, "
        f"wykonalny gilotynowo: {feasible}."
    )


def annotate_guillotine_layout(
    layout: SheetLayout,
    kerf: float = 0.0,
    min_reusable_size: float = 200.0,
) -> SheetLayout:
    feasible, errors = validate_guillotine_feasibility(layout, kerf)
    layout.is_guillotine_feasible = feasible
    layout.strip_count = len(_segment_bounds(layout))
    if feasible:
        slicing_proof = (
            build_slicing_tree(layout, kerf)
            if (not layout.strip_count or len(layout.parts) <= SLICING_TREE_PART_LIMIT)
            else None
        )
        if layout.strip_count:
            cut_tree, operations, waste_rects = _build_cut_data(layout, kerf)
            if slicing_proof is not None and slicing_proof.feasible:
                cut_tree["slicing_proof"] = slicing_proof.tree
        else:
            cut_tree = (slicing_proof.tree if slicing_proof is not None else None) or {
                "type": "recursive-slicing",
                "width": layout.stock.width,
                "height": layout.stock.height,
            }
            operations = []
            waste_rects = _best_metric_offcuts(layout, [], kerf, min_reusable_size)
        layout.cut_tree = cut_tree
        layout.cut_operations = operations
        metric_waste_rects = _best_metric_offcuts(layout, waste_rects, kerf, min_reusable_size)
        layout.waste_rects = metric_waste_rects
        layout.offcuts = [rect for rect in metric_waste_rects if rect[2] >= min_reusable_size - EPS and rect[3] >= min_reusable_size - EPS]
        layout.cut_count = len(operations)
        layout.total_cut_length = sum(operation.length for operation in operations)
        layout.reusable_offcut_area = sum(_rect_area(rect) for rect in layout.offcuts)
        layout.largest_reusable_offcut_area = max((_rect_area(rect) for rect in layout.offcuts), default=0.0)
        layout.fragmentation_score = float(len(layout.offcuts))
        layout.offcut_quality = (
            layout.largest_reusable_offcut_area / layout.reusable_offcut_area * 100.0
            if layout.reusable_offcut_area
            else 0.0
        )
        layout.technology_warning = ""
    else:
        layout.cut_tree = None
        layout.cut_operations = []
        layout.waste_rects = []
        layout.technology_warning = "; ".join(errors[:5])
    layout.cutting_explanation = explain_layout_decision(layout)
    return layout


def annotate_guillotine_result(
    result: OptimizationResult,
    kerf: float = 0.0,
    min_reusable_size: float = 200.0,
) -> OptimizationResult:
    for layout in result.sheet_layouts:
        annotate_guillotine_layout(layout, kerf, min_reusable_size)

    result.total_reusable_offcut_area = sum(layout.reusable_offcut_area for layout in result.sheet_layouts)
    result.largest_reusable_offcut_area = max((layout.largest_reusable_offcut_area for layout in result.sheet_layouts), default=0.0)
    result.fragmentation_score = sum(layout.fragmentation_score for layout in result.sheet_layouts)
    result.offcut_quality = (
        result.largest_reusable_offcut_area / result.total_reusable_offcut_area * 100.0
        if result.total_reusable_offcut_area
        else 0.0
    )
    return result

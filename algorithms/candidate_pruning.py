from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from algorithms.layout_scoring import annotate_result_metrics, score_result
from core.models import OptimizationResult


@dataclass(frozen=True)
class CandidateMetrics:
    index: int
    non_filler_unplaced: int
    total_unplaced: int
    sheet_count: int
    infeasible_count: int
    saved_length: float
    consumed_area: float
    placed_count: int
    largest_reusable: float
    reusable_area: float
    fragmentation: float
    score_value: tuple[float, ...]


def candidate_metrics(
    index: int,
    result: OptimizationResult,
    kerf: float,
    min_reusable_size: float,
    count_non_fillers: Callable[[OptimizationResult], int],
    saved_length: Callable[[OptimizationResult], float],
    consumed_area: Callable[[OptimizationResult], float],
) -> CandidateMetrics:
    annotate_result_metrics(result, kerf, min_reusable_size)
    layouts = result.sheet_layouts
    return CandidateMetrics(
        index=index,
        non_filler_unplaced=count_non_fillers(result),
        total_unplaced=len(result.unplaced_sheet_parts),
        sheet_count=len(layouts),
        infeasible_count=sum(
            1
            for layout in layouts
            if layout.parts and getattr(layout, "is_guillotine_feasible", False) is False
        ),
        saved_length=saved_length(result),
        consumed_area=consumed_area(result),
        placed_count=sum(len(layout.parts) for layout in layouts),
        largest_reusable=max(
            (getattr(layout, "largest_reusable_offcut_area", 0.0) for layout in layouts),
            default=0.0,
        ),
        reusable_area=sum(getattr(layout, "reusable_offcut_area", 0.0) for layout in layouts),
        fragmentation=sum(getattr(layout, "fragmentation_score", 0.0) for layout in layouts),
        score_value=score_result(result, kerf, min_reusable_size).value,
    )


def dominates(left: CandidateMetrics, right: CandidateMetrics) -> bool:
    """Return True when ``left`` is no worse on production-critical axes.

    This is intentionally conservative.  A candidate is removed only if another
    candidate places at least as much, uses no more sheets/material, is no less
    feasible, and does not sacrifice the main reusable-offcut signals.
    """
    checks = (
        left.non_filler_unplaced <= right.non_filler_unplaced,
        left.total_unplaced <= right.total_unplaced,
        left.sheet_count <= right.sheet_count,
        left.infeasible_count <= right.infeasible_count,
        left.saved_length <= right.saved_length + 0.001,
        left.consumed_area <= right.consumed_area + 0.001,
        left.placed_count >= right.placed_count,
        left.largest_reusable + 0.001 >= right.largest_reusable,
        left.reusable_area + 0.001 >= right.reusable_area,
        left.fragmentation <= right.fragmentation + 0.001,
    )
    if not all(checks):
        return False
    return any(
        (
            left.non_filler_unplaced < right.non_filler_unplaced,
            left.total_unplaced < right.total_unplaced,
            left.sheet_count < right.sheet_count,
            left.infeasible_count < right.infeasible_count,
            left.saved_length + 0.001 < right.saved_length,
            left.consumed_area + 0.001 < right.consumed_area,
            left.placed_count > right.placed_count,
            left.largest_reusable > right.largest_reusable + 0.001,
            left.reusable_area > right.reusable_area + 0.001,
            left.fragmentation + 0.001 < right.fragmentation,
        )
    )


def prune_dominated_candidates(
    candidates: list[OptimizationResult],
    kerf: float,
    min_reusable_size: float,
    count_non_fillers: Callable[[OptimizationResult], int],
    saved_length: Callable[[OptimizationResult], float],
    consumed_area: Callable[[OptimizationResult], float],
    max_candidates: int = 96,
) -> tuple[list[OptimizationResult], int]:
    if len(candidates) <= 1:
        return candidates, 0

    metrics = [
        candidate_metrics(
            index,
            candidate,
            kerf,
            min_reusable_size,
            count_non_fillers,
            saved_length,
            consumed_area,
        )
        for index, candidate in enumerate(candidates)
    ]

    dominated: set[int] = set()
    for right in metrics:
        if right.index in dominated:
            continue
        for left in metrics:
            if left.index == right.index or left.index in dominated:
                continue
            if dominates(left, right):
                dominated.add(right.index)
                break

    kept = [candidate for index, candidate in enumerate(candidates) if index not in dominated]

    if len(kept) > max_candidates:
        ranked = sorted(
            enumerate(kept),
            key=lambda item: score_result(item[1], kerf, min_reusable_size).value,
        )
        keep_indices = {index for index, _ in ranked[:max_candidates]}
        removed_by_cap = len(kept) - len(keep_indices)
        kept = [candidate for index, candidate in enumerate(kept) if index in keep_indices]
    else:
        removed_by_cap = 0

    return kept, len(dominated) + removed_by_cap

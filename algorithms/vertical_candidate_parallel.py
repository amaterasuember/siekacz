from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from core.models import OptimizationResult, SheetPart, SheetStock

_logger = logging.getLogger(__name__)


def recommended_strategy_workers(strategy_count: int) -> int:
    cores = os.cpu_count() or 2
    return max(2, min(strategy_count, cores))


def _build_strategy_candidate(payload: tuple[Any, ...]) -> OptimizationResult:
    # Imported lazily to avoid a top-level circular import: two_d imports this
    # module, while worker processes need two_d's _build_result and _Strategy.
    from algorithms.two_d_vertical_segmented import _build_result

    stock, parts, kerf, margin, mode, strategy = payload
    return _build_result(stock, parts, kerf, margin, mode, strategy)


def build_strategy_candidates_parallel(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
    strategies: list[object],
) -> list[OptimizationResult]:
    if not strategies:
        return []

    payloads = [(stock, parts, kerf, margin, mode, strategy) for strategy in strategies]
    results: list[OptimizationResult | None] = [None] * len(payloads)
    workers = recommended_strategy_workers(len(payloads))

    try:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_build_strategy_candidate, payload): index
                for index, payload in enumerate(payloads)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    _logger.warning("Strategy candidate %s failed: %s", index, exc)
    except Exception as exc:
        _logger.warning("Strategy process pool unavailable: %s", exc)
        results = [None] * len(payloads)

    if all(result is not None for result in results):
        return [result for result in results if result is not None]

    # Deterministic fallback for failed/missing candidates.
    from algorithms.two_d_vertical_segmented import _build_result

    fallback: list[OptimizationResult] = []
    for index, payload in enumerate(payloads):
        result = results[index]
        if result is None:
            stock_i, parts_i, kerf_i, margin_i, mode_i, strategy_i = payload
            result = _build_result(stock_i, parts_i, kerf_i, margin_i, mode_i, strategy_i)
        fallback.append(result)
    return fallback

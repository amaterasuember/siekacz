from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from core.models import OptimizationResult, SheetPart, SheetStock

_logger = logging.getLogger(__name__)
_WORKER_CONTEXT: tuple[list[SheetStock], list[SheetPart], float, float, str] | None = None


def recommended_strategy_workers(strategy_count: int) -> int:
    cores = os.cpu_count() or 2
    configured = os.environ.get("SIEKACZ_STRATEGY_WORKERS", "").strip()
    if configured:
        try:
            cores = max(1, min(cores, int(configured)))
        except ValueError:
            pass
    return max(1, min(strategy_count, cores))


def _init_strategy_worker(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
) -> None:
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = (stock, parts, kerf, margin, mode)


def _build_strategy_candidate(strategy: Any) -> OptimizationResult:
    # Imported lazily to avoid a top-level circular import: two_d imports this
    # module, while worker processes need two_d's _build_result and _Strategy.
    from algorithms.two_d_vertical_segmented import _build_result

    if _WORKER_CONTEXT is None:
        raise RuntimeError("Strategy worker was not initialized.")
    stock, parts, kerf, margin, mode = _WORKER_CONTEXT
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

    results: list[OptimizationResult | None] = [None] * len(strategies)
    workers = recommended_strategy_workers(len(strategies))

    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_strategy_worker,
            initargs=(stock, parts, kerf, margin, mode),
        ) as executor:
            futures = {
                executor.submit(_build_strategy_candidate, strategy): index
                for index, strategy in enumerate(strategies)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    _logger.warning("Strategy candidate %s failed: %s", index, exc)
    except Exception as exc:
        _logger.warning("Strategy process pool unavailable: %s", exc)
        # Preserve the one-result-per-strategy contract so the deterministic
        # fallback below can still evaluate every candidate.  ``payloads`` was
        # removed when the input became worker-initializer state; referring to
        # it here turned a recoverable pool-start failure into a total abort.
        results = [None] * len(strategies)

    if all(result is not None for result in results):
        return [result for result in results if result is not None]

    # Deterministic fallback for failed/missing candidates.
    from algorithms.two_d_vertical_segmented import _build_result

    fallback: list[OptimizationResult] = []
    for index, strategy in enumerate(strategies):
        result = results[index]
        if result is None:
            result = _build_result(stock, parts, kerf, margin, mode, strategy)
        fallback.append(result)
    return fallback

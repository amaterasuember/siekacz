from __future__ import annotations

"""Multi-core cutting ensemble.

Runs several sheet algorithms over the *same* input on separate CPU cores and
returns the best-scoring result (``layout_scoring.score_result`` — lower tuple
is better).  This is the module that actually spreads the workload across the
processor.

Kept deliberately free of any Qt import: it is executed inside worker processes
spawned by :class:`concurrent.futures.ProcessPoolExecutor`, and pulling PySide6
into every child would make spawning slow and fragile.  A pure-thread fallback
keeps the program working if the process pool cannot start (frozen build,
sandbox, pickling issue, …).
"""

import logging
import os
import time
from concurrent.futures import (
    BrokenExecutor,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from copy import deepcopy

from algorithms.layout_scoring import annotate_result_metrics, score_result
from algorithms.two_d_guillotine import optimize_2d_guillotine
from algorithms.two_d_maxrects import optimize_2d_maxrects
from algorithms.two_d_skyline import optimize_2d_skyline
from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import OptimizationResult, SheetPart, SheetStock

_logger = logging.getLogger(__name__)

ALGORITHM_NAMES: tuple[str, ...] = (
    "Vertical Segmented Guillotine",
    "Guillotine",
    "Skyline",
    "MaxRects",
)

ENSEMBLE_TIMEOUT_S: float = 60.0


def run_named_algorithm(
    name: str,
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
    min_reusable_size: float,
    cutting_mode: str,
    optimization_mode: str,
) -> OptimizationResult:
    """Top-level (picklable) dispatch used by both pool kinds."""
    if name == "Vertical Segmented Guillotine":
        old_flag = os.environ.get("SIEKACZ_IN_ENSEMBLE")
        os.environ["SIEKACZ_IN_ENSEMBLE"] = "1"
        try:
            return optimize_2d_vertical_segmented(
                stock, parts, kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode
            )
        finally:
            if old_flag is None:
                os.environ.pop("SIEKACZ_IN_ENSEMBLE", None)
            else:
                os.environ["SIEKACZ_IN_ENSEMBLE"] = old_flag
    if name == "Guillotine":
        return annotate_result_metrics(optimize_2d_guillotine(stock, parts, kerf, margin, mode), kerf, min_reusable_size)
    if name == "Skyline":
        return annotate_result_metrics(optimize_2d_skyline(stock, parts, kerf, margin, mode), kerf, min_reusable_size)
    return annotate_result_metrics(optimize_2d_maxrects(stock, parts, kerf, margin, mode), kerf, min_reusable_size)


def recommended_worker_count() -> int:
    cores = os.cpu_count() or 2
    # The UI lives outside this process, so use every useful core.  There are
    # currently four independent algorithms, so extra workers would sit idle.
    return max(2, min(len(ALGORITHM_NAMES), cores))


def _run_with_threads(args_common) -> list[tuple[str, OptimizationResult]]:
    results: list[tuple[str, OptimizationResult]] = []
    stock, parts, rest = args_common
    with ThreadPoolExecutor(max_workers=recommended_worker_count()) as executor:
        futures = {
            executor.submit(run_named_algorithm, name, deepcopy(stock), deepcopy(parts), *rest): name
            for name in ALGORITHM_NAMES
        }
        for future in as_completed(futures, timeout=ENSEMBLE_TIMEOUT_S + 5):
            name = futures[future]
            try:
                results.append((name, future.result(timeout=ENSEMBLE_TIMEOUT_S)))
            except Exception as exc:  # pragma: no cover — defensive
                _logger.warning("Thread ensemble algorithm %s failed: %s", name, exc)
    return results


def _run_with_processes(args_common) -> list[tuple[str, OptimizationResult]]:
    results: list[tuple[str, OptimizationResult]] = []
    stock, parts, rest = args_common
    with ProcessPoolExecutor(max_workers=recommended_worker_count()) as executor:
        futures = {
            executor.submit(run_named_algorithm, name, stock, parts, *rest): name
            for name in ALGORITHM_NAMES
        }
        for future in as_completed(futures, timeout=ENSEMBLE_TIMEOUT_S + 15):
            name = futures[future]
            try:
                results.append((name, future.result(timeout=ENSEMBLE_TIMEOUT_S)))
            except Exception as exc:  # pragma: no cover — defensive
                _logger.warning("Process ensemble algorithm %s failed: %s", name, exc)
    return results


def run_parallel_ensemble(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
    min_reusable_size: float,
    cutting_mode: str,
    optimization_mode: str,
    use_processes: bool = True,
) -> OptimizationResult:
    """Evaluate every algorithm across cores; return the best-scoring result."""
    started = time.perf_counter()
    rest = (kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode)
    args_common = (stock, parts, rest)

    pairs: list[tuple[str, OptimizationResult]] = []
    used = "processes"
    if use_processes:
        try:
            pairs = _run_with_processes(args_common)
        except (BrokenExecutor, OSError, ImportError, Exception) as exc:
            _logger.warning("Process pool unavailable (%s) — falling back to threads", exc)
            pairs = []
    if not pairs:
        used = "threads"
        pairs = _run_with_threads(args_common)

    # Score the candidates (scoring is cheap and runs in the parent).
    scored: list[tuple[str, OptimizationResult, tuple]] = []
    diagnostics: list[str] = []
    for name, result in pairs:
        try:
            value = score_result(result, kerf, min_reusable_size).value
        except Exception as exc:
            diagnostics.append(f"{name}: scoring failed ({exc})")
            continue
        scored.append((name, result, value))
        diagnostics.append(
            f"{name}: sheets={len(result.sheet_layouts)} unplaced={len(result.unplaced_sheet_parts)}"
        )

    elapsed = time.perf_counter() - started

    if not scored:
        # Total failure — synchronous fallback so the user still gets an answer.
        fallback = run_named_algorithm(
            "Vertical Segmented Guillotine", deepcopy(stock), deepcopy(parts), *rest
        )
        fallback.algorithm = "Vertical Segmented Guillotine (fallback)"
        fallback.messages.append("Ensemble: wszystkie algorytmy zawiodły — użyto fallbacku.")
        return fallback

    scored.sort(key=lambda item: item[2])
    winner_name, winner, _ = scored[0]
    winner.algorithm = f"{winner_name} (multi-core ensemble winner)"
    winner.messages.append(
        f"Ensemble {used}: {len(scored)}/{len(ALGORITHM_NAMES)} algorytmów na "
        f"{recommended_worker_count()} rdzeniach w {elapsed:.2f}s — zwycięzca: {winner_name}."
    )
    for line in diagnostics:
        winner.messages.append(f"  • {line}")
    return winner

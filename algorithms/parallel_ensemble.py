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
from typing import Callable

from algorithms.layout_scoring import annotate_result_metrics, score_result
from algorithms.two_d_guillotine import optimize_2d_guillotine
from algorithms.two_d_maxrects import optimize_2d_maxrects
from algorithms.two_d_skyline import optimize_2d_skyline
from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import OptimizationResult, SheetPart, SheetStock

_logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]


def _report_progress(callback: ProgressCallback | None, percent: int, label: str) -> None:
    """Report a completed ensemble phase without affecting optimization."""
    if callback is None:
        return
    try:
        callback(max(0, min(100, int(percent))), label)
    except Exception:
        _logger.debug("Ensemble progress callback failed", exc_info=True)

ALGORITHM_NAMES: tuple[str, ...] = (
    "Vertical Segmented Guillotine",
    "Guillotine",
    "Skyline",
    "MaxRects",
)

ENSEMBLE_TIMEOUT_S: float = 3600.0


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


def _process_worker_split(total_units: int) -> tuple[int, int]:
    cores = max(1, os.cpu_count() or 2)
    # The vertical search owns a process pool itself.  Keep both pools within
    # the logical-core budget; the former eight-core branch created 2
    # comparison + 8 strategy workers, causing context switching and idle
    # comparison processes to remain resident after their short work ended.
    light_workers = 1 if cores > 1 and total_units else 0
    strategy_workers = max(1, cores - light_workers)
    return light_workers, strategy_workers


def _run_vertical_with_strategy_pool(
    stock: list[SheetStock],
    parts: list[SheetPart],
    rest: tuple,
    strategy_workers: int,
) -> OptimizationResult:
    old_parallel = os.environ.get("SIEKACZ_PARALLEL_STRATEGIES")
    old_workers = os.environ.get("SIEKACZ_STRATEGY_WORKERS")
    old_disable = os.environ.get("SIEKACZ_DISABLE_VERTICAL_CANDIDATE_POOL")
    old_ensemble = os.environ.pop("SIEKACZ_IN_ENSEMBLE", None)
    if strategy_workers > 1:
        os.environ["SIEKACZ_PARALLEL_STRATEGIES"] = "1"
        os.environ.pop("SIEKACZ_DISABLE_VERTICAL_CANDIDATE_POOL", None)
    else:
        os.environ.pop("SIEKACZ_PARALLEL_STRATEGIES", None)
        os.environ["SIEKACZ_DISABLE_VERTICAL_CANDIDATE_POOL"] = "1"
    os.environ["SIEKACZ_STRATEGY_WORKERS"] = str(max(1, strategy_workers))
    try:
        return optimize_2d_vertical_segmented(stock, parts, *rest)
    finally:
        if old_parallel is None:
            os.environ.pop("SIEKACZ_PARALLEL_STRATEGIES", None)
        else:
            os.environ["SIEKACZ_PARALLEL_STRATEGIES"] = old_parallel
        if old_workers is None:
            os.environ.pop("SIEKACZ_STRATEGY_WORKERS", None)
        else:
            os.environ["SIEKACZ_STRATEGY_WORKERS"] = old_workers
        if old_disable is None:
            os.environ.pop("SIEKACZ_DISABLE_VERTICAL_CANDIDATE_POOL", None)
        else:
            os.environ["SIEKACZ_DISABLE_VERTICAL_CANDIDATE_POOL"] = old_disable
        if old_ensemble is not None:
            os.environ["SIEKACZ_IN_ENSEMBLE"] = old_ensemble


def _run_sequential(args_common, progress_callback: ProgressCallback | None = None) -> list[tuple[str, OptimizationResult]]:
    results: list[tuple[str, OptimizationResult]] = []
    stock, parts, rest = args_common
    total = len(ALGORITHM_NAMES)
    for index, name in enumerate(ALGORITHM_NAMES, start=1):
        _report_progress(progress_callback, 5 + int((index - 1) / total * 78), f"Algorytm {index}/{total}: {name}")
        try:
            res = run_named_algorithm(name, deepcopy(stock), deepcopy(parts), *rest)
            results.append((name, res))
        except Exception as exc:  # pragma: no cover — defensive
            _logger.warning("Sequential ensemble algorithm %s failed: %s", name, exc)
        _report_progress(progress_callback, 5 + int(index / total * 78), f"Zakończono {index}/{total} algorytmów")
    return results


def _run_with_threads(args_common, progress_callback: ProgressCallback | None = None) -> list[tuple[str, OptimizationResult]]:
    results: list[tuple[str, OptimizationResult]] = []
    stock, parts, rest = args_common
    with ThreadPoolExecutor(max_workers=recommended_worker_count()) as executor:
        futures = {
            executor.submit(run_named_algorithm, name, deepcopy(stock), deepcopy(parts), *rest): name
            for name in ALGORITHM_NAMES
        }
        for completed, future in enumerate(as_completed(futures, timeout=ENSEMBLE_TIMEOUT_S + 5), start=1):
            name = futures[future]
            try:
                results.append((name, future.result(timeout=ENSEMBLE_TIMEOUT_S)))
            except Exception as exc:  # pragma: no cover — defensive
                _logger.warning("Thread ensemble algorithm %s failed: %s", name, exc)
            _report_progress(
                progress_callback,
                5 + int(completed / len(futures) * 78),
                f"Zakonczono {name} ({completed}/{len(futures)})",
            )
    return results


def _run_with_processes(args_common, progress_callback: ProgressCallback | None = None) -> list[tuple[str, OptimizationResult]]:
    results: list[tuple[str, OptimizationResult]] = []
    stock, parts, rest = args_common
    light_algorithms = tuple(name for name in ALGORITHM_NAMES if name != "Vertical Segmented Guillotine")
    total_units = sum(max(0, int(part.quantity)) for part in parts)
    light_workers, strategy_workers = _process_worker_split(total_units)
    with ProcessPoolExecutor(max_workers=light_workers) as executor:
        futures = {
            executor.submit(run_named_algorithm, name, stock, parts, *rest): name
            for name in light_algorithms
        }
        _report_progress(
            progress_callback,
            5,
            f"Produkcja: do {strategy_workers} procesów roboczych; porównanie: do {light_workers} procesów",
        )
        try:
            vertical = _run_vertical_with_strategy_pool(
                deepcopy(stock), deepcopy(parts), rest, strategy_workers
            )
            results.append(("Vertical Segmented Guillotine", vertical))
        except Exception as exc:
            _logger.warning("Parallel vertical segmented algorithm failed: %s", exc)
        _report_progress(progress_callback, 67, "Zakończono główny algorytm produkcyjny")

        for completed, future in enumerate(as_completed(futures, timeout=ENSEMBLE_TIMEOUT_S + 15), start=1):
            name = futures[future]
            try:
                results.append((name, future.result(timeout=ENSEMBLE_TIMEOUT_S)))
            except Exception as exc:  # pragma: no cover — defensive
                _logger.warning("Process ensemble algorithm %s failed: %s", name, exc)
            _report_progress(
                progress_callback,
                67 + int(completed / max(1, len(futures)) * 16),
                f"Zakonczono {name} ({completed}/{len(futures)})",
            )
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
    execution_mode: str = "processes",
    progress_callback: ProgressCallback | None = None,
) -> OptimizationResult:
    """Evaluate every algorithm across cores; return the best-scoring result."""
    started = time.perf_counter()
    rest = (kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode)
    args_common = (stock, parts, rest)

    pairs: list[tuple[str, OptimizationResult]] = []
    used = execution_mode
    if execution_mode == "sequential":
        _report_progress(progress_callback, 2, "Przygotowuje algorytmy do liczenia po kolei")
    else:
        _report_progress(
            progress_callback,
            2,
            f"Rownolegle licze {len(ALGORITHM_NAMES)} algorytmy (0/{len(ALGORITHM_NAMES)})",
        )
    if execution_mode == "processes":
        try:
            pairs = _run_with_processes(args_common, progress_callback)
        except (BrokenExecutor, OSError, ImportError, Exception) as exc:
            _logger.warning("Process pool unavailable (%s) — falling back to threads", exc)
            pairs = []
    if not pairs and execution_mode != "sequential":
        used = "threads"
        _report_progress(progress_callback, 5, "Przełączam na wykonanie awaryjne")
        pairs = _run_with_threads(args_common, progress_callback)
    if not pairs:
        used = "sequential"
        pairs = _run_sequential(args_common, progress_callback)

    # Score the candidates (scoring is cheap and runs in the parent).
    _report_progress(progress_callback, 87, "Porównuję wyniki algorytmów")
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
        _report_progress(progress_callback, 100, "Użyto algorytmu awaryjnego")
        return fallback

    scored.sort(key=lambda item: item[2])
    winner_name, winner, _ = scored[0]
    winner.algorithm = f"{winner_name} (multi-core ensemble winner)"
    winner.messages.append(
        f"Ensemble {used}: {len(scored)}/{len(ALGORITHM_NAMES)} algorytmów na "
        f"{os.cpu_count() or 2} rdzeniach logicznych w {elapsed:.2f}s — zwycięzca: {winner_name}."
    )
    for line in diagnostics:
        winner.messages.append(f"  • {line}")
    _report_progress(progress_callback, 100, "Wybrano najlepszy układ")
    return winner

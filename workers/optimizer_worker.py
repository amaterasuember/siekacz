from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QObject, Signal, Slot

from algorithms.one_d import optimize_1d
from algorithms.two_d_guillotine import optimize_2d_guillotine
from algorithms.layout_scoring import annotate_result_metrics, score_result
from algorithms.part_classification import is_small_filler_part
from algorithms.two_d_maxrects import optimize_2d_maxrects
from algorithms.two_d_skyline import optimize_2d_skyline
from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import OptimizationResult, Project, SheetPart, SheetStock


_logger = logging.getLogger(__name__)

KERF_TOLERANCE_MM = 0.2

# Algorithm name aliases that trigger the parallel ensemble.  When the user
# selects one of these in OptimizationSettings.algorithm, the worker runs
# every sheet algorithm in parallel and picks the best result via
# layout_scoring.score_result.
ENSEMBLE_ALIASES: frozenset[str] = frozenset({
    "auto", "ensemble", "best-of", "all", "best", "automatyczny",
})

# Maximum per-algorithm wall-time when running the ensemble.  Algorithms that
# exceed this are dropped from the comparison.  Generous default — most runs
# complete in <2 s; this is just a safety net against runaway scenarios.
ENSEMBLE_TIMEOUT_S: float = 45.0

# Cap on workers — more than 4 buys nothing (we only have 4 algorithms).
_ENSEMBLE_WORKERS: int = min(4, max(2, (os.cpu_count() or 2)))


def _finite_number(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{label} musi być poprawną liczbą.") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} musi być skończoną liczbą.")
    return number


def _positive_number(value: object, label: str) -> float:
    number = _finite_number(value, label)
    if number <= 0:
        raise ValueError(f"{label} musi być większe od zera.")
    return number


def _non_negative_number(value: object, label: str) -> float:
    number = _finite_number(value, label)
    if number < 0:
        raise ValueError(f"{label} nie może być ujemny.")
    return number


def _positive_int(value: object, label: str) -> int:
    number = _finite_number(value, label)
    rounded = round(number)
    if number <= 0 or abs(number - rounded) > 0.000001:
        raise ValueError(f"{label} musi być dodatnią liczbą całkowitą.")
    return int(rounded)


def _validate_project_for_optimization(project: Project) -> None:
    settings = project.settings
    kerf = _finite_number(settings.kerf, "Kerf")
    if kerf < 0:
        raise ValueError("Kerf nie może być ujemny.")
    margin = _non_negative_number(settings.margin, "Margines")
    _non_negative_number(settings.sheet_allowance, "Naddatek płyty")
    _non_negative_number(settings.min_reusable_offcut_size, "Minimalny użyteczny odpad")

    if settings.job_type == "linear":
        if not project.linear_stock:
            raise ValueError("Dodaj przynajmniej jeden dostępny profil.")
        if not project.linear_parts:
            raise ValueError("Dodaj przynajmniej jedną formatkę.")
        for index, stock in enumerate(project.linear_stock, start=1):
            _positive_number(stock.length, f"Profil {index} - długość")
            _positive_int(stock.quantity, f"Profil {index} - ilość")
            profile_kerf = _finite_number(stock.kerf, f"Profil {index} - kerf")
            if profile_kerf < 0:
                raise ValueError(f"Profil {index} - kerf nie może być ujemny.")
        for index, part in enumerate(project.linear_parts, start=1):
            _positive_number(part.length, f"Formatka {index} - długość")
            _positive_int(part.quantity, f"Formatka {index} - ilość")
        return

    if not project.sheet_stock:
        raise ValueError("Dodaj przynajmniej jeden dostępny format płyty.")
    if not project.sheet_parts:
        raise ValueError("Dodaj przynajmniej jedną formatkę.")

    for index, stock in enumerate(project.sheet_stock, start=1):
        _positive_number(stock.width, f"Płyta {index} - szerokość")
        _positive_number(stock.height, f"Płyta {index} - wysokość")
        _positive_number(stock.thickness, f"Płyta {index} - grubość")
        _positive_int(stock.quantity, f"Płyta {index} - ilość")
        _non_negative_number(stock.min_offcut_width, f"Płyta {index} - minimalny odpad szerokość")
        _non_negative_number(stock.min_offcut_height, f"Płyta {index} - minimalny odpad wysokość")
        # Margin must leave a usable area in both dimensions — otherwise the
        # algorithm would try to pack parts into a region with zero or negative
        # available space, causing silent failures or division-by-zero.
        usable_w = stock.width - 2 * margin
        usable_h = stock.height - 2 * margin
        if usable_w <= 0 or usable_h <= 0:
            raise ValueError(
                f"Płyta {index}: margines {margin:.1f} mm jest zbyt duży dla płyty "
                f"{stock.width:.0f} × {stock.height:.0f} mm — nie zostaje żadna użyteczna powierzchnia. "
                f"Zmniejsz margines poniżej {min(stock.width, stock.height) / 2:.0f} mm."
            )

    for index, part in enumerate(project.sheet_parts, start=1):
        _positive_number(part.width, f"Formatka {index} - szerokość")
        _positive_number(part.height, f"Formatka {index} - wysokość")
        _positive_number(part.thickness, f"Formatka {index} - grubość")
        _positive_int(part.quantity, f"Formatka {index} - ilość")


def _effective_kerf(kerf: float, tolerance: float = KERF_TOLERANCE_MM) -> float:
    return max(0.0, float(kerf or 0.0) + float(tolerance))


def _apply_sheet_allowance(stock: list[SheetStock], allowance: float) -> list[SheetStock]:
    amount = max(0.0, float(allowance or 0.0))
    adjusted: list[SheetStock] = []
    for item in stock:
        nominal_width = item.nominal_width or item.width
        nominal_height = item.nominal_height or item.height
        adjusted.append(
            replace(
                item,
                width=nominal_width + amount,
                height=nominal_height + amount,
                nominal_width=nominal_width,
                nominal_height=nominal_height,
                sheet_allowance=amount,
            )
        )
    return adjusted


# ---------------------------------------------------------------------------
# Algorithm registry — single source of truth used by the single-algorithm
# dispatcher AND the parallel ensemble.  Each entry is (display_name, runner).
# ``runner`` accepts the standard 8-positional signature and returns an
# already-annotated OptimizationResult.
# ---------------------------------------------------------------------------
def _runner_vertical_segmented(stock, parts, kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode):
    return optimize_2d_vertical_segmented(stock, parts, kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode)


def _runner_guillotine(stock, parts, kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode):
    return annotate_result_metrics(optimize_2d_guillotine(stock, parts, kerf, margin, mode), kerf, min_reusable_size)


def _runner_skyline(stock, parts, kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode):
    return annotate_result_metrics(optimize_2d_skyline(stock, parts, kerf, margin, mode), kerf, min_reusable_size)


def _runner_maxrects(stock, parts, kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode):
    return annotate_result_metrics(optimize_2d_maxrects(stock, parts, kerf, margin, mode), kerf, min_reusable_size)


_SHEET_ALGORITHMS: tuple[tuple[str, callable], ...] = (
    ("Vertical Segmented Guillotine", _runner_vertical_segmented),
    ("Guillotine", _runner_guillotine),
    ("Skyline", _runner_skyline),
    ("MaxRects", _runner_maxrects),
)


def _resolve_algorithm(algorithm: str) -> tuple[str, callable]:
    """Map an algorithm string to its (display_name, runner) entry."""
    key = (algorithm or "").lower()
    if "vertical" in key or "segment" in key or "pion" in key:
        return _SHEET_ALGORITHMS[0]
    if key.startswith("guillotine"):
        return _SHEET_ALGORITHMS[1]
    if key.startswith("skyline"):
        return _SHEET_ALGORITHMS[2]
    # MaxRects is the default fallback (matches historical behavior).
    return _SHEET_ALGORITHMS[3]


def _is_ensemble_request(algorithm: str) -> bool:
    return (algorithm or "").strip().lower() in ENSEMBLE_ALIASES


def _run_sheet_algorithm(
    algorithm: str,
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
    min_reusable_size: float = 200.0,
    cutting_mode: str = "hybrid",
    optimization_mode: str = "comfort",
) -> OptimizationResult:
    if _is_ensemble_request(algorithm):
        return _run_ensemble_sheet_algorithm(
            stock, parts, kerf, margin, mode,
            min_reusable_size, cutting_mode, optimization_mode,
        )
    _, runner = _resolve_algorithm(algorithm)
    return runner(stock, parts, kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode)


def _run_ensemble_sheet_algorithm(
    stock: list[SheetStock],
    parts: list[SheetPart],
    kerf: float,
    margin: float,
    mode: str,
    min_reusable_size: float,
    cutting_mode: str,
    optimization_mode: str,
) -> OptimizationResult:
    """Run every sheet algorithm across CPU cores; return the best-scoring result.

    Delegates to :mod:`algorithms.parallel_ensemble`, which is kept free of any
    Qt import so it can run inside :class:`ProcessPoolExecutor` workers (real
    multi-core), with a thread fallback when a process pool cannot start.
    Selection follows ``layout_scoring.score_result`` (lower tuple = better).
    """
    from algorithms.parallel_ensemble import run_parallel_ensemble

    return run_parallel_ensemble(
        stock, parts, kerf, margin, mode,
        min_reusable_size, cutting_mode, optimization_mode,
        use_processes=True,
    )


# Bonus-fill candidates for missing-sheet runs use the *same* classification
# as the main optimizer's fillers.  See algorithms.part_classification.
_is_bonus_filler_for_missing_sheets = is_small_filler_part


def _missing_stock_for(effective_stock: list[SheetStock], missing_parts: list[SheetPart]) -> list[SheetStock]:
    parts_by_material: dict[tuple[str, float], list[SheetPart]] = defaultdict(list)
    for part in missing_parts:
        parts_by_material[(part.material, round(part.thickness, 4))].append(part)

    virtual_stock: list[SheetStock] = []
    seen: set[tuple[object, ...]] = set()
    for (material, thickness), parts in parts_by_material.items():
        candidates = [
            item
            for item in effective_stock
            if item.material == material and abs(item.thickness - thickness) < 0.001
        ]
        for item in candidates:
            signature = (
                item.material,
                round(item.thickness, 4),
                round(item.width, 4),
                round(item.height, 4),
                item.grain_direction,
                item.allow_rotation,
                round(item.min_offcut_width, 4),
                round(item.min_offcut_height, 4),
            )
            if signature in seen:
                continue
            seen.add(signature)
            virtual_stock.append(replace(item, quantity=max(1, len(parts)), price=0.0, source="missing"))
    return virtual_stock


def _attach_missing_sheet_layouts(
    result: OptimizationResult,
    algorithm: str,
    effective_stock: list[SheetStock],
    kerf: float,
    margin: float,
    mode: str,
    min_reusable_size: float,
    cutting_mode: str,
    optimization_mode: str,
) -> OptimizationResult:
    missing_parts = result.unplaced_sheet_parts
    base_messages = [message for message in result.messages if "could not be placed" not in message]
    if not missing_parts:
        result.messages = base_messages
        return result

    result.messages = base_messages

    # Names of the truly-required unplaced parts — used to filter the
    # missing-run output and decide what counts as "still unplaced".
    missing_part_names: set[str] = {p.name for p in missing_parts}

    # Collect small filler parts from the main sheets and include them as
    # bonus-cut candidates in the missing-sheet run.  They fill waste areas
    # below / beside large structural parts (e.g. the gap under a 1023×404
    # panel).  Each bonus copy is tagged ``is_waste_fill=True`` so downstream
    # code (reports, BOM counts) can exclude them from the required-part total.
    bonus_filler_map: dict[str, SheetPart] = {}
    for layout in result.sheet_layouts:
        for placed in layout.parts:
            p = placed.part
            if p.name in missing_part_names or p.name in bonus_filler_map:
                continue
            if _is_bonus_filler_for_missing_sheets(p, layout.stock):
                bonus_filler_map[p.name] = replace(p, is_waste_fill=True)

    bonus_fillers = list(bonus_filler_map.values())
    all_parts_for_missing = list(missing_parts) + bonus_fillers

    missing_stock = _missing_stock_for(effective_stock, all_parts_for_missing)
    missing_result = _run_sheet_algorithm(
        algorithm, missing_stock, all_parts_for_missing,
        kerf, margin, mode, min_reusable_size, cutting_mode, optimization_mode,
    )

    # Sort missing sheets so the most-used (widest) appears first.  Users expect
    # the layout to fill sheets in order — a fuller sheet 2 followed by a
    # smaller sheet 3 reads as "packed greedily" even though the total material
    # is identical to any other ordering.
    sorted_missing = sorted(
        missing_result.sheet_layouts,
        key=lambda lay: (-lay.used_width, -lay.used_height, -len(lay.parts)),
    )
    for offset, layout in enumerate(sorted_missing, start=1):
        layout.sheet_index = len(result.sheet_layouts) + offset
        layout.stock = replace(layout.stock, source="missing")

    result.missing_sheet_layouts = sorted_missing
    # Parts that fit on the virtual missing sheets are no longer unplaced —
    # only the ones that STILL did not fit (too big for any available format)
    # should remain in result.unplaced_sheet_parts. Bonus-fill leftovers are
    # discarded — they are optional waste cuts, not required BOM parts.
    result.unplaced_sheet_parts = [
        p for p in missing_result.unplaced_sheet_parts
        if p.name in missing_part_names
    ]
    result.messages.append(f"Brakuje {len(result.missing_sheet_layouts)} dodatkowych płyt na {len(missing_parts)} formatek.")

    if result.unplaced_sheet_parts:
        names = []
        for part in result.unplaced_sheet_parts[:8]:
            names.append(f"{part.name} ({part.width:.0f} x {part.height:.0f})")
        suffix = "..." if len(result.unplaced_sheet_parts) > 8 else ""
        result.messages.append("Za duże dla dostępnego formatu: " + ", ".join(names) + suffix)
    return result


def optimize_sheet_project(project: Project) -> OptimizationResult:
    _validate_project_for_optimization(project)
    settings = project.settings
    effective_kerf = _effective_kerf(settings.kerf, getattr(settings, "kerf_tolerance", KERF_TOLERANCE_MM))
    effective_stock = _apply_sheet_allowance(project.sheet_stock, settings.sheet_allowance)
    sheet_parts = [
        replace(part, allow_rotation=part.allow_rotation and settings.allow_rotation)
        for part in project.sheet_parts
    ]
    result = _run_sheet_algorithm(
        settings.algorithm,
        effective_stock,
        sheet_parts,
        effective_kerf,
        settings.margin,
        settings.mode,
        settings.min_reusable_offcut_size,
        settings.cutting_mode,
        settings.optimization_mode,
    )
    return _attach_missing_sheet_layouts(
        result,
        settings.algorithm,
        effective_stock,
        effective_kerf,
        settings.margin,
        settings.mode,
        settings.min_reusable_offcut_size,
        settings.cutting_mode,
        settings.optimization_mode,
    )


def optimize_sheet_order(projects: list[Project]) -> OptimizationResult:
    """Optimize several independent board groups (different board types /
    thicknesses, each with its own formatki) and merge them into one result
    for a combined preview and whole-order quote.

    Each group is solved in complete isolation — formatki from one group never
    land on another group's boards.  Boards are re-numbered globally and tagged
    with a ``group_label`` so the UI can tell them apart.
    """
    groups = [p for p in projects if p is not None]
    if not groups:
        raise ValueError("Brak grup do policzenia.")
    if len(groups) == 1:
        return optimize_sheet_project(groups[0])

    merged = OptimizationResult(job_type="sheet", algorithm="order (multi-group)")
    sheet_no = 0
    for index, project in enumerate(groups, start=1):
        label = (project.meta.material or "").strip() or f"Grupa {index}"
        sub = optimize_sheet_project(project)
        for layout in list(sub.sheet_layouts) + list(sub.missing_sheet_layouts):
            sheet_no += 1
            layout.sheet_index = sheet_no
            setattr(layout, "group_label", label)
            setattr(layout, "group_index", index)
        merged.sheet_layouts.extend(sub.sheet_layouts)
        merged.missing_sheet_layouts.extend(sub.missing_sheet_layouts)
        merged.unplaced_sheet_parts.extend(sub.unplaced_sheet_parts)
        merged.messages.extend(sub.messages)
    return merged


class OptimizerWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self._cancelled = False

    @Slot()
    def run(self) -> None:
        try:
            settings = self.project.settings
            self.progress.emit(5, "Przygotowuję materiał i formatki")
            if self._cancelled:
                return
            if settings.job_type == "linear":
                self.progress.emit(35, "Liczenie rozkroju liniowego")
                result: OptimizationResult = optimize_1d(
                    self.project.linear_stock,
                    self.project.linear_parts,
                    method=settings.algorithm,
                    mode=settings.mode,
                )
            else:
                self.progress.emit(35, f"Liczenie płyt: {settings.algorithm}")
                result = optimize_sheet_project(self.project)
            self.progress.emit(100, "Obliczenia zakończone")
            if not self._cancelled:
                self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

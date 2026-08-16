"""Independent adversarial audit harness for the sheet-cutting engine.

This script deliberately does not reuse the production validator, scoring, or
candidate-selection code.  It checks geometry and requested quantities from
the returned placements, compares tiny one-sheet problems with a separate
integer brute-force oracle, and records every deterministic seed required to
reproduce a failure.

It is an audit artefact, not a replacement for the ordinary regression suite.

Examples::

    .\\.venv\\Scripts\\python.exe tests\\final_audit_harness.py
    .\\.venv\\Scripts\\python.exe tests\\final_audit_harness.py --engine-fuzz 10000
"""
from __future__ import annotations

import argparse
import json
import math
import random
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from itertools import permutations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.two_d_vertical_segmented import optimize_2d_vertical_segmented
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import _missing_stock_for, optimize_sheet_project


EPS = 1e-6
WORKER_KERF_TOLERANCE = 0.2
REPORT_PATH = Path("outputs") / "final_audit_harness_results.json"


@dataclass(frozen=True)
class AuditCase:
    seed: int
    stock_width: int
    stock_height: int
    kerf: int
    rotation: bool
    mode: str
    parts: tuple[tuple[str, int, int, int], ...]

    def build(self) -> tuple[list[SheetStock], list[SheetPart]]:
        stock = [
            SheetStock(
                "AUDIT",
                1,
                self.stock_width,
                self.stock_height,
                1,
                allow_rotation=self.rotation,
                min_offcut_width=0,
                min_offcut_height=0,
            )
        ]
        parts = [
            SheetPart(name, width, height, quantity, "AUDIT", 1, allow_rotation=self.rotation)
            for name, width, height, quantity in self.parts
        ]
        return stock, parts

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _part_key(part: SheetPart) -> tuple[object, ...]:
    """Full identity; names alone are not sufficient for audit accounting."""
    return (
        part.name,
        round(float(part.width), 6),
        round(float(part.height), 6),
        str(part.material).casefold(),
        round(float(part.thickness), 6),
        bool(part.allow_rotation),
        str(part.grain_direction),
        int(part.priority),
    )


def _effective_kerf(user_kerf: float, through_worker: bool) -> float:
    return float(user_kerf) + (WORKER_KERF_TOLERANCE if through_worker else 0.0)


def _assert_independent_invariants(
    case: AuditCase,
    result,
    *,
    effective_kerf: float,
    require_no_virtual: bool = False,
) -> list[str]:
    """Check output independently from production geometry helpers."""
    errors: list[str] = []
    expected: Counter[tuple[object, ...]] = Counter()
    actual: Counter[tuple[object, ...]] = Counter()
    for part in (case.build()[1]):
        expected[_part_key(part)] += int(part.quantity)

    for layout in list(result.sheet_layouts) + list(result.missing_sheet_layouts):
        if require_no_virtual and str(getattr(layout.stock, "source", "stock")) == "missing":
            errors.append("unexpected virtual/missing sheet")
        width = float(layout.stock.width)
        height = float(layout.stock.height)
        if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
            errors.append(f"invalid sheet geometry {width}x{height}")
        for placement in layout.parts:
            values = (placement.x, placement.y, placement.width, placement.height)
            if not all(math.isfinite(float(value)) for value in values):
                errors.append(f"non-finite placement {placement.part.name}: {values}")
                continue
            if placement.x < -EPS or placement.y < -EPS:
                errors.append(f"negative coordinate {placement.part.name}: {placement.x},{placement.y}")
            if placement.width <= EPS or placement.height <= EPS:
                errors.append(f"non-positive placement {placement.part.name}: {placement.width}x{placement.height}")
            if placement.x + placement.width > width + EPS or placement.y + placement.height > height + EPS:
                errors.append(
                    f"out of bounds {placement.part.name}: {placement.x}+{placement.width} x {placement.y}+{placement.height} on {width}x{height}"
                )
            expected_dims = (placement.part.width, placement.part.height)
            rendered_dims = (placement.width, placement.height)
            if placement.rotated:
                valid_dims = rendered_dims == (expected_dims[1], expected_dims[0])
            else:
                valid_dims = rendered_dims == expected_dims
            if not valid_dims:
                errors.append(
                    f"orientation mismatch {placement.part.name}: source={expected_dims}, rendered={rendered_dims}, rotated={placement.rotated}"
                )
            if not getattr(placement.part, "is_waste_fill", False):
                actual[_part_key(placement.part)] += 1

        for first_index, first in enumerate(layout.parts):
            for second in layout.parts[first_index + 1 :]:
                x_overlap = first.x < second.x + second.width - EPS and second.x < first.x + first.width - EPS
                y_overlap = first.y < second.y + second.height - EPS and second.y < first.y + first.height - EPS
                if x_overlap and y_overlap:
                    errors.append(f"overlap {first.part.name} / {second.part.name}")
                    continue
                # If projections overlap on one axis, a production cut needs a
                # kerf-sized separation on the other axis.  This catches a
                # common false-positive: no rectangle overlap but no saw gap.
                if y_overlap:
                    gap = max(second.x - (first.x + first.width), first.x - (second.x + second.width))
                    if gap < effective_kerf - EPS:
                        errors.append(f"horizontal kerf gap {gap:g} < {effective_kerf:g}: {first.part.name}/{second.part.name}")
                if x_overlap:
                    gap = max(second.y - (first.y + first.height), first.y - (second.y + second.height))
                    if gap < effective_kerf - EPS:
                        errors.append(f"vertical kerf gap {gap:g} < {effective_kerf:g}: {first.part.name}/{second.part.name}")

    for part in result.unplaced_sheet_parts:
        actual[_part_key(part)] += 1
    if actual != expected:
        errors.append(f"quantity mismatch expected={dict(expected)} actual={dict(actual)}")
    return errors


def _brute_feasible(case: AuditCase, max_nodes: int = 250_000) -> bool | None:
    """Independent exhaustive integer packing oracle for tiny one-sheet cases.

    A value of ``None`` means the deliberate node budget was reached; such a
    case is excluded rather than guessed.  Kerf is modelled only between
    pieces, never at the outer sheet edge.
    """
    stock, source_parts = case.build()
    sheet = stock[0]
    pieces: list[tuple[str, int, int, bool]] = []
    for part in source_parts:
        pieces.extend((part.name, int(part.width), int(part.height), bool(part.allow_rotation)) for _ in range(part.quantity))
    pieces.sort(key=lambda item: item[1] * item[2], reverse=True)
    placed: list[tuple[int, int, int, int]] = []
    nodes = 0

    def slicing_feasible(
        items: tuple[tuple[int, int, int, int], ...],
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> bool:
        """Independent recursive guillotine proof for a completed layout."""
        if len(items) <= 1:
            return True
        right = left + width
        bottom = top + height
        vertical_edges = sorted({item[0] for item in items} | {item[0] + item[2] for item in items})
        for cut in vertical_edges:
            if cut <= left or cut + case.kerf >= right:
                continue
            left_items = tuple(item for item in items if item[0] + item[2] <= cut)
            right_items = tuple(item for item in items if item[0] >= cut + case.kerf)
            if len(left_items) + len(right_items) != len(items) or not left_items or not right_items:
                continue
            if slicing_feasible(left_items, left, top, cut - left, height) and slicing_feasible(
                right_items, cut + case.kerf, top, right - cut - case.kerf, height
            ):
                return True
        horizontal_edges = sorted({item[1] for item in items} | {item[1] + item[3] for item in items})
        for cut in horizontal_edges:
            if cut <= top or cut + case.kerf >= bottom:
                continue
            upper_items = tuple(item for item in items if item[1] + item[3] <= cut)
            lower_items = tuple(item for item in items if item[1] >= cut + case.kerf)
            if len(upper_items) + len(lower_items) != len(items) or not upper_items or not lower_items:
                continue
            if slicing_feasible(upper_items, left, top, width, cut - top) and slicing_feasible(
                lower_items, left, cut + case.kerf, width, bottom - cut - case.kerf
            ):
                return True
        return False

    def fits(x: int, y: int, width: int, height: int) -> bool:
        if x < 0 or y < 0 or x + width > sheet.width or y + height > sheet.height:
            return False
        for px, py, pw, ph in placed:
            x_overlap = x < px + pw and px < x + width
            y_overlap = y < py + ph and py < y + height
            if x_overlap and y_overlap:
                return False
            if y_overlap and max(x - (px + pw), px - (x + width)) < case.kerf:
                return False
            if x_overlap and max(y - (py + ph), py - (y + height)) < case.kerf:
                return False
        return True

    def search(index: int) -> bool | None:
        nonlocal nodes
        if index == len(pieces):
            return slicing_feasible(tuple(placed), 0, 0, int(sheet.width), int(sheet.height))
        if nodes >= max_nodes:
            return None
        _name, original_width, original_height, rotate = pieces[index]
        variants = [(original_width, original_height)]
        if rotate and original_width != original_height:
            variants.append((original_height, original_width))
        seen: set[tuple[int, int]] = set()
        for width, height in variants:
            if (width, height) in seen:
                continue
            seen.add((width, height))
            for y in range(int(sheet.height - height) + 1):
                for x in range(int(sheet.width - width) + 1):
                    nodes += 1
                    if not fits(x, y, width, height):
                        continue
                    placed.append((x, y, width, height))
                    answer = search(index + 1)
                    placed.pop()
                    if answer is not False:
                        return answer
        return False

    return search(0)


def _run_direct(case: AuditCase):
    stock, parts = case.build()
    return optimize_2d_vertical_segmented(
        stock,
        parts,
        kerf=case.kerf,
        margin=0,
        min_reusable_size=1,
        cutting_mode="hybrid",
        optimization_mode=case.mode,
    )


def _case_from_seed(seed: int, *, tiny: bool) -> AuditCase:
    rng = random.Random(seed)
    if tiny:
        stock_width = rng.randint(5, 10)
        stock_height = rng.randint(5, 10)
        groups = rng.randint(1, 3)
        max_quantity = 2
        max_dimension = min(stock_width, stock_height)
    else:
        stock_width = rng.choice([1000, 1300, 1500, 2000, 2050, rng.randint(450, 2600)])
        stock_height = rng.choice([1000, 1400, 2000, 3000, 3050, rng.randint(450, 3200)])
        groups = rng.randint(1, 4)
        max_quantity = 3
        max_dimension = None
    parts: list[tuple[str, int, int, int]] = []
    for index in range(groups):
        if tiny:
            width = rng.randint(1, max_dimension)
            height = rng.randint(1, max_dimension)
        else:
            # Boundary-biased distribution: exact edges, ±1, halves, thirds,
            # plus irregular dimensions.
            candidates_w = [1, stock_width - 1, stock_width, stock_width + 1, stock_width // 2, stock_width // 3]
            candidates_h = [1, stock_height - 1, stock_height, stock_height + 1, stock_height // 2, stock_height // 3]
            width = rng.choice(candidates_w + [rng.randint(1, stock_width + 100)])
            height = rng.choice(candidates_h + [rng.randint(1, stock_height + 100)])
        parts.append((f"P{index}", max(1, width), max(1, height), rng.randint(1, max_quantity)))
    return AuditCase(
        seed=seed,
        stock_width=stock_width,
        stock_height=stock_height,
        kerf=rng.choice([0, 1, 3] if tiny else [0, 1, 3, 5, 8, 25]),
        rotation=bool(rng.getrandbits(1)),
        mode=rng.choice(["comfort", "sport"]),
        parts=tuple(parts),
    )


def _all_placed(result) -> bool:
    return not result.unplaced_sheet_parts and not result.missing_sheet_layouts


def run_boundary_matrix() -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    presets = ((1000, 2000), (2050, 3050), (1500, 3000), (1300, 1400))
    total = 0
    for stock_width, stock_height in presets:
        for rotation in (False, True):
            for mode in ("comfort", "sport"):
                for kerf in (0, 5):
                    for width in (stock_width - 1, stock_width, stock_width + 1):
                        for height in (stock_height - 1, stock_height, stock_height + 1):
                            total += 1
                            case = AuditCase(total, stock_width, stock_height, kerf, rotation, mode, (("edge", width, height, 1),))
                            result = _run_direct(case)
                            errors = _assert_independent_invariants(case, result, effective_kerf=kerf)
                            should_fit = width <= stock_width and height <= stock_height
                            if rotation:
                                should_fit = should_fit or (height <= stock_width and width <= stock_height)
                            if errors or _all_placed(result) != should_fit:
                                failures.append({"case": case.as_dict(), "errors": errors, "all_placed": _all_placed(result), "expected_fit": should_fit})
    print(f"[audit] boundary matrix: {total} cases, failures={len(failures)}", flush=True)
    return failures


def run_bruteforce_comparison(count: int) -> tuple[list[dict[str, object]], int]:
    failures: list[dict[str, object]] = []
    compared = 0
    for offset in range(count):
        case = _case_from_seed(80_000 + offset, tiny=True)
        oracle = _brute_feasible(case)
        if oracle is None:
            continue
        compared += 1
        result = _run_direct(case)
        errors = _assert_independent_invariants(case, result, effective_kerf=case.kerf)
        if errors or (oracle and not _all_placed(result)):
            failures.append({"case": case.as_dict(), "oracle_feasible": oracle, "all_placed": _all_placed(result), "errors": errors})
        if (offset + 1) % 100 == 0:
            print(f"[audit] brute-force: {offset + 1}/{count}, compared={compared}, failures={len(failures)}", flush=True)
    return failures, compared


def run_engine_fuzz(count: int) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for offset in range(count):
        case = _case_from_seed(10_000_000 + offset, tiny=False)
        result = _run_direct(case)
        errors = _assert_independent_invariants(case, result, effective_kerf=case.kerf)
        if errors:
            failures.append({"case": case.as_dict(), "errors": errors})
        if (offset + 1) % 250 == 0:
            print(f"[audit] engine fuzz: {offset + 1}/{count}, failures={len(failures)}", flush=True)
    return failures


def run_known_regressions() -> list[dict[str, object]]:
    """Execute every confirmed production failure independently of fuzz ranges.

    These cases intentionally fail until the production defects are repaired.
    Keeping them explicit makes a later repair verifiable without relying on a
    pseudo-random seed landing on the same geometry again.
    """
    cases = (
        (
            "AUD-001-incomplete-sport-packing",
            AuditCase(80_057, 6, 5, 0, False, "sport", (("P0", 1, 1, 2), ("P1", 5, 2, 1), ("P2", 3, 2, 2))),
            True,
        ),
        (
            "AUD-002-incomplete-comfort-packing",
            AuditCase(80_789, 6, 8, 0, False, "comfort", (("P0", 3, 6, 2), ("P1", 5, 1, 1))),
            True,
        ),
        (
            "AUD-003-incomplete-kerf-packing",
            AuditCase(80_964, 9, 10, 3, False, "comfort", (("P0", 4, 2, 1), ("P1", 3, 3, 2))),
            True,
        ),
        (
            "AUD-004-compression-drops-parts",
            AuditCase(
                10_006_414,
                2000,
                2000,
                1,
                False,
                "sport",
                (("P0", 1000, 1000, 3), ("P1", 1, 666, 2), ("P2", 2000, 2001, 2), ("P3", 722, 624, 3)),
            ),
            False,
        ),
    )
    failures: list[dict[str, object]] = []
    for identifier, case, needs_complete_packing in cases:
        result = _run_direct(case)
        errors = _assert_independent_invariants(case, result, effective_kerf=case.kerf)
        if needs_complete_packing and not _all_placed(result):
            errors.append("independent oracle has a complete one-sheet guillotine layout, but optimizer leaves parts unplaced")
        if errors:
            failures.append({"id": identifier, "case": case.as_dict(), "errors": errors})
    print(f"[audit] confirmed regressions: {len(cases)} cases, still failing={len(failures)}", flush=True)
    return failures


def run_metamorphic_checks(count: int) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for offset in range(count):
        original = _case_from_seed(20_000_000 + offset, tiny=True)
        result = _run_direct(original)
        scale = 2
        scaled = AuditCase(
            original.seed,
            original.stock_width * scale,
            original.stock_height * scale,
            original.kerf * scale,
            original.rotation,
            original.mode,
            tuple((name, width * scale, height * scale, quantity) for name, width, height, quantity in original.parts),
        )
        scaled_result = _run_direct(scaled)
        original_errors = _assert_independent_invariants(original, result, effective_kerf=original.kerf)
        scaled_errors = _assert_independent_invariants(scaled, scaled_result, effective_kerf=scaled.kerf)
        if original_errors or scaled_errors or _all_placed(result) != _all_placed(scaled_result):
            failures.append({
                "case": original.as_dict(),
                "scaled_case": scaled.as_dict(),
                "original_all_placed": _all_placed(result),
                "scaled_all_placed": _all_placed(scaled_result),
                "errors": original_errors + scaled_errors,
            })
    print(f"[audit] metamorphic scale ×2: {count} cases, failures={len(failures)}", flush=True)
    return failures


def run_permutation_checks(count: int) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for offset in range(count):
        case = _case_from_seed(30_000_000 + offset, tiny=True)
        if len(case.parts) < 2:
            continue
        outcomes: set[tuple[bool, int, int]] = set()
        for ordering in permutations(case.parts):
            permuted = AuditCase(case.seed, case.stock_width, case.stock_height, case.kerf, case.rotation, case.mode, tuple(ordering))
            result = _run_direct(permuted)
            outcomes.add((_all_placed(result), len(result.sheet_layouts), len(result.unplaced_sheet_parts)))
        if len(outcomes) > 1:
            failures.append({"case": case.as_dict(), "outcomes": sorted(outcomes)})
    print(f"[audit] permutation checks: {count} seeds, unstable={len(failures)}", flush=True)
    return failures


def run_virtual_stock_probe() -> dict[str, object]:
    """Probe whether a direct worker call invents unavailable supplier sizes."""
    stock = [SheetStock("AUDIT", 1, 1000, 1000, 1, allow_rotation=True)]
    part = SheetPart("oversize", 1500, 200, 1, "AUDIT", 1, allow_rotation=True)
    virtual = _missing_stock_for(stock, [part])
    project = Project(
        sheet_stock=stock,
        sheet_parts=[part],
        settings=OptimizationSettings(kerf=5, multi_core=False),
    )
    result = optimize_sheet_project(project)
    return {
        "input_stock": [(item.width, item.height) for item in stock],
        "part": (part.width, part.height),
        "virtual_stock": [(item.width, item.height) for item in virtual],
        "missing_layout_stock": [(item.stock.width, item.stock.height) for item in result.missing_sheet_layouts],
        "unplaced": [(item.width, item.height) for item in result.unplaced_sheet_parts],
    }


def run_history_corruption_probe() -> dict[str, object]:
    """Verify whether an unreadable history is preserved rather than overwritten."""
    from app import project_history

    original_app_dir = project_history.APP_DIR
    original_history_path = project_history.HISTORY_PATH
    original_lock_path = project_history.HISTORY_LOCK_PATH
    try:
        with tempfile.TemporaryDirectory(prefix="siekacz-audit-history-") as temporary:
            root = Path(temporary)
            project_history.APP_DIR = root
            project_history.HISTORY_PATH = root / "history.json"
            project_history.HISTORY_LOCK_PATH = root / "history.lock"
            project_history.save_project_record({"id": "survivor", "name": "Niezastępowalny projekt"})
            project_history.HISTORY_PATH.write_text("{not valid json", encoding="utf-8")
            visible_after_corruption = project_history.list_projects()
            write_blocked = False
            try:
                project_history.save_project_record({"id": "new", "name": "Nowy projekt"})
            except project_history.ProjectHistoryReadError:
                write_blocked = True
            corrupt_source_preserved = project_history.HISTORY_PATH.read_text(encoding="utf-8") == "{not valid json"
            backups = list(root.glob("history.corrupt-*.json"))
            return {
                "visible_after_corruption": visible_after_corruption,
                "write_blocked": write_blocked,
                "corrupt_source_preserved": corrupt_source_preserved,
                "backup_created": bool(backups),
            }
    finally:
        project_history.APP_DIR = original_app_dir
        project_history.HISTORY_PATH = original_history_path
        project_history.HISTORY_LOCK_PATH = original_lock_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-fuzz", type=int, default=1000, help="Number of production-engine fuzz cases.")
    parser.add_argument("--brute-force", type=int, default=500, help="Number of tiny oracle comparisons.")
    parser.add_argument("--metamorphic", type=int, default=300)
    parser.add_argument("--permutations", type=int, default=100)
    args = parser.parse_args()
    started = time.perf_counter()

    boundary_failures = run_boundary_matrix()
    known_regression_failures = run_known_regressions()
    brute_failures, brute_compared = run_bruteforce_comparison(args.brute_force)
    metamorphic_failures = run_metamorphic_checks(args.metamorphic)
    permutation_failures = run_permutation_checks(args.permutations)
    engine_failures = run_engine_fuzz(args.engine_fuzz)
    virtual_probe = run_virtual_stock_probe()
    history_probe = run_history_corruption_probe()

    payload = {
        "engine_fuzz_requested": args.engine_fuzz,
        "brute_force_requested": args.brute_force,
        "brute_force_compared": brute_compared,
        "metamorphic_requested": args.metamorphic,
        "permutation_seeds": args.permutations,
        "boundary_failures": boundary_failures,
        "known_regression_failures": known_regression_failures,
        "brute_force_failures": brute_failures,
        "metamorphic_failures": metamorphic_failures,
        "permutation_failures": permutation_failures,
        "engine_failures": engine_failures,
        "virtual_stock_probe": virtual_probe,
        "history_corruption_probe": history_probe,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = sum(
        len(items)
        for items in (boundary_failures, known_regression_failures, brute_failures, metamorphic_failures, permutation_failures, engine_failures)
    )
    print(f"[audit] complete in {payload['elapsed_seconds']}s; invariant/oracle failures={failures}", flush=True)
    print(f"[audit] virtual-stock probe: {virtual_probe}", flush=True)
    print(f"[audit] history-corruption probe: {history_probe}", flush=True)
    print(f"[audit] JSON: {REPORT_PATH}", flush=True)
    # The virtual-stock probe is recorded separately because it is an explicit
    # product-policy issue, not a geometry invariant.
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

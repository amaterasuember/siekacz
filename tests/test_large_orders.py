"""Dense, large orders must preserve all instances and render the same geometry."""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.models import Project, SheetStock, SheetPart, OptimizationSettings
from core.layout_validation import assert_sheet_result
from workers.optimizer_worker import optimize_sheet_project


def test_large_identical_orders():
    for mode in ("comfort", "sport"):
        p = Project(
            sheet_stock=[SheetStock("M", 18, 1000, 2000, 10)],
            sheet_parts=[SheetPart("small", 20, 20, 1500, "M", 18)],
            settings=OptimizationSettings(kerf=5, kerf_tolerance=0, min_reusable_offcut_size=0, optimization_mode=mode, multi_core=False),
        )
        start = time.monotonic()
        result = optimize_sheet_project(p)
        elapsed = time.monotonic() - start
        assert_sheet_result(result, p.sheet_parts, 5)
        assert not result.unplaced_sheet_parts and not result.missing_sheet_layouts
        assert sum(len(l.parts) for l in result.sheet_layouts) == 1500
        assert len(result.sheet_layouts) == 1
        assert elapsed < 90, f"Large identical order took {elapsed:.1f}s"
        print(f"[OK] {mode}: 1500 parts, {elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    test_large_identical_orders()

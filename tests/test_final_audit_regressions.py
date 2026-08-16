"""Permanent regression gate for failures discovered by the final audit."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from final_audit_harness import run_history_corruption_probe, run_known_regressions, run_virtual_stock_probe
from core.models import OptimizationSettings, Project, SheetPart, SheetStock
from workers.optimizer_worker import optimize_sheet_project


def test_final_audit_regressions() -> None:
    assert not run_known_regressions()

    virtual = run_virtual_stock_probe()
    assert virtual["virtual_stock"] == []
    assert virtual["missing_layout_stock"] == []
    assert virtual["unplaced"] == [(1500, 200)]

    history = run_history_corruption_probe()
    assert history["write_blocked"] is True
    assert history["corrupt_source_preserved"] is True
    assert history["backup_created"] is True


def test_missing_sheet_keeps_non_rotatable_profile_orientation() -> None:
    project = Project(
        sheet_stock=[SheetStock("GRAIN", 1, 2000, 1000, 1, allow_rotation=False, grain_direction="x")],
        sheet_parts=[SheetPart("A", 1500, 600, 2, "GRAIN", 1, allow_rotation=False, grain_direction="x")],
        settings=OptimizationSettings(kerf=5, multi_core=False),
    )
    result = optimize_sheet_project(project)
    assert not result.unplaced_sheet_parts
    assert len(result.missing_sheet_layouts) == 1
    missing = result.missing_sheet_layouts[0].stock
    assert (missing.width, missing.height) == (2000, 1000)


if __name__ == "__main__":
    test_final_audit_regressions()
    test_missing_sheet_keeps_non_rotatable_profile_orientation()
    print("test_final_audit_regressions: OK")

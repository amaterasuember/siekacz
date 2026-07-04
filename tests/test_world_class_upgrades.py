"""Regression tests for the round of world-class upgrades.

Covers:
  T5-1: shared filler classification — old and new entry points agree.
  T5-2: log rotation handler is wired up.
  T5-3: SQLite migrations are idempotent and produce expected schema.
  T5-5: i18n facade returns translations and falls through for unknowns.
  T2-1: parallel ensemble produces a result ≥ as good as the single best.
  T2-6: estimated_cut_time_s is populated and aggregated.

These tests do NOT exercise the Qt UI — that would require an event loop.
UI changes (T1-2..T1-8, T1-3, T1-4) are smoke-tested through the existing
import chain in test_animations.py.
"""
from __future__ import annotations

import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.part_classification import (
    is_small_filler_part,
    strategic_dimension,
    stock_profile_key,
)
from algorithms.layout_scoring import (
    estimate_cut_time_seconds,
    format_cut_time,
    score_result,
)
from core.models import Project, ProjectMeta, OptimizationSettings, SheetStock, SheetPart
from core.i18n import (
    DEFAULT_LOCALE,
    available_locales,
    current_locale,
    missing_translations,
    set_locale,
    tr,
)


# ---------------------------------------------------------------------------
# T5-1: shared filler classification
# ---------------------------------------------------------------------------
def test_shared_filler_classification_unchanged() -> None:
    stock = SheetStock(material="MDF", thickness=18.0, width=2000.0, height=1000.0, quantity=5)
    # Truly tiny ⇒ filler.
    assert is_small_filler_part(SheetPart("tiny", 30.0, 30.0, 1, "MDF", 18.0), stock)
    # Strategic-limited (close to strategic side) ⇒ NOT a filler.
    assert not is_small_filler_part(SheetPart("big", 40.0, 900.0, 1, "MDF", 18.0), stock)
    # Mid-area ⇒ NOT a filler (area_ratio above threshold).
    assert not is_small_filler_part(SheetPart("mid", 400.0, 300.0, 1, "MDF", 18.0), stock)

    # Optimizer worker uses the same function ⇒ alias must point to it.
    from workers.optimizer_worker import _is_bonus_filler_for_missing_sheets
    assert _is_bonus_filler_for_missing_sheets is is_small_filler_part

    # Strategic dimension presets honoured.
    assert strategic_dimension(SheetStock("X", 18.0, 1000.0, 2000.0, 1)) == 1000.0
    assert strategic_dimension(SheetStock("X", 18.0, 2000.0, 1000.0, 1)) == 1000.0
    assert strategic_dimension(SheetStock("X", 18.0, 1000.0, 620.0, 1)) == 1000.0
    assert strategic_dimension(SheetStock("X", 18.0, 620.0, 1000.0, 1)) == 1000.0
    assert stock_profile_key(SheetStock("X", 18.0, 1500.0, 3000.0, 1)) == (1500, 3000)

    print("[OK] T5-1 shared filler classification stable")


# ---------------------------------------------------------------------------
# T5-2: log rotation
# ---------------------------------------------------------------------------
def test_log_rotation_handler_attached() -> None:
    # Importing app.logging_setup must not raise.  Run setup_logging into a
    # throwaway temp dir to verify the RotatingFileHandler is configured.
    import importlib
    import logging
    import database.db as db_mod
    from app import logging_setup

    tmp = tempfile.mkdtemp(prefix="siekacz_logtest_")
    db_mod.APP_DIR = Path(tmp) / ".cut_optimizer_desktop"
    db_mod.DB_PATH = db_mod.APP_DIR / "cut_optimizer.db"
    # Force-reload logging_setup so it picks up our patched APP_DIR.
    importlib.reload(logging_setup)
    logging_setup.setup_logging()
    handlers = logging.getLogger().handlers
    rot_handlers = [h for h in handlers if isinstance(h, RotatingFileHandler)]
    assert rot_handlers, "RotatingFileHandler not attached"
    h = rot_handlers[0]
    assert h.maxBytes > 0 and h.backupCount > 0, "Rotation params not set"
    print(f"[OK] T5-2 RotatingFileHandler: maxBytes={h.maxBytes} backups={h.backupCount}")


# ---------------------------------------------------------------------------
# T5-3: SQLite migrations
# ---------------------------------------------------------------------------
def test_db_migrations_idempotent() -> None:
    import importlib
    import database.db as db

    tmp = tempfile.mkdtemp(prefix="siekacz_dbtest_")
    db.APP_DIR = Path(tmp) / ".cut_optimizer_desktop"
    db.DB_PATH = db.APP_DIR / "cut_optimizer.db"
    db.init_db()
    db.init_db()  # second run must be a no-op
    with db.get_connection() as conn:
        versions = [row["version"] for row in conn.execute(
            "SELECT version FROM schema_version ORDER BY version"
        )]
        assert versions == sorted(versions) and versions[-1] >= 3, f"versions={versions}"
        cols = [c["name"] for c in conn.execute("PRAGMA table_info(settings)")]
        assert "updated_at" in cols, f"missing migration-3 column; cols={cols}"
        # Index exists?
        idx_rows = list(conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='leftovers_material_idx'"
        ))
        assert idx_rows, "leftovers_material_idx not created"
    print(f"[OK] T5-3 migrations applied; current version={versions[-1]}")


# ---------------------------------------------------------------------------
# T5-5: i18n
# ---------------------------------------------------------------------------
def test_i18n_translation_and_fallthrough() -> None:
    assert "pl" in available_locales() and "en" in available_locales()
    assert current_locale() == DEFAULT_LOCALE
    set_locale("en")
    try:
        assert tr("Oblicz rozkrój") == "Calculate layout"
        # Unknown text should fall through unchanged.
        assert tr("totally-unknown-string") == "totally-unknown-string"
        # Empty input is preserved.
        assert tr("") == ""
        assert missing_translations("en", ["Oblicz rozkrój", "NOT_REGISTERED"]) == ["NOT_REGISTERED"]
    finally:
        set_locale("pl")
    print("[OK] T5-5 i18n facade works (PL/EN with fallthrough)")


# ---------------------------------------------------------------------------
# T2-1: ensemble parity
# ---------------------------------------------------------------------------
def test_ensemble_at_least_as_good_as_default() -> None:
    from workers.optimizer_worker import optimize_sheet_project, _SHEET_ALGORITHMS

    project = Project(
        meta=ProjectMeta(),
        sheet_stock=[SheetStock("MDF", 18.0, 2000.0, 1000.0, quantity=5, allow_rotation=True)],
        sheet_parts=[
            SheetPart("A", 400.0, 300.0, 8, "MDF", 18.0),
            SheetPart("B", 200.0, 200.0, 12, "MDF", 18.0),
            SheetPart("C", 60.0, 60.0, 30, "MDF", 18.0),
        ],
        settings=OptimizationSettings(),
    )

    # Run default (vertical_segmented).
    default_result = optimize_sheet_project(project)
    default_score = score_result(default_result, kerf=project.settings.kerf,
                                 min_reusable_size=project.settings.min_reusable_offcut_size).value

    # Run ensemble (auto).
    project.settings.algorithm = "auto"
    ensemble_result = optimize_sheet_project(project)
    ensemble_score = score_result(ensemble_result, kerf=project.settings.kerf,
                                  min_reusable_size=project.settings.min_reusable_offcut_size).value

    # The ensemble must NEVER produce a strictly-worse score than the default,
    # because vertical_segmented is one of the ensemble candidates.
    assert ensemble_score <= default_score, (
        f"ensemble worse than default!\n  default = {default_score}\n  ensemble = {ensemble_score}"
    )

    # The winning algorithm name must be annotated.
    assert "ensemble" in ensemble_result.algorithm.lower(), (
        f"algorithm field not annotated: {ensemble_result.algorithm!r}"
    )
    assert any("Ensemble" in m for m in ensemble_result.messages), (
        f"no ensemble message: {ensemble_result.messages}"
    )

    print(f"[OK] T2-1 ensemble picked: {ensemble_result.algorithm}")
    print(f"      Algorithms in registry: {[name for name, _ in _SHEET_ALGORITHMS]}")


# ---------------------------------------------------------------------------
# T2-6: cut-time estimation
# ---------------------------------------------------------------------------
def test_estimated_cut_time_populated() -> None:
    from workers.optimizer_worker import optimize_sheet_project

    project = Project(
        meta=ProjectMeta(),
        sheet_stock=[SheetStock("MDF", 18.0, 2000.0, 1000.0, quantity=5, allow_rotation=True)],
        sheet_parts=[SheetPart("A", 400.0, 300.0, 10, "MDF", 18.0)],
        settings=OptimizationSettings(),
    )
    result = optimize_sheet_project(project)
    # Every drawn layout should have a non-negative cut-time estimate.
    assert all(l.estimated_cut_time_s >= 0.0 for l in result.sheet_layouts), \
        "negative cut time on some layout"
    # Aggregated result-level total must match the sum of per-sheet times.
    expected = sum(l.estimated_cut_time_s for l in result.sheet_layouts) + \
               sum(l.estimated_cut_time_s for l in result.missing_sheet_layouts)
    assert abs(result.total_estimated_cut_time_s - expected) < 1e-6, (
        f"total mismatch: {result.total_estimated_cut_time_s} vs sum {expected}"
    )

    # Direct estimator behavior.
    assert estimate_cut_time_seconds(0.0, 0) == 0.0
    assert estimate_cut_time_seconds(1000.0, 5) > estimate_cut_time_seconds(1000.0, 1)
    assert format_cut_time(0.0) == "<1 s"
    assert format_cut_time(90.0).startswith("~1 min")
    print(f"[OK] T2-6 cut-time estimate: {format_cut_time(result.total_estimated_cut_time_s)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    test_shared_filler_classification_unchanged()
    test_log_rotation_handler_attached()
    test_db_migrations_idempotent()
    test_i18n_translation_and_fallthrough()
    test_ensemble_at_least_as_good_as_default()
    test_estimated_cut_time_populated()
    print("\ntest_world_class_upgrades: OK")


if __name__ == "__main__":
    main()

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable


APP_DIR = Path.home() / ".cut_optimizer_desktop"
DB_PATH = APP_DIR / "cut_optimizer.db"

_logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_schema_version_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


# ---------------------------------------------------------------------------
# Migrations.  Each entry is (version, description, callable(connection)).
# Migrations run in ascending order, each in its own transaction.  Versions
# must be strictly increasing.  NEVER edit/remove a migration after it ships —
# always append a new one.
# ---------------------------------------------------------------------------
def _migration_001_base_schema(connection: sqlite3.Connection) -> None:
    """Initial schema: project_history, leftovers, settings."""
    schema_path = Path(__file__).with_name("schema.sql")
    connection.executescript(schema_path.read_text(encoding="utf-8"))


def _migration_002_leftover_dimensions_index(connection: sqlite3.Connection) -> None:
    """Add index on (material, thickness, status) for leftovers — speeds lookups."""
    connection.execute(
        "CREATE INDEX IF NOT EXISTS leftovers_material_idx "
        "ON leftovers (material, thickness, status)"
    )


def _migration_003_settings_updated_at(connection: sqlite3.Connection) -> None:
    """Add updated_at column to settings (idempotent, NULL-safe)."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(settings)")}
    if "updated_at" not in columns:
        connection.execute("ALTER TABLE settings ADD COLUMN updated_at TEXT")


def _migration_004_history_date_index(connection: sqlite3.Connection) -> None:
    """Add index on project_history.saved_at for fast chronological sorting.

    The history tab sorts by saved_at (TEXT ISO-8601) — without an index this
    requires a full table scan on every open, which becomes noticeable after a
    few hundred saved projects.
    """
    connection.execute(
        "CREATE INDEX IF NOT EXISTS project_history_saved_at_idx "
        "ON project_history (saved_at DESC)"
    )


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "base schema", _migration_001_base_schema),
    (2, "leftovers index", _migration_002_leftover_dimensions_index),
    (3, "settings updated_at column", _migration_003_settings_updated_at),
    (4, "project_history saved_at index", _migration_004_history_date_index),
]


def _apply_pending_migrations(connection: sqlite3.Connection) -> None:
    _ensure_schema_version_table(connection)
    current = _current_version(connection)
    for version, description, fn in MIGRATIONS:
        if version <= current:
            continue
        _logger.info("DB migration %d: %s", version, description)
        try:
            with connection:  # atomic per migration
                fn(connection)
                connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
        except sqlite3.Error:
            _logger.exception("Migration %d (%s) failed", version, description)
            raise


def init_db() -> None:
    with get_connection() as connection:
        _apply_pending_migrations(connection)

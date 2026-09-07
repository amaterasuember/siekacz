"""Commit, rollback and deterministic SQLite handle release."""
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import db, repositories


def test_database_transactions():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with patch.object(db, "APP_DIR", root), patch.object(db, "DB_PATH", root / "qa.db"):
            db.init_db()
            repositories.set_setting("keep", "original")
            try:
                with db.get_connection() as connection:
                    connection.execute("UPDATE settings SET value = ? WHERE key = ?", ('"changed"', "keep"))
                    raise ValueError("rollback")
            except ValueError:
                pass
            assert repositories.get_setting("keep") == "original"
            try:
                connection.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                pass
            else:
                raise AssertionError("Connection was left open")
            repositories.set_setting("keep", "committed")
            assert repositories.get_setting("keep") == "committed"
            db.init_db()  # Migrations remain idempotent.
            db.DB_PATH.unlink()  # Windows rejects this while a handle is open.


if __name__ == "__main__":
    test_database_transactions()
    print("[OK] SQLite commit, rollback and handle cleanup")

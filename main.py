from __future__ import annotations

import sys
import multiprocessing as mp
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import expiry, integrity
from app.logging_setup import setup_logging
from app.simple_window import SimpleCutWindow
from app.theme import apply_native_title_bar, apply_theme
from app.version import APP_VERSION
from database.db import init_db
from database.repositories import get_setting, set_setting


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def migrate_display_orientation() -> None:
    if get_setting("display_orientation_height_axis_migration_done", False):
        return
    set_setting("display_orientation", "horizontal")
    set_setting("display_orientation_height_axis_migration_done", True)


def ensure_default_preferences() -> None:
    if get_setting("theme") is None:
        set_setting("theme", "dark")
    if get_setting("optimization_mode") is None:
        set_setting("optimization_mode", "comfort")


def _disable_windows_ghosting() -> None:
    """Prevent Windows from replacing the app window with a white 'ghost'
    when the message queue is temporarily busy (e.g. during result rendering).
    No-op on non-Windows platforms."""
    try:
        import ctypes
        ctypes.windll.user32.DisableProcessWindowsGhosting()
    except Exception:
        pass


def main() -> int:
    _disable_windows_ghosting()
    setup_logging()
    init_db()
    migrate_display_orientation()
    ensure_default_preferences()
    app = QApplication(sys.argv)
    app.setApplicationName("SIEKACZ 9000")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Workshop Tools")
    icon = QIcon(str(resource_path("assets/app_icon.ico")))
    if not icon.isNull():
        app.setWindowIcon(icon)
    app.setProperty("optimization_mode", "sport" if get_setting("optimization_mode", "comfort") == "sport" else "comfort")
    apply_theme(app, get_setting("theme", "dark"))
    window = SimpleCutWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    apply_native_title_bar(window, get_setting("theme", "dark"))
    integrity.check_and_warn(window)
    if not expiry.check_and_prompt(window):
        return 0
    return app.exec()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())

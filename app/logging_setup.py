from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from database.db import APP_DIR


LOG_PATH = APP_DIR / "siekacz9000.log"

# Rotate at 5 MB; keep 3 historical files (siekacz9000.log.1, .2, .3).
# Prevents unbounded log growth in production deployments.
_MAX_LOG_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def setup_logging() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers if setup_logging is called twice.
    if not any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")) == LOG_PATH
        for h in root_logger.handlers
    ):
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=_MAX_LOG_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root_logger.addHandler(handler)

    logging.info("SIEKACZ 9000 start")

    previous_hook = sys.excepthook

    def _log_unhandled(exc_type, exc_value, exc_traceback):
        logging.exception("Nieobsługiwany błąd aplikacji", exc_info=(exc_type, exc_value, exc_traceback))
        previous_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _log_unhandled


def log_path() -> Path:
    return LOG_PATH

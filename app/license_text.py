from __future__ import annotations

"""Single source of truth for the proprietary SIEKACZ 9000 license."""

import sys
from pathlib import Path


LICENSE_NAME = "Bezpłatna licencja użytkowania, w tym komercyjnego (Beta)"
LICENSE_URL = "https://github.com/amaterasuember/siekacz/blob/main/LICENSE"
BUY_ME_COFFEE_URL = "https://buymeacoffee.com/kewinwojcik"


def _license_path() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / "LICENSE"


def full_license_text() -> str:
    """Return the packaged EULA, with a useful fallback for damaged installs."""
    try:
        return _license_path().read_text(encoding="utf-8")
    except OSError:
        return (
            "Nie udalo sie odczytac pliku licencji. Pobierz oficjalna kopie z:\n"
            f"{LICENSE_URL}"
        )

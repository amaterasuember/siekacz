from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.license_text import full_license_text


def run() -> None:
    text = full_license_text()
    assert "SIEKACZ 9000" in text
    assert "Wersja 1.1" in text
    assert "komercyjne" in text.lower()
    assert "sprzedawa" in text.lower()
    assert "inżynierii wstecznej" in text.lower()

    root = Path(__file__).resolve().parents[1]
    assert "ensure_current_license_accepted" not in (root / "main.py").read_text(encoding="utf-8")
    assert "LicenseFile=" not in (root / "installer_src" / "SIEKACZ9000.iss").read_text(encoding="utf-8")
    print("License visibility checks passed")


if __name__ == "__main__":
    run()

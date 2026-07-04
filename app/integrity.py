"""
Ochrona integralności plików SIEKACZ 9000.

Budowanie (wywołuje BUILD_EXE.bat przed PyInstaller):
    python -m app.integrity

Uruchomienie (wywoływane z main.py po pokazaniu okna):
    from app import integrity
    integrity.check_and_warn(parent_widget)

Jak działa:
  - Przy budowaniu skrypt generuje app/_manifest.json z hashami SHA-256
    chronionych plików (licencja, wersja, okno główne).
  - Przy starcie aplikacji hasze są porównywane z manifestem.
  - Jeśli cokolwiek zostało zmienione, użytkownik widzi ostrzeżenie
    z linkiem do oficjalnego źródła.
  - Brak manifestu (tryb deweloperski) = cisza.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = Path(__file__).parent / "_manifest.json"

# Pliki, których integralność chroni licencję i markę.
_PROTECTED: list[str] = [
    "main.py",
    "app/integrity.py",
    "app/expiry.py",
    "app/license_text.py",
    "app/version.py",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def generate() -> None:
    """Generuje manifest — wywołaj raz przed zbudowaniem EXE."""
    data: dict[str, str] = {}
    for rel in _PROTECTED:
        p = _ROOT / rel
        if p.exists():
            data[rel] = _sha256(p)
        else:
            print(f"[integrity] WARN: nie znaleziono {rel}")
    _MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[integrity] Manifest zapisany → {_MANIFEST}")
    for rel, h in data.items():
        print(f"  {h[:12]}…  {rel}")


def verify() -> list[str]:
    """Zwraca listę zmienionych/brakujących plików (pusta = OK)."""
    if not _MANIFEST.exists():
        return []
    try:
        manifest: dict[str, str] = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return []
    tampered: list[str] = []
    for rel, expected in manifest.items():
        p = _ROOT / rel
        if not p.exists() or _sha256(p) != expected:
            tampered.append(rel)
    return tampered


def check_and_warn(parent=None) -> None:  # type: ignore[assignment]
    """Wyświetla ostrzeżenie, jeśli jakikolwiek chroniony plik został zmieniony."""
    tampered = verify()
    if not tampered:
        return

    import logging
    logging.getLogger(__name__).warning("Integrity check FAILED — zmienione pliki: %s", tampered)

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(parent)
        box.setWindowTitle("SIEKACZ 9000 — Nieoficjalna wersja")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "<b>Wykryto zmodyfikowane pliki programu.</b><br><br>"
            "Ta kopia może być nieoficjalną lub zmodyfikowaną wersją "
            "SIEKACZ 9000, co jest niezgodne z licencją.<br><br>"
            "Pobierz oryginalną wersję ze strony:<br>"
            "<a href='https://github.com/amaterasuember/siekacz/releases'>"
            "github.com/amaterasuember/siekacz/releases</a>"
        )
        box.setDetailedText(
            "Zmienione lub brakujące pliki:\n" + "\n".join(tampered)
        )
        box.exec()
    except Exception:
        pass


if __name__ == "__main__":
    generate()

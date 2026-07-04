"""Lightweight in-process translation layer (T5-5).

Why a custom layer instead of Qt Linguist:
  - Qt Linguist requires a build step (lupdate/lrelease) and shipping .qm
    files alongside the executable.  This complicates the PyInstaller
    build pipeline already in use.
  - The app currently has only ~hundreds of UI strings, all in one
    codebase.  A simple Python dict is enough for the first locale
    expansion (PL → EN) and trivially extensible to DE/FR/ES later.

Usage:
    from core.i18n import tr, set_locale
    label.setText(tr("Oblicz rozkrój"))
    set_locale("en")  # switches the entire app

Unknown strings fall through to the source text — so wrapping a string
in ``tr()`` is always safe, even before the translation is added.

Persistence:
    Locale is read from / written to the same SQLite ``settings`` table
    used by the rest of the app.  Default = "pl".
"""
from __future__ import annotations

import logging
import threading
from typing import Iterable

_logger = logging.getLogger(__name__)

# Supported locales.  "pl" is the source language (identity translation),
# so its dict is empty; other locales map Polish source → translated text.
SUPPORTED_LOCALES: tuple[str, ...] = ("pl", "en")
DEFAULT_LOCALE: str = "pl"


# ---------------------------------------------------------------------------
# Catalogs.  Add new strings here as you wrap them with tr().  For brevity,
# only the most user-visible strings are seeded — the rest fall through to
# Polish.  Adding a translation is non-breaking: code that wraps untranslated
# strings still works, the user simply sees the source.
# ---------------------------------------------------------------------------
_CATALOGS: dict[str, dict[str, str]] = {
    "pl": {},
    "en": {
        # Window / toolbar
        "Oblicz rozkrój": "Calculate layout",
        "Liczenie...": "Calculating...",
        "Anuluj": "Cancel",
        "Anulowanie...": "Cancelling...",
        "Anulowanie obliczeń...": "Cancelling calculation...",
        "Obliczenia anulowane": "Calculation cancelled",
        "Nie mogę policzyć": "Cannot calculate",
        "Liczenie rozkroju...": "Calculating layout...",
        # Tabs
        "Formatki": "Parts",
        "Dodaj formatkę": "Add part",
        "Usuń zaznaczone": "Remove selected",
        "Szablon CSV": "CSV template",
        "Zapisz szablon formatek": "Save parts template",
        # Sheet nav
        "Płyta": "Sheet",
        "Brakująca": "Missing",
        # Legend
        "Legenda formatek": "Parts legend",
        "Legenda formatek (kliknij, aby podświetlić)": "Parts legend (click to highlight)",
        # History
        "Klient:": "Client:",
        "Materiał:": "Material:",
        "Sortuj:": "Sort:",
        "Wszyscy klienci": "All clients",
        "Wszystkie materiały": "All materials",
        "Najnowsze najpierw": "Newest first",
        "Najstarsze najpierw": "Oldest first",
        "Nazwa A→Z": "Name A→Z",
        "Nazwa Z→A": "Name Z→A",
        "Wykorzystanie ↓": "Utilization ↓",
        "Liczba płyt ↑": "Sheet count ↑",
        "Szukaj projektu, klienta lub trybu...": "Search project, client or mode...",
        "Bez klienta": "No client",
        # Zoom
        "Aktualne powiększenie. Kliknij, aby zresetować do 100%.":
            "Current zoom. Click to reset to 100%.",
        "Przybliż podgląd  (+)": "Zoom in  (+)",
        "Oddal podgląd  (−)": "Zoom out  (−)",
        "Dopasuj cały rozkrój do widoku": "Fit layout to view",
        # Status bar
        "Jednostki: mm": "Units: mm",
        "Cięcia": "Cuts",
        "Wykorzystanie": "Utilization",
        "Płyty": "Sheets",
        "Czas cięcia": "Cut time",
    },
}

# Per-thread lock for set_locale.  The Qt main loop is single-threaded so
# contention is essentially zero, but worker threads may emit log strings.
_lock = threading.RLock()
_current_locale: str = DEFAULT_LOCALE


def available_locales() -> tuple[str, ...]:
    return SUPPORTED_LOCALES


def current_locale() -> str:
    return _current_locale


def set_locale(locale: str) -> None:
    """Switch the active locale.  Falls back to default if unknown."""
    global _current_locale
    if locale not in _CATALOGS:
        _logger.warning("Unknown locale %r; falling back to %r", locale, DEFAULT_LOCALE)
        locale = DEFAULT_LOCALE
    with _lock:
        _current_locale = locale


def tr(source: str) -> str:
    """Translate *source* (Polish) into the active locale.

    Missing translations transparently fall through to the source string,
    so wrapping new UI text in ``tr()`` is always safe even before a
    translation has been authored.
    """
    if not source:
        return source
    catalog = _CATALOGS.get(_current_locale)
    if not catalog:
        return source
    return catalog.get(source, source)


def register_translations(locale: str, entries: dict[str, str]) -> None:
    """Merge *entries* into *locale*'s catalog (useful for plugins/tests)."""
    if locale not in _CATALOGS:
        _CATALOGS[locale] = {}
    _CATALOGS[locale].update(entries)


def translations_for(locale: str) -> dict[str, str]:
    return dict(_CATALOGS.get(locale, {}))


def missing_translations(locale: str, sources: Iterable[str]) -> list[str]:
    """Return source strings that lack a translation in *locale*.

    Useful for the test suite that audits translation coverage."""
    catalog = _CATALOGS.get(locale, {})
    return [s for s in sources if s and s not in catalog]

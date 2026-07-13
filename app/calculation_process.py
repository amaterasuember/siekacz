from __future__ import annotations

from multiprocessing.connection import Connection

from core.models import Project
from workers.optimizer_worker import optimize_sheet_order, optimize_sheet_project


def _friendly_calculation_error(exc: BaseException) -> str:
    raw = str(exc).strip()
    if isinstance(exc, MemoryError):
        return (
            "Zabraklo pamieci RAM podczas obliczen.\n"
            "Sprobuj zmniejszyc liczbe formatek albo podzielic zlecenie na mniejsze partie."
        )
    if isinstance(exc, RecursionError):
        return (
            "Przekroczono limit zagniezdzen rekurencji.\n"
            "Zglos ten przypadek - szczegoly sa w pliku dziennika."
        )
    if isinstance(exc, ValueError) and raw:
        return raw
    if raw and len(raw) < 300 and "\n" not in raw:
        return f"Blad podczas obliczen: {raw}\n\nSzczegoly w pliku dziennika."
    return (
        "Wystapil nieoczekiwany blad podczas obliczen.\n"
        f"Typ bledu: {type(exc).__name__}\n\n"
        "Szczegoly zostaly zapisane w pliku dziennika."
    )


def run_calculation_payload(project: Project | list[Project], progress_callback=None):
    if isinstance(project, list):
        return optimize_sheet_order(project, progress_callback=progress_callback)
    return optimize_sheet_project(project, progress_callback=progress_callback)


def calculation_process_entry(project: Project | list[Project], conn: Connection) -> None:
    """Run optimization in a killable child process and stream real phase progress."""
    def report_progress(percent: int, label: str) -> None:
        conn.send(("progress", (int(percent), str(label))))

    try:
        report_progress(0, "Uruchamiam obliczenia")
        conn.send(("result", run_calculation_payload(project, progress_callback=report_progress)))
    except BaseException as exc:
        conn.send(("error", _friendly_calculation_error(exc)))
    finally:
        conn.close()

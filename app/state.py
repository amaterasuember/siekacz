from __future__ import annotations

import json
import logging
from pathlib import Path

from core.models import Project
from core.atomic_file import atomic_write_text
from database import repositories


AUTOSAVE_PATH = Path.home() / ".cut_optimizer_desktop" / "autosave_project.json"

_logger = logging.getLogger(__name__)


class AppState:
    def __init__(self) -> None:
        self.project = Project()
        self.project_path: Path | None = None
        self.last_result = None

    def new_project(self) -> None:
        self.project = Project()
        self.project_path = None
        self.last_result = None

    def load(self, path: str | Path) -> None:
        path = Path(path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"Nie można otworzyć pliku projektu:\n{path}\n\n{exc}"
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Plik projektu jest uszkodzony lub nie jest prawidłowym plikiem JSON:\n{path}\n\n{exc}"
            ) from exc
        try:
            self.project = Project.from_dict(data)
        except Exception as exc:
            _logger.exception("Project.from_dict failed for %s", path)
            raise ValueError(
                f"Nie można wczytać projektu z pliku:\n{path}\n\n"
                "Plik może pochodzić z niekompatybilnej wersji programu.\n"
                f"Szczegóły: {exc}"
            ) from exc
        self.project_path = path
        self.last_result = None

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.project_path
        if target is None:
            raise ValueError("A project path is required.")
        try:
            atomic_write_text(target, json.dumps(self.project.to_dict(), indent=2, allow_nan=False))
        except OSError as exc:
            raise ValueError(
                f"Nie można zapisać pliku projektu:\n{target}\n\n{exc}"
            ) from exc
        self.project_path = target
        repositories.record_project(str(target), self.project.meta.client_name, self.project.meta.order_number, self.project.meta.material)
        return target

    def autosave(self) -> None:
        try:
            AUTOSAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(AUTOSAVE_PATH, json.dumps(self.project.to_dict(), indent=2, allow_nan=False))
        except Exception as exc:
            # Autosave failures are non-fatal — log but don't interrupt the user.
            _logger.warning("Autosave failed: %s", exc)

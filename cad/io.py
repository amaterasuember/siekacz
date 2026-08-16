from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from cad.model import SCHEMA_VERSION, CadDocument, CadValidationError


PROJECT_EXTENSION = ".siekcad"
MAX_PROJECT_BYTES = 50 * 1024 * 1024


class CadIoError(ValueError):
    """A safe file error suitable for presenting in the CAD UI."""


def migrate_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CadIoError("Plik projektu CAD ma nieprawidłową strukturę.")
    try:
        version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise CadIoError("Wersja schematu projektu CAD jest nieprawidłowa.") from exc
    if version < 1 or version > SCHEMA_VERSION:
        raise CadIoError(f"Nieobsługiwana wersja schematu CAD: {version!r}.")
    migrated = json.loads(json.dumps(payload))
    if version == 1:
        document = migrated.get("document")
        if not isinstance(document, dict):
            raise CadIoError("W pliku brakuje dokumentu CAD.")
        objects = document.get("objects", [])
        if not isinstance(objects, list):
            raise CadIoError("Lista obiektów CAD jest nieprawidłowa.")
        for item in objects:
            if isinstance(item, dict):
                item.setdefault("constraints", [])
        migrated["schema_version"] = 2
        version = 2
    if version == 2:
        document = migrated.get("document")
        if not isinstance(document, dict):
            raise CadIoError("W pliku brakuje dokumentu CAD.")
        objects = document.get("objects", [])
        if not isinstance(objects, list):
            raise CadIoError("Lista obiektów CAD jest nieprawidłowa.")
        for item in objects:
            if isinstance(item, dict):
                item.setdefault("patterns", [])
        migrated["schema_version"] = 3
        version = 3
    if version == 3:
        document = migrated.get("document")
        if not isinstance(document, dict):
            raise CadIoError("W pliku brakuje dokumentu CAD.")
        legacy_parameters = document.get("parameters", {})
        if not isinstance(legacy_parameters, dict):
            raise CadIoError("Lista parametrów CAD jest nieprawidłowa.")
        document["parameters"] = [
            {
                "id": str(uuid.uuid4()),
                "name": str(name),
                "expression": f"{float(value):.17g} mm",
                "value": float(value),
                "scope_object_id": "",
                "unit_kind": "length",
                "bindings": [],
            }
            for name, value in legacy_parameters.items()
        ]
        objects = document.get("objects", [])
        if not isinstance(objects, list):
            raise CadIoError("Lista obiektów CAD jest nieprawidłowa.")
        for item in objects:
            if not isinstance(item, dict):
                continue
            constraints = item.get("constraints", [])
            if not isinstance(constraints, list):
                raise CadIoError("Lista więzów CAD jest nieprawidłowa.")
            for constraint in constraints:
                if isinstance(constraint, dict):
                    constraint.setdefault("expression", "")
                    constraint.setdefault("parameter_bindings", [])
        migrated["schema_version"] = 4
        version = 4
    if version != SCHEMA_VERSION:
        raise CadIoError(f"Brakuje migracji schematu CAD {version} → {SCHEMA_VERSION}.")
    return migrated


def normalized_project_path(path: str | Path) -> Path:
    target = Path(path)
    return target if target.suffix.lower() == PROJECT_EXTENSION else target.with_suffix(PROJECT_EXTENSION)


def save_document(document: CadDocument, path: str | Path) -> Path:
    target = normalized_project_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = document.to_dict()
    payload["application"] = {"name": "SIEKACZ 9000", "format": "native-cad"}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_PROJECT_BYTES:
        raise CadIoError("Projekt CAD przekracza bezpieczny limit rozmiaru pliku.")
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CadIoError(f"Nie można zapisać projektu CAD: {exc}") from exc
    return target


def load_document(path: str | Path) -> CadDocument:
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise CadIoError(f"Nie można otworzyć projektu CAD: {exc}") from exc
    if size > MAX_PROJECT_BYTES:
        raise CadIoError("Projekt CAD przekracza bezpieczny limit rozmiaru pliku.")
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CadIoError(f"Projekt CAD jest uszkodzony lub nie jest prawidłowym JSON: {exc}") from exc
    try:
        document = CadDocument.from_dict(migrate_payload(payload))
        from cad.parameter_engine import recompute_parameters

        recompute_parameters(document)
        document.recompute()
        return document
    except CadValidationError as exc:
        raise CadIoError(f"Projekt CAD nie przeszedł walidacji: {exc}") from exc

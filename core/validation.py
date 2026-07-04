from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .models import LinearPart, LinearStock, OptimizationSettings, Project, SheetPart, SheetStock


def safeNumber(value: object, fallback: float | None = None) -> float | None:
    """Parse user-entered numeric text without leaking NaN/Infinity downstream."""
    if value is None:
        return fallback
    if isinstance(value, bool):
        return fallback
    try:
        if isinstance(value, str):
            text = value.strip().replace(",", ".")
            if not text:
                return fallback
            number = float(text)
        else:
            number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _safe_int(value: object) -> int | None:
    number = safeNumber(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _field(data: Any, key: str, default: object = "") -> object:
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def _clean_text(value: object, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def validatePlate(data: Any, row: int = 1) -> tuple[SheetStock | None, list[str]]:
    errors: list[str] = []
    width = safeNumber(_field(data, "width"))
    height = safeNumber(_field(data, "height"))
    quantity = _safe_int(_field(data, "quantity"))
    if width is None or height is None or quantity is None:
        errors.append(f"Popraw płytę w wierszu {row}. Wpisz szerokość, wysokość i całkowitą ilość.")
    elif width <= 0 or height <= 0 or quantity <= 0:
        errors.append(f"Popraw płytę w wierszu {row}. Wymiary i ilość muszą być większe od zera.")
    if errors:
        return None, errors
    assert width is not None and height is not None and quantity is not None
    return (
        SheetStock(
            material=_clean_text(_field(data, "material", "standard")) or "standard",
            thickness=safeNumber(_field(data, "thickness", 1), 1) or 1,
            width=width,
            height=height,
            quantity=quantity,
            price=safeNumber(_field(data, "price", 0), 0) or 0,
            grain_direction=_clean_text(_field(data, "grain_direction", "none")) or "none",
            allow_rotation=bool(_field(data, "allow_rotation", True)),
            min_offcut_width=max(0.0, safeNumber(_field(data, "min_offcut_width", 0), 0) or 0),
            min_offcut_height=max(0.0, safeNumber(_field(data, "min_offcut_height", 0), 0) or 0),
            source=_clean_text(_field(data, "source", "stock")) or "stock",
            nominal_width=safeNumber(_field(data, "nominal_width", width), width) or width,
            nominal_height=safeNumber(_field(data, "nominal_height", height), height) or height,
            sheet_allowance=max(0.0, safeNumber(_field(data, "sheet_allowance", 0), 0) or 0),
        ),
        [],
    )


def validatePart(data: Any, row: int = 1) -> tuple[SheetPart | None, list[str]]:
    errors: list[str] = []
    width = safeNumber(_field(data, "width"))
    height = safeNumber(_field(data, "height"))
    quantity = _safe_int(_field(data, "quantity"))
    if width is None or height is None or quantity is None:
        errors.append(f"Popraw wiersz {row}. Wpisz poprawne liczby: szerokość, długość i całkowitą ilość.")
    elif width <= 0 or height <= 0 or quantity <= 0:
        errors.append(f"Popraw wiersz {row}. Szerokość, długość i ilość muszą być większe od zera.")
    if errors:
        return None, errors
    assert width is not None and height is not None and quantity is not None
    name = _clean_text(_field(data, "name")) or f"{width:.0f} x {height:.0f}"
    return (
        SheetPart(
            name=name,
            width=width,
            height=height,
            quantity=quantity,
            material=_clean_text(_field(data, "material", "standard")) or "standard",
            thickness=safeNumber(_field(data, "thickness", 1), 1) or 1,
            allow_rotation=bool(_field(data, "allow_rotation", True)),
            grain_direction=_clean_text(_field(data, "grain_direction", "none")) or "none",
            label=_clean_text(_field(data, "label", name)) or name,
            priority=int(safeNumber(_field(data, "priority", 0), 0) or 0),
            notes=_clean_text(_field(data, "notes", "")),
        ),
        [],
    )


def validateProjectInput(project: Project, max_total_parts: int = 5000) -> list[str]:
    errors: list[str] = []
    settings = project.settings if isinstance(project.settings, OptimizationSettings) else OptimizationSettings()
    kerf = safeNumber(settings.kerf)
    if kerf is None or kerf < 0:
        errors.append("Rzaz / kerf musi być poprawną, nieujemną liczbą.")
    if settings.job_type == "sheet":
        if not project.sheet_stock:
            errors.append("Dodaj przynajmniej jedną płytę.")
        if not project.sheet_parts:
            errors.append("Brak formatek do rozkroju.")
        total_quantity = 0
        for index, stock in enumerate(project.sheet_stock, 1):
            _stock, row_errors = validatePlate(stock, index)
            errors.extend(row_errors)
        for index, part in enumerate(project.sheet_parts, 1):
            _part, row_errors = validatePart(part, index)
            errors.extend(row_errors)
            total_quantity += max(0, _safe_int(_field(part, "quantity", 0)) or 0)
        if total_quantity > max_total_parts:
            errors.append(
                f"Za dużo formatek naraz ({total_quantity}). Podziel zlecenie albo zmniejsz ilość do {max_total_parts} szt."
            )
    return errors


def sanitizeProjectState(data: Any, max_total_parts: int = 5000) -> Project:
    if isinstance(data, Project):
        project = data
    elif isinstance(data, dict):
        try:
            project = Project.from_dict(data)
        except Exception:
            project = Project()
    else:
        project = Project()

    safe_stock: list[SheetStock] = []
    for index, item in enumerate(list(project.sheet_stock), 1):
        stock, _errors = validatePlate(item, index)
        if stock:
            safe_stock.append(stock)
    safe_parts: list[SheetPart] = []
    total = 0
    for index, item in enumerate(list(project.sheet_parts), 1):
        part, _errors = validatePart(item, index)
        if not part:
            continue
        if total + part.quantity > max_total_parts:
            break
        total += part.quantity
        safe_parts.append(part)
    project.sheet_stock = safe_stock
    project.sheet_parts = safe_parts
    project.settings.kerf = max(0.0, safeNumber(project.settings.kerf, 0.0) or 0.0)
    project.settings.sheet_allowance = max(0.0, safeNumber(project.settings.sheet_allowance, 0.0) or 0.0)
    project.settings.min_reusable_offcut_size = max(0.0, safeNumber(project.settings.min_reusable_offcut_size, 0.0) or 0.0)
    return project


def getValidationErrors(project: Project, max_total_parts: int = 5000) -> list[str]:
    return validateProjectInput(project, max_total_parts=max_total_parts)


def positive_number(value: float, name: str) -> str | None:
    return f"{name} must be positive." if value <= 0 else None


def positive_int(value: int, name: str) -> str | None:
    return f"{name} must be a positive integer." if value <= 0 else None


def validate_sheet_stock(items: Iterable[SheetStock]) -> list[str]:
    errors: list[str] = []
    for i, item in enumerate(items, 1):
        for field_name in ("width", "height", "thickness", "price"):
            value = getattr(item, field_name)
            if field_name != "price":
                error = positive_number(float(value), f"Sheet stock row {i} {field_name}")
                if error:
                    errors.append(error)
        error = positive_int(int(item.quantity), f"Sheet stock row {i} quantity")
        if error:
            errors.append(error)
    return errors


def validate_linear_stock(items: Iterable[LinearStock]) -> list[str]:
    errors: list[str] = []
    for i, item in enumerate(items, 1):
        for field_name in ("length",):
            error = positive_number(float(getattr(item, field_name)), f"Linear stock row {i} {field_name}")
            if error:
                errors.append(error)
        error = positive_int(int(item.quantity), f"Linear stock row {i} quantity")
        if error:
            errors.append(error)
    return errors


def validate_sheet_parts(parts: Iterable[SheetPart], stock: Iterable[SheetStock]) -> list[str]:
    errors: list[str] = []
    stock_list = list(stock)
    for i, part in enumerate(parts, 1):
        for field_name in ("width", "height", "thickness"):
            error = positive_number(float(getattr(part, field_name)), f"Sheet part row {i} {field_name}")
            if error:
                errors.append(error)
        error = positive_int(int(part.quantity), f"Sheet part row {i} quantity")
        if error:
            errors.append(error)
        fits = any(
            s.material == part.material
            and abs(s.thickness - part.thickness) < 0.001
            and (
                (part.width <= s.width and part.height <= s.height)
                or (part.allow_rotation and s.allow_rotation and part.height <= s.width and part.width <= s.height)
            )
            for s in stock_list
        )
        if stock_list and not fits:
            errors.append(f"Sheet part '{part.name}' does not fit any matching stock sheet.")
    return errors


def validate_linear_parts(parts: Iterable[LinearPart], stock: Iterable[LinearStock]) -> list[str]:
    errors: list[str] = []
    stock_list = list(stock)
    for i, part in enumerate(parts, 1):
        error = positive_number(float(part.length), f"Linear part row {i} length")
        if error:
            errors.append(error)
        error = positive_int(int(part.quantity), f"Linear part row {i} quantity")
        if error:
            errors.append(error)
        fits = any(s.material == part.material and part.length <= s.length for s in stock_list)
        if stock_list and not fits:
            errors.append(f"Linear part '{part.name}' does not fit any matching stock length.")
    return errors

from __future__ import annotations

MM_PER_INCH = 25.4


def to_mm(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit == "mm":
        return value
    if unit == "cm":
        return value * 10.0
    if unit in {"inch", "inches", "in"}:
        return value * MM_PER_INCH
    raise ValueError(f"Unsupported unit: {unit}")


def from_mm(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit == "mm":
        return value
    if unit == "cm":
        return value / 10.0
    if unit in {"inch", "inches", "in"}:
        return value / MM_PER_INCH
    raise ValueError(f"Unsupported unit: {unit}")


def format_dimension(value_mm: float, unit: str = "mm") -> str:
    value = from_mm(value_mm, unit)
    return f"{value:.2f} {unit}" if unit != "mm" else f"{value:.0f} mm"


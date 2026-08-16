from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from cad.parameter_data import ExpressionBinding


class ConstraintType(str, Enum):
    COINCIDENT = "coincident"
    POINT_ON_OBJECT = "point_on_object"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    PARALLEL = "parallel"
    PERPENDICULAR = "perpendicular"
    COLLINEAR = "collinear"
    CONCENTRIC = "concentric"
    EQUAL_LENGTH = "equal_length"
    EQUAL_RADIUS = "equal_radius"
    MIDPOINT = "midpoint"
    LENGTH = "length"
    ARC_LENGTH = "arc_length"
    DISTANCE = "distance"
    DISTANCE_X = "distance_x"
    DISTANCE_Y = "distance_y"
    X_COORDINATE = "x_coordinate"
    Y_COORDINATE = "y_coordinate"
    RADIUS = "radius"
    DIAMETER = "diameter"
    FIX = "fix"
    TANGENT = "tangent"
    ANGLE = "angle"


class ConstraintStatus(str, Enum):
    OK = "ok"
    REFERENCE = "reference"
    DISABLED = "disabled"
    REDUNDANT = "redundant"
    CONFLICTING = "conflicting"
    BROKEN_REFERENCE = "broken_reference"


class SketchSolveStatus(str, Enum):
    UNDER_CONSTRAINED = "under_constrained"
    FULLY_CONSTRAINED = "fully_constrained"
    REDUNDANT = "redundant"
    CONFLICTING = "conflicting"
    UNSOLVABLE = "unsolvable"


def _new_id() -> str:
    return str(uuid.uuid4())


def _uuid(value: object, field_name: str) -> str:
    text = str(value or "")
    try:
        uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Pole {field_name} nie zawiera prawidłowego UUID.") from exc
    return text


@dataclass(frozen=True, slots=True)
class GeometryReference:
    entity_id: str
    element: str = "entity"

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _uuid(self.entity_id, "entity_id"))
        if not self.element or len(self.element) > 64:
            raise ValueError("Nazwa pod-elementu referencji jest nieprawidłowa.")

    def to_dict(self) -> dict[str, str]:
        return {"entity_id": self.entity_id, "element": self.element}

    @classmethod
    def from_dict(cls, data: object) -> "GeometryReference":
        if not isinstance(data, dict):
            raise ValueError("Referencja geometrii ma nieprawidłowy format.")
        return cls(_uuid(data.get("entity_id"), "entity_id"), str(data.get("element", "entity")))


@dataclass(frozen=True, slots=True)
class SketchConstraint:
    constraint_type: ConstraintType
    references: tuple[GeometryReference, ...]
    id: str = field(default_factory=_new_id)
    value: float | None = None
    name: str = ""
    driving: bool = True
    enabled: bool = True
    temporary: bool = False
    fixed_values: tuple[float, ...] = ()
    status: ConstraintStatus = ConstraintStatus.OK
    diagnostic: str = ""
    expression: str = ""
    parameter_bindings: tuple[ExpressionBinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "constraint.id"))
        object.__setattr__(self, "constraint_type", ConstraintType(self.constraint_type))
        object.__setattr__(self, "references", tuple(self.references))
        if not self.references:
            raise ValueError("Więz musi zawierać co najmniej jedną referencję.")
        if len(self.references) > 8:
            raise ValueError("Więz zawiera zbyt wiele referencji.")
        if self.value is not None:
            number = float(self.value)
            if not math_is_finite(number) or abs(number) > 1e12:
                raise ValueError("Wartość więzu jest poza bezpiecznym zakresem.")
            object.__setattr__(self, "value", number)
        fixed = tuple(float(value) for value in self.fixed_values)
        if any(not math_is_finite(value) or abs(value) > 1e12 for value in fixed):
            raise ValueError("Wartości więzu blokującego są nieprawidłowe.")
        object.__setattr__(self, "fixed_values", fixed)
        object.__setattr__(self, "name", str(self.name)[:128])
        object.__setattr__(self, "diagnostic", str(self.diagnostic)[:1000])
        expression = str(self.expression).strip()
        if len(expression) > 2000:
            raise ValueError("Wyrażenie więzu jest zbyt długie.")
        object.__setattr__(self, "expression", expression)
        bindings = tuple(self.parameter_bindings)
        if len(bindings) > 256 or len({binding.alias for binding in bindings}) != len(bindings):
            raise ValueError("Powiązania wyrażenia więzu są nieprawidłowe.")
        object.__setattr__(self, "parameter_bindings", bindings)

    @property
    def is_active_driving(self) -> bool:
        return self.enabled and self.driving and self.status != ConstraintStatus.BROKEN_REFERENCE

    def with_runtime_status(self, status: ConstraintStatus, diagnostic: str = "") -> "SketchConstraint":
        return replace(self, status=status, diagnostic=diagnostic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.constraint_type.value,
            "references": [reference.to_dict() for reference in self.references],
            "value": self.value,
            "name": self.name,
            "driving": self.driving,
            "enabled": self.enabled,
            "temporary": self.temporary,
            "fixed_values": list(self.fixed_values),
            "expression": self.expression,
            "parameter_bindings": [binding.to_dict() for binding in self.parameter_bindings],
        }


def math_is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def constraint_from_dict(data: object) -> SketchConstraint:
    if not isinstance(data, dict):
        raise ValueError("Więz szkicu ma nieprawidłowy format.")
    raw_references = data.get("references", [])
    if not isinstance(raw_references, list):
        raise ValueError("Lista referencji więzu jest nieprawidłowa.")
    raw_bindings = data.get("parameter_bindings", [])
    if not isinstance(raw_bindings, list):
        raise ValueError("Lista powiązań wyrażenia więzu jest nieprawidłowa.")
    return SketchConstraint(
        id=_uuid(data.get("id"), "constraint.id"),
        constraint_type=ConstraintType(str(data.get("type"))),
        references=tuple(GeometryReference.from_dict(reference) for reference in raw_references),
        value=data.get("value"),
        name=str(data.get("name", "")),
        driving=bool(data.get("driving", True)),
        enabled=bool(data.get("enabled", True)),
        temporary=bool(data.get("temporary", False)),
        fixed_values=tuple(data.get("fixed_values", ())),
        expression=str(data.get("expression", "")),
        parameter_bindings=tuple(ExpressionBinding.from_dict(binding) for binding in raw_bindings),
    )


_TYPE_LABELS = {
    ConstraintType.COINCIDENT: "Zbieżność",
    ConstraintType.POINT_ON_OBJECT: "Punkt na obiekcie",
    ConstraintType.HORIZONTAL: "Poziomość",
    ConstraintType.VERTICAL: "Pionowość",
    ConstraintType.PARALLEL: "Równoległość",
    ConstraintType.PERPENDICULAR: "Prostopadłość",
    ConstraintType.COLLINEAR: "Współliniowość",
    ConstraintType.CONCENTRIC: "Współśrodkowość",
    ConstraintType.EQUAL_LENGTH: "Równa długość",
    ConstraintType.EQUAL_RADIUS: "Równy promień",
    ConstraintType.MIDPOINT: "Punkt środkowy",
    ConstraintType.LENGTH: "Długość",
    ConstraintType.ARC_LENGTH: "Długość łuku",
    ConstraintType.DISTANCE: "Odległość",
    ConstraintType.DISTANCE_X: "Odległość X",
    ConstraintType.DISTANCE_Y: "Odległość Y",
    ConstraintType.X_COORDINATE: "Współrzędna X",
    ConstraintType.Y_COORDINATE: "Współrzędna Y",
    ConstraintType.RADIUS: "Promień",
    ConstraintType.DIAMETER: "Średnica",
    ConstraintType.FIX: "Blokada",
    ConstraintType.TANGENT: "Styczność",
    ConstraintType.ANGLE: "Kąt",
}


def constraint_label(constraint: SketchConstraint) -> str:
    label = constraint.name or _TYPE_LABELS[constraint.constraint_type]
    if constraint.value is not None:
        unit = "°" if constraint.constraint_type == ConstraintType.ANGLE else " mm"
        return f"{label}: {constraint.value:g}{unit}"
    return label

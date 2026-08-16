from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any


def _new_id() -> str:
    return str(uuid.uuid4())


def _uuid(value: object, field_name: str, *, optional: bool = False) -> str:
    text = str(value or "")
    if optional and not text:
        return ""
    try:
        uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Pole {field_name} nie zawiera prawidlowego UUID.") from exc
    return text


def validate_parameter_name(value: object) -> str:
    name = str(value or "").strip()
    if not name.isidentifier() or len(name) > 128:
        raise ValueError("Nazwa parametru musi byc prawidlowym identyfikatorem.")
    if name.startswith("__"):
        raise ValueError("Nazwa parametru nie moze zaczynac sie od podwojnego podkreslenia.")
    return name


@dataclass(frozen=True, slots=True)
class ExpressionBinding:
    alias: str
    parameter_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "alias", validate_parameter_name(self.alias))
        object.__setattr__(self, "parameter_id", _uuid(self.parameter_id, "parameter_id"))

    def to_dict(self) -> dict[str, str]:
        return {"alias": self.alias, "parameter_id": self.parameter_id}

    @classmethod
    def from_dict(cls, data: object) -> "ExpressionBinding":
        if not isinstance(data, dict):
            raise ValueError("Powiazanie wyrazenia ma nieprawidlowy format.")
        return cls(data.get("alias", ""), data.get("parameter_id", ""))


@dataclass(frozen=True, slots=True)
class CadParameter:
    name: str
    expression: str
    value: float
    id: str = field(default_factory=_new_id)
    scope_object_id: str = ""
    unit_kind: str = "length"
    bindings: tuple[ExpressionBinding, ...] = ()
    diagnostic: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "parameter.id"))
        object.__setattr__(self, "scope_object_id", _uuid(self.scope_object_id, "scope_object_id", optional=True))
        object.__setattr__(self, "name", validate_parameter_name(self.name))
        expression = str(self.expression).strip()
        if not expression or len(expression) > 2000:
            raise ValueError("Wyrazenie parametru jest puste lub zbyt dlugie.")
        object.__setattr__(self, "expression", expression)
        number = float(self.value)
        if not math.isfinite(number) or abs(number) > 1e12:
            raise ValueError("Wartosc parametru jest poza bezpiecznym zakresem.")
        object.__setattr__(self, "value", number)
        if self.unit_kind != "length":
            raise ValueError("Nieobslugiwany rodzaj jednostki parametru.")
        bindings = tuple(self.bindings)
        if len(bindings) > 256 or len({binding.alias for binding in bindings}) != len(bindings):
            raise ValueError("Lista powiazan parametru jest nieprawidlowa.")
        object.__setattr__(self, "bindings", bindings)
        object.__setattr__(self, "diagnostic", str(self.diagnostic)[:1000])

    @property
    def dependency_ids(self) -> tuple[str, ...]:
        return tuple(binding.parameter_id for binding in self.bindings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "expression": self.expression,
            "value": self.value,
            "scope_object_id": self.scope_object_id,
            "unit_kind": self.unit_kind,
            "bindings": [binding.to_dict() for binding in self.bindings],
        }

    @classmethod
    def from_dict(cls, data: object) -> "CadParameter":
        if not isinstance(data, dict):
            raise ValueError("Parametr CAD ma nieprawidlowy format.")
        raw_bindings = data.get("bindings", [])
        if not isinstance(raw_bindings, list):
            raise ValueError("Lista powiazan parametru jest nieprawidlowa.")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            expression=data.get("expression", ""),
            value=data.get("value", 0.0),
            scope_object_id=data.get("scope_object_id", ""),
            unit_kind=str(data.get("unit_kind", "length")),
            bindings=tuple(ExpressionBinding.from_dict(binding) for binding in raw_bindings),
        )

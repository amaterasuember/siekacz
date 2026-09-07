from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, TYPE_CHECKING

from cad.constraints import ConstraintType, GeometryReference, SketchConstraint

if TYPE_CHECKING:
    from cad.model import Sketch, SketchEntity


class PatternType(str, Enum):
    LINEAR = "linear"


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
class SketchPattern:
    source_entity_id: str
    count: int
    spacing_x: float
    spacing_y: float = 0.0
    id: str = field(default_factory=_new_id)
    name: str = "LinearPattern"
    label: str = "Szyk liniowy"
    pattern_type: PatternType = PatternType.LINEAR
    generated_entity_ids: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    enabled: bool = True
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "pattern.id"))
        object.__setattr__(self, "source_entity_id", _uuid(self.source_entity_id, "source_entity_id"))
        object.__setattr__(self, "pattern_type", PatternType(self.pattern_type))
        count = int(self.count)
        if count < 2 or count > 1000:
            raise ValueError("Liczba elementów szyku musi należeć do zakresu 2–1000.")
        object.__setattr__(self, "count", count)
        spacing_x, spacing_y = float(self.spacing_x), float(self.spacing_y)
        if not all(math.isfinite(value) and abs(value) <= 1e9 for value in (spacing_x, spacing_y)):
            raise ValueError("Odstęp szyku jest poza bezpiecznym zakresem.")
        if math.hypot(spacing_x, spacing_y) <= 1e-9:
            raise ValueError("Wektor odstępu szyku nie może być zerowy.")
        object.__setattr__(self, "spacing_x", spacing_x)
        object.__setattr__(self, "spacing_y", spacing_y)
        generated = tuple(_uuid(value, "generated_entity_ids") for value in self.generated_entity_ids)
        constraints = tuple(_uuid(value, "constraint_ids") for value in self.constraint_ids)
        if generated and len(generated) != count - 1:
            raise ValueError("Liczba wygenerowanych encji nie zgadza się z parametrem count.")
        if constraints and len(constraints) != (count - 1) * 3:
            raise ValueError("Lista więzów szyku liniowego jest niekompletna.")
        object.__setattr__(self, "generated_entity_ids", generated)
        object.__setattr__(self, "constraint_ids", constraints)
        object.__setattr__(self, "name", str(self.name)[:128])
        object.__setattr__(self, "label", str(self.label)[:256])
        object.__setattr__(self, "error", str(self.error)[:1000])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.pattern_type.value,
            "name": self.name,
            "label": self.label,
            "source_entity_id": self.source_entity_id,
            "count": self.count,
            "spacing_x": self.spacing_x,
            "spacing_y": self.spacing_y,
            "generated_entity_ids": list(self.generated_entity_ids),
            "constraint_ids": list(self.constraint_ids),
            "enabled": self.enabled,
        }


def pattern_from_dict(data: object) -> SketchPattern:
    if not isinstance(data, dict):
        raise ValueError("Szyk szkicu ma nieprawidłowy format.")
    return SketchPattern(
        id=_uuid(data.get("id"), "pattern.id"),
        pattern_type=PatternType(str(data.get("type"))),
        name=str(data.get("name", "LinearPattern")),
        label=str(data.get("label", "Szyk liniowy")),
        source_entity_id=_uuid(data.get("source_entity_id"), "source_entity_id"),
        count=int(data.get("count", 0)),
        spacing_x=float(data.get("spacing_x", 0.0)),
        spacing_y=float(data.get("spacing_y", 0.0)),
        generated_entity_ids=tuple(data.get("generated_entity_ids", ())),
        constraint_ids=tuple(data.get("constraint_ids", ())),
        enabled=bool(data.get("enabled", True)),
    )


@dataclass(frozen=True, slots=True)
class PatternBuild:
    pattern: SketchPattern
    entities: tuple["SketchEntity", ...]
    constraints: tuple[SketchConstraint, ...]


def create_linear_circle_pattern(
    sketch: "Sketch",
    source_entity_id: str,
    count: int,
    spacing_x: float,
    spacing_y: float = 0.0,
    *,
    label: str = "Szyk otworów",
) -> PatternBuild:
    from cad.model import CircleEntity, Point2D

    source = sketch.entities.get(source_entity_id)
    if not isinstance(source, CircleEntity):
        raise ValueError("Szyk otworów wymaga okręgu źródłowego.")
    draft = SketchPattern(source_entity_id, count, spacing_x, spacing_y, label=label)
    entities: list[SketchEntity] = []
    constraints: list[SketchConstraint] = []
    for index in range(1, draft.count):
        generated = CircleEntity(
            Point2D(source.center.x + draft.spacing_x * index, source.center.y + draft.spacing_y * index),
            source.radius,
            construction=source.construction,
            visible=source.visible,
            locked=source.locked,
            layer=source.layer,
        )
        entities.append(generated)
        constraints.extend((
            SketchConstraint(
                ConstraintType.EQUAL_RADIUS,
                (GeometryReference(source.id), GeometryReference(generated.id)),
                name=f"{draft.label} — równy promień {index + 1}",
            ),
            SketchConstraint(
                ConstraintType.DISTANCE_X,
                (GeometryReference(source.id, "center"), GeometryReference(generated.id, "center")),
                value=draft.spacing_x * index,
                name=f"{draft.label} — X {index + 1}",
            ),
            SketchConstraint(
                ConstraintType.DISTANCE_Y,
                (GeometryReference(source.id, "center"), GeometryReference(generated.id, "center")),
                value=draft.spacing_y * index,
                name=f"{draft.label} — Y {index + 1}",
            ),
        ))
    pattern = replace(
        draft,
        generated_entity_ids=tuple(entity.id for entity in entities),
        constraint_ids=tuple(constraint.id for constraint in constraints),
    )
    return PatternBuild(pattern, tuple(entities), tuple(constraints))


def reconfigure_linear_circle_pattern(
    sketch: "Sketch",
    pattern_id: str,
    count: int,
    spacing_x: float,
    spacing_y: float,
) -> SketchPattern:
    from cad.model import CircleEntity, Point2D

    current = sketch.patterns.get(pattern_id)
    if current is None or current.pattern_type != PatternType.LINEAR:
        raise ValueError("Nie znaleziono liniowego szyku szkicu.")
    source = sketch.entities.get(current.source_entity_id)
    if not isinstance(source, CircleEntity):
        raise ValueError("Źródło szyku otworów nie istnieje lub nie jest okręgiem.")
    target = SketchPattern(
        source.id,
        count,
        spacing_x,
        spacing_y,
        id=current.id,
        name=current.name,
        label=current.label,
        enabled=current.enabled,
    )
    old_entities = list(current.generated_entity_ids)
    old_constraint_groups = [current.constraint_ids[index:index + 3] for index in range(0, len(current.constraint_ids), 3)]
    generated_ids: list[str] = []
    constraint_ids: list[str] = []
    for index in range(1, target.count):
        if index - 1 < len(old_entities):
            entity_id = old_entities[index - 1]
            existing = sketch.entities.get(entity_id)
            if not isinstance(existing, CircleEntity):
                raise ValueError("Wygenerowana encja szyku została usunięta lub zmieniona.")
            generated = replace(
                existing,
                center=Point2D(source.center.x + target.spacing_x * index, source.center.y + target.spacing_y * index),
                radius=source.radius,
            )
            sketch._replace_entity(generated)
            group = old_constraint_groups[index - 1]
            if len(group) != 3:
                raise ValueError("Więzy istniejącego szyku są niekompletne.")
            equal = sketch.constraints[group[0]]
            distance_x = replace(sketch.constraints[group[1]], value=target.spacing_x * index)
            distance_y = replace(sketch.constraints[group[2]], value=target.spacing_y * index)
            sketch._replace_constraint(equal)
            sketch._replace_constraint(distance_x)
            sketch._replace_constraint(distance_y)
            group_ids = list(group)
        else:
            generated = CircleEntity(
                Point2D(source.center.x + target.spacing_x * index, source.center.y + target.spacing_y * index),
                source.radius,
                construction=source.construction,
                visible=source.visible,
                locked=source.locked,
                layer=source.layer,
            )
            sketch._add_entity(generated)
            additions = (
                SketchConstraint(ConstraintType.EQUAL_RADIUS, (GeometryReference(source.id), GeometryReference(generated.id)), name=f"{target.label} — równy promień {index + 1}"),
                SketchConstraint(ConstraintType.DISTANCE_X, (GeometryReference(source.id, "center"), GeometryReference(generated.id, "center")), value=target.spacing_x * index, name=f"{target.label} — X {index + 1}"),
                SketchConstraint(ConstraintType.DISTANCE_Y, (GeometryReference(source.id, "center"), GeometryReference(generated.id, "center")), value=target.spacing_y * index, name=f"{target.label} — Y {index + 1}"),
            )
            for constraint in additions:
                sketch._add_constraint(constraint)
            group_ids = [constraint.id for constraint in additions]
        generated_ids.append(generated.id)
        constraint_ids.extend(group_ids)

    for entity_id in old_entities[target.count - 1:]:
        if entity_id in sketch.entities:
            sketch._remove_entity(entity_id)
    for group in old_constraint_groups[target.count - 1:]:
        for constraint_id in group:
            if constraint_id in sketch.constraints:
                sketch._remove_constraint(constraint_id)
    replacement = replace(target, generated_entity_ids=tuple(generated_ids), constraint_ids=tuple(constraint_ids))
    sketch._replace_pattern(replacement)
    return replacement

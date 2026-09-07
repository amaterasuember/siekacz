from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Callable, Iterator, Protocol

from cad.constraints import ConstraintType, GeometryReference, SketchConstraint
from cad.editing import TopologyEdit
from cad.model import CadDocument, CadValidationError, Sketch, SketchEntity
from cad.parameter_data import CadParameter
from cad.parameter_engine import edit_parameter, parameter_usage, recompute_parameters
from cad.patterns import PatternBuild, SketchPattern, reconfigure_linear_circle_pattern
from cad.profiles import ProfileRepairPlan


class CadCommand(Protocol):
    description: str

    @property
    def changed_ids(self) -> set[str]: ...

    def execute(self, document: CadDocument) -> None: ...

    def undo(self, document: CadDocument) -> None: ...

    def redo(self, document: CadDocument) -> None: ...

    def merge_with_previous(self, previous: "CadCommand") -> bool: ...


@dataclass(slots=True)
class _SketchState:
    entities: dict[str, SketchEntity]
    order: list[str]
    constraints: dict[str, SketchConstraint]
    constraint_order: list[str]
    patterns: dict[str, SketchPattern]
    pattern_order: list[str]
    solve_status: object
    degrees_of_freedom: int
    error: str
    diagnostics: list[str]
    recompute_status: str


def _capture(sketch: Sketch) -> _SketchState:
    return _SketchState(
        entities=dict(sketch.entities),
        order=list(sketch.order),
        constraints=dict(sketch.constraints),
        constraint_order=list(sketch.constraint_order),
        patterns=dict(sketch.patterns),
        pattern_order=list(sketch.pattern_order),
        solve_status=sketch.solve_status,
        degrees_of_freedom=sketch.degrees_of_freedom,
        error=sketch.error,
        diagnostics=list(sketch.diagnostics),
        recompute_status=sketch.recompute_status,
    )


def _restore(sketch: Sketch, state: _SketchState) -> None:
    sketch.entities = dict(state.entities)
    sketch.order = list(state.order)
    sketch.constraints = dict(state.constraints)
    sketch.constraint_order = list(state.constraint_order)
    sketch.patterns = dict(state.patterns)
    sketch.pattern_order = list(state.pattern_order)
    sketch.solve_status = state.solve_status  # type: ignore[assignment]
    sketch.degrees_of_freedom = state.degrees_of_freedom
    sketch.error = state.error
    sketch.diagnostics = list(state.diagnostics)
    sketch.recompute_status = state.recompute_status


@dataclass(slots=True)
class _ParameterDocumentState:
    parameters: dict[str, CadParameter]
    parameter_order: list[str]
    sketches: dict[str, _SketchState]


def _capture_parameter_document(document: CadDocument) -> _ParameterDocumentState:
    return _ParameterDocumentState(
        dict(document.parameters),
        list(document.parameter_order),
        {sketch_id: _capture(sketch) for sketch_id, sketch in document.sketches.items()},
    )


def _restore_parameter_document(document: CadDocument, state: _ParameterDocumentState) -> None:
    document.parameters = dict(state.parameters)
    document.parameter_order = list(state.parameter_order)
    for sketch_id, sketch_state in state.sketches.items():
        _restore(document.sketches[sketch_id], sketch_state)


def _solve_and_apply(sketch: Sketch) -> None:
    from cad.solver import solve_and_apply

    solve_and_apply(sketch)


class BaseCommand(ABC):
    description = "Operacja CAD"

    @property
    def changed_ids(self) -> set[str]:
        return set()

    @abstractmethod
    def execute(self, document: CadDocument) -> None:
        """Apply the concrete command to the document."""

    def redo(self, document: CadDocument) -> None:
        self.execute(document)

    def merge_with_previous(self, previous: CadCommand) -> bool:
        return False


@dataclass(slots=True)
class AddParameterCommand(BaseCommand):
    parameter: CadParameter
    description: str = "Dodaj parametr"
    _before: _ParameterDocumentState | None = None
    _after: _ParameterDocumentState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.parameter.id, self.parameter.scope_object_id} - {""}

    def execute(self, document: CadDocument) -> None:
        self._before = _capture_parameter_document(document)
        try:
            document._add_parameter(self.parameter)
            recompute_parameters(document)
        except Exception:
            _restore_parameter_document(document, self._before)
            raise
        self._after = _capture_parameter_document(document)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego dodania parametru.")
        _restore_parameter_document(document, self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore_parameter_document(document, self._after)


@dataclass(slots=True)
class EditParameterCommand(BaseCommand):
    parameter_id: str
    name: str
    expression: str
    description: str = "Zmień parametr"
    _before: _ParameterDocumentState | None = None
    _after: _ParameterDocumentState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.parameter_id}

    def execute(self, document: CadDocument) -> None:
        self._before = _capture_parameter_document(document)
        try:
            edit_parameter(document, self.parameter_id, name=self.name, expression=self.expression)
        except Exception:
            _restore_parameter_document(document, self._before)
            raise
        self._after = _capture_parameter_document(document)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanej edycji parametru.")
        _restore_parameter_document(document, self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore_parameter_document(document, self._after)


@dataclass(slots=True)
class RemoveParameterCommand(BaseCommand):
    parameter_id: str
    description: str = "Usuń parametr"
    _before: _ParameterDocumentState | None = None
    _after: _ParameterDocumentState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.parameter_id}

    def execute(self, document: CadDocument) -> None:
        self._before = _capture_parameter_document(document)
        usages = parameter_usage(document, self.parameter_id)
        if usages:
            raise CadValidationError("Nie można usunąć używanego parametru: " + ", ".join(usages) + ".")
        try:
            document._remove_parameter(self.parameter_id)
            recompute_parameters(document)
        except Exception:
            _restore_parameter_document(document, self._before)
            raise
        self._after = _capture_parameter_document(document)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego usunięcia parametru.")
        _restore_parameter_document(document, self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore_parameter_document(document, self._after)


@dataclass(slots=True)
class AddEntityCommand(BaseCommand):
    sketch_id: str
    entity: SketchEntity
    index: int | None = None
    description: str = "Dodaj geometrię"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, self.entity.id}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        sketch._add_entity(self.entity, self.index)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego dodania geometrii.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class AddEntitiesCommand(BaseCommand):
    sketch_id: str
    entities: tuple[SketchEntity, ...]
    description: str = "Dodaj geometrię"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, *(entity.id for entity in self.entities)}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        for entity in self.entities:
            sketch._add_entity(entity)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego dodania geometrii.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class RemoveEntitiesCommand(BaseCommand):
    sketch_id: str
    entity_ids: tuple[str, ...]
    description: str = "Usuń geometrię"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, *self.entity_ids}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        indexed = sorted(
            ((sketch.order.index(entity_id), entity_id) for entity_id in self.entity_ids if entity_id in sketch.entities),
            reverse=True,
        )
        for _index, entity_id in indexed:
            sketch._remove_entity(entity_id)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego usunięcia geometrii.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class ReplaceEntityCommand(BaseCommand):
    sketch_id: str
    replacement: SketchEntity
    description: str = "Zmień geometrię"
    merge_key: str = ""
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, self.replacement.id}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        sketch._replace_entity(self.replacement)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanej zmiany geometrii.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)

    def merge_with_previous(self, previous: CadCommand) -> bool:
        if not isinstance(previous, ReplaceEntityCommand):
            return False
        if not self.merge_key or self.merge_key != previous.merge_key:
            return False
        if self.sketch_id != previous.sketch_id or self.replacement.id != previous.replacement.id:
            return False
        previous.replacement = self.replacement
        previous._after = self._after
        return True


@dataclass(slots=True)
class ApplyTopologyEditCommand(BaseCommand):
    """Apply split/trim/extend atomically and conservatively drop stale constraints."""

    sketch_id: str
    edit: TopologyEdit
    description: str = "Edytuj topologię"
    removed_constraint_ids: tuple[str, ...] = ()
    created_constraint_ids: tuple[str, ...] = ()
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {
            self.sketch_id,
            self.edit.source_id,
            *(piece.id for piece in self.edit.pieces),
            *self.removed_constraint_ids,
            *self.created_constraint_ids,
        }

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        source = sketch.entities.get(self.edit.source_id)
        if source is None:
            raise CadValidationError("Nie znaleziono geometrii źródłowej operacji.")
        if source.locked:
            raise CadValidationError("Zablokowanej geometrii nie można dzielić, przycinać ani przedłużać.")
        for pattern in sketch.patterns.values():
            if self.edit.source_id in {pattern.source_entity_id, *pattern.generated_entity_ids}:
                raise CadValidationError("Geometria należąca do szyku musi być najpierw odłączona od szyku.")
        self._before = _capture(sketch)
        try:
            self._apply(sketch)
        except Exception:
            _restore(sketch, self._before)
            raise
        self._after = _capture(sketch)

    def _apply(self, sketch: Sketch) -> None:
        self.removed_constraint_ids = tuple(
            constraint.id
            for constraint in sketch.ordered_constraints()
            if any(reference.entity_id == self.edit.source_id for reference in constraint.references)
        )
        for constraint_id in self.removed_constraint_ids:
            sketch._remove_constraint(constraint_id)
        source_index = sketch.order.index(self.edit.source_id)
        sketch._replace_entity(self.edit.pieces[0])
        for offset, piece in enumerate(self.edit.pieces[1:], 1):
            sketch._add_entity(piece, source_index + offset)
        created: list[str] = []
        endpoint_pairs = list(zip(self.edit.pieces, self.edit.pieces[1:]))
        if len(self.edit.pieces) > 1:
            first, last = self.edit.pieces[0], self.edit.pieces[-1]
            if hasattr(first, "start") and hasattr(last, "end") and first.start.distance_to(last.end) <= 1e-7:  # type: ignore[union-attr]
                endpoint_pairs.append((last, first))
        for first_piece, second_piece in endpoint_pairs:
            if not hasattr(first_piece, "end") or not hasattr(second_piece, "start"):
                continue
            if first_piece.end.distance_to(second_piece.start) > 1e-7:  # type: ignore[union-attr]
                continue
            constraint = SketchConstraint(
                ConstraintType.COINCIDENT,
                (GeometryReference(first_piece.id, "end"), GeometryReference(second_piece.id, "start")),
                name="Auto — ciągłość po podziale",
            )
            sketch._add_constraint(constraint)
            created.append(constraint.id)
        self.created_constraint_ids = tuple(created)
        _solve_and_apply(sketch)
        if self.removed_constraint_ids:
            sketch.diagnostics.append(
                f"Operacja {self.edit.operation} usunęła {len(self.removed_constraint_ids)} więzów zależnych od zmienionej topologii."
            )

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanej operacji topologicznej.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class RepairProfileCommand(BaseCommand):
    """Apply a checked profile-repair plan as one reversible document change."""

    sketch_id: str
    plan: ProfileRepairPlan
    description: str = "Sprawdz i napraw szkic"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        removed = {*self.plan.duplicate_entity_ids, *self.plan.micro_entity_ids}
        return {
            self.sketch_id,
            *removed,
            *(constraint.id for constraint in self.plan.coincident_constraints),
        }

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        removed = {
            *self.plan.duplicate_entity_ids,
            *self.plan.micro_entity_ids,
        }
        dependent_constraints = [
            constraint.id
            for constraint in sketch.constraints.values()
            if any(reference.entity_id in removed for reference in constraint.references)
        ]
        for constraint_id in dependent_constraints:
            sketch._remove_constraint(constraint_id)
        for entity_id in tuple(sketch.order):
            if entity_id in removed:
                sketch._remove_entity(entity_id)
        for constraint in self.plan.coincident_constraints:
            if all(reference.entity_id in sketch.entities for reference in constraint.references):
                sketch._add_constraint(constraint)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie mozna cofnac niewykonanej naprawy szkicu.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class AddConstraintCommand(BaseCommand):
    sketch_id: str
    constraint: SketchConstraint
    index: int | None = None
    description: str = "Dodaj więz"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, self.constraint.id, *(ref.entity_id for ref in self.constraint.references)}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        sketch._add_constraint(self.constraint, self.index)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego dodania więzu.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class RemoveConstraintsCommand(BaseCommand):
    sketch_id: str
    constraint_ids: tuple[str, ...]
    description: str = "Usuń więz"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, *self.constraint_ids}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        indexed = sorted(
            ((sketch.constraint_order.index(item_id), item_id) for item_id in self.constraint_ids if item_id in sketch.constraints),
            reverse=True,
        )
        for _index, constraint_id in indexed:
            sketch._remove_constraint(constraint_id)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego usunięcia więzu.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class ReplaceConstraintCommand(BaseCommand):
    sketch_id: str
    replacement: SketchConstraint
    description: str = "Zmień więz"
    merge_key: str = ""
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, self.replacement.id, *(ref.entity_id for ref in self.replacement.references)}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        sketch._replace_constraint(self.replacement)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanej zmiany więzu.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)

    def merge_with_previous(self, previous: CadCommand) -> bool:
        if not isinstance(previous, ReplaceConstraintCommand):
            return False
        if not self.merge_key or self.merge_key != previous.merge_key:
            return False
        if self.sketch_id != previous.sketch_id or self.replacement.id != previous.replacement.id:
            return False
        previous.replacement = self.replacement
        previous._after = self._after
        return True


@dataclass(slots=True)
class SetConstraintEnabledCommand(ReplaceConstraintCommand):
    description: str = "Włącz lub wyłącz więz"

    @classmethod
    def create(cls, sketch_id: str, constraint: SketchConstraint, enabled: bool) -> "SetConstraintEnabledCommand":
        return cls(sketch_id, replace(constraint, enabled=enabled))


@dataclass(slots=True)
class AddPatternCommand(BaseCommand):
    sketch_id: str
    build: PatternBuild
    description: str = "Dodaj szyk liniowy"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {
            self.sketch_id,
            self.build.pattern.id,
            self.build.pattern.source_entity_id,
            *(entity.id for entity in self.build.entities),
            *(constraint.id for constraint in self.build.constraints),
        }

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        for entity in self.build.entities:
            sketch._add_entity(entity)
        for constraint in self.build.constraints:
            sketch._add_constraint(constraint)
        sketch._add_pattern(self.build.pattern)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego szyku.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class EditLinearPatternCommand(BaseCommand):
    sketch_id: str
    pattern_id: str
    count: int
    spacing_x: float
    spacing_y: float = 0.0
    description: str = "Zmień szyk liniowy"
    merge_key: str = ""
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, self.pattern_id}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        reconfigure_linear_circle_pattern(sketch, self.pattern_id, self.count, self.spacing_x, self.spacing_y)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanej zmiany szyku.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)

    def merge_with_previous(self, previous: CadCommand) -> bool:
        if not isinstance(previous, EditLinearPatternCommand):
            return False
        if not self.merge_key or self.merge_key != previous.merge_key:
            return False
        if self.sketch_id != previous.sketch_id or self.pattern_id != previous.pattern_id:
            return False
        previous.count = self.count
        previous.spacing_x = self.spacing_x
        previous.spacing_y = self.spacing_y
        previous._after = self._after
        return True


@dataclass(slots=True)
class RemovePatternCommand(BaseCommand):
    sketch_id: str
    pattern_id: str
    description: str = "Usuń szyk"
    _before: _SketchState | None = None
    _after: _SketchState | None = None

    @property
    def changed_ids(self) -> set[str]:
        return {self.sketch_id, self.pattern_id}

    def execute(self, document: CadDocument) -> None:
        sketch = document.sketches[self.sketch_id]
        self._before = _capture(sketch)
        pattern = sketch.patterns.get(self.pattern_id)
        if pattern is None:
            raise CadValidationError("Nie znaleziono szyku do usunięcia.")
        for constraint_id in reversed(pattern.constraint_ids):
            if constraint_id in sketch.constraints:
                sketch._remove_constraint(constraint_id)
        for entity_id in reversed(pattern.generated_entity_ids):
            if entity_id in sketch.entities:
                sketch._remove_entity(entity_id)
        sketch._remove_pattern(pattern.id)
        _solve_and_apply(sketch)
        self._after = _capture(sketch)

    def undo(self, document: CadDocument) -> None:
        if self._before is None:
            raise CadValidationError("Nie można cofnąć niewykonanego usunięcia szyku.")
        _restore(document.sketches[self.sketch_id], self._before)

    def redo(self, document: CadDocument) -> None:
        if self._after is None:
            self.execute(document)
        else:
            _restore(document.sketches[self.sketch_id], self._after)


@dataclass(slots=True)
class CompositeCommand(BaseCommand):
    commands: list[CadCommand]
    description: str = "Transakcja CAD"

    @property
    def changed_ids(self) -> set[str]:
        changed: set[str] = set()
        for command in self.commands:
            changed.update(command.changed_ids)
        return changed

    def execute(self, document: CadDocument) -> None:
        executed: list[CadCommand] = []
        try:
            for command in self.commands:
                command.execute(document)
                executed.append(command)
        except Exception:
            for command in reversed(executed):
                command.undo(document)
            raise

    def undo(self, document: CadDocument) -> None:
        for command in reversed(self.commands):
            command.undo(document)

    def redo(self, document: CadDocument) -> None:
        for command in self.commands:
            command.redo(document)


class CommandStack:
    def __init__(self, document: CadDocument, limit: int = 500) -> None:
        self.document = document
        self.limit = max(1, limit)
        self.undo_commands: list[CadCommand] = []
        self.redo_commands: list[CadCommand] = []
        self._listeners: list[Callable[[set[str]], None]] = []
        self._transaction_description = ""
        self._transaction_commands: list[CadCommand] | None = None

    def subscribe(self, listener: Callable[[set[str]], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _notify(self, changed_ids: set[str]) -> None:
        self.document.recompute()
        for listener in tuple(self._listeners):
            listener(set(changed_ids))

    def execute(self, command: CadCommand) -> None:
        command.execute(self.document)
        if self._transaction_commands is not None:
            self._transaction_commands.append(command)
            self._notify(command.changed_ids)
            return
        if self.undo_commands and command.merge_with_previous(self.undo_commands[-1]):
            self.redo_commands.clear()
            self._notify(command.changed_ids)
            return
        self.undo_commands.append(command)
        if len(self.undo_commands) > self.limit:
            del self.undo_commands[0]
        self.redo_commands.clear()
        self._notify(command.changed_ids)

    def undo(self) -> bool:
        if not self.undo_commands:
            return False
        command = self.undo_commands.pop()
        command.undo(self.document)
        self.redo_commands.append(command)
        self._notify(command.changed_ids)
        return True

    def redo(self) -> bool:
        if not self.redo_commands:
            return False
        command = self.redo_commands.pop()
        command.redo(self.document)
        self.undo_commands.append(command)
        self._notify(command.changed_ids)
        return True

    @contextmanager
    def transaction(self, description: str) -> Iterator[None]:
        if self._transaction_commands is not None:
            raise RuntimeError("Zagnieżdżone transakcje CAD nie są obsługiwane.")
        self._transaction_description = description
        self._transaction_commands = []
        try:
            yield
        except Exception:
            for command in reversed(self._transaction_commands):
                command.undo(self.document)
            self._transaction_commands = None
            self._transaction_description = ""
            raise
        commands = self._transaction_commands
        self._transaction_commands = None
        if commands:
            self.undo_commands.append(CompositeCommand(commands, description))
            if len(self.undo_commands) > self.limit:
                del self.undo_commands[0]
            self.redo_commands.clear()
            changed: set[str] = set()
            for command in commands:
                changed.update(command.changed_ids)
            self._notify(changed)
        self._transaction_description = ""

    @property
    def undo_description(self) -> str:
        return self.undo_commands[-1].description if self.undo_commands else ""

    @property
    def redo_description(self) -> str:
        return self.redo_commands[-1].description if self.redo_commands else ""

from __future__ import annotations

from dataclasses import replace

from cad.constraints import SketchConstraint
from cad.model import CadDocument, CadValidationError
from cad.parameter_data import CadParameter, ExpressionBinding, validate_parameter_name
from cad.units import expression_identifiers, parse_length, rename_expression_identifier, validate_length_dimensions


def _visible_parameter(document: CadDocument, name: str, scope_object_id: str, *, exclude_id: str = "") -> CadParameter:
    local = [
        parameter
        for parameter in document.parameters.values()
        if parameter.id != exclude_id and parameter.name == name and parameter.scope_object_id == scope_object_id
    ]
    if local:
        return local[0]
    global_matches = [
        parameter
        for parameter in document.parameters.values()
        if parameter.id != exclude_id and parameter.name == name and not parameter.scope_object_id
    ]
    if global_matches:
        return global_matches[0]
    raise CadValidationError(f"Nieznany parametr: {name}.")


def bind_expression(
    document: CadDocument,
    expression: str,
    scope_object_id: str = "",
    *,
    exclude_id: str = "",
) -> tuple[tuple[ExpressionBinding, ...], float]:
    names = expression_identifiers(expression)
    bindings = tuple(
        ExpressionBinding(name, _visible_parameter(document, name, scope_object_id, exclude_id=exclude_id).id)
        for name in names
    )
    validate_length_dimensions(expression, {binding.alias for binding in bindings})
    values = {binding.alias: document.parameters[binding.parameter_id].value for binding in bindings}
    return bindings, parse_length(expression, values)


def make_parameter(
    document: CadDocument,
    name: str,
    expression: str,
    scope_object_id: str = "",
) -> CadParameter:
    checked_name = validate_parameter_name(name)
    if scope_object_id and scope_object_id not in document.sketches:
        raise CadValidationError("Zakres lokalnego parametru nie istnieje.")
    if any(
        parameter.name == checked_name and parameter.scope_object_id == scope_object_id
        for parameter in document.parameters.values()
    ):
        raise CadValidationError(f"Parametr {checked_name} już istnieje w tym zakresie.")
    bindings, value = bind_expression(document, expression, scope_object_id)
    return CadParameter(checked_name, expression, value, scope_object_id=scope_object_id, bindings=bindings)


def _evaluate_parameter_values(document: CadDocument) -> dict[str, float]:
    values: dict[str, float] = {}
    visiting: list[str] = []

    def evaluate(parameter_id: str) -> float:
        if parameter_id in values:
            return values[parameter_id]
        if parameter_id in visiting:
            cycle = visiting[visiting.index(parameter_id):] + [parameter_id]
            labels = " → ".join(document.parameters[item].name for item in cycle if item in document.parameters)
            raise CadValidationError(f"Wykryto cykl parametrów: {labels}.")
        parameter = document.parameters.get(parameter_id)
        if parameter is None:
            raise CadValidationError(f"Wyrażenie wskazuje brakujący parametr {parameter_id}.")
        visiting.append(parameter_id)
        names: dict[str, float] = {}
        for binding in parameter.bindings:
            if binding.parameter_id not in document.parameters:
                raise CadValidationError(f"Parametr {parameter.name} wskazuje brakującą zależność.")
            names[binding.alias] = evaluate(binding.parameter_id)
        value = parse_length(parameter.expression, names)
        visiting.pop()
        values[parameter_id] = value
        return value

    for parameter_id in document.parameter_order:
        evaluate(parameter_id)
    return values


def _constraint_value(document: CadDocument, constraint: SketchConstraint, values: dict[str, float]) -> float:
    names: dict[str, float] = {}
    for binding in constraint.parameter_bindings:
        parameter = document.parameters.get(binding.parameter_id)
        if parameter is None:
            raise CadValidationError(f"Więz {constraint.id} wskazuje brakujący parametr.")
        names[binding.alias] = values[binding.parameter_id]
    return parse_length(constraint.expression, names)


def recompute_parameters(document: CadDocument) -> None:
    """Atomically evaluate the parameter DAG, update bound constraints and solve sketches."""

    values = _evaluate_parameter_values(document)
    constraint_values: dict[tuple[str, str], float] = {}
    for sketch in document.sketches.values():
        for constraint_id in tuple(sketch.constraint_order):
            constraint = sketch.constraints[constraint_id]
            if constraint.expression:
                constraint_values[(sketch.id, constraint_id)] = _constraint_value(document, constraint, values)
    for parameter_id, value in values.items():
        document.parameters[parameter_id] = replace(document.parameters[parameter_id], value=value, diagnostic="")
    for (sketch_id, constraint_id), value in constraint_values.items():
        constraint = document.sketches[sketch_id].constraints[constraint_id]
        document.sketches[sketch_id].constraints[constraint_id] = replace(constraint, value=value)
    for sketch in document.sketches.values():
        from cad.solver import solve_and_apply

        solve_and_apply(sketch)
    document.recompute()


def bind_constraint_expression(
    document: CadDocument,
    sketch_id: str,
    constraint: SketchConstraint,
    expression: str,
) -> SketchConstraint:
    bindings, value = bind_expression(document, expression, sketch_id)
    return replace(constraint, value=value, expression=expression.strip(), parameter_bindings=bindings)


def edit_parameter(document: CadDocument, parameter_id: str, *, name: str, expression: str) -> None:
    current = document.parameters.get(parameter_id)
    if current is None:
        raise CadValidationError("Nie znaleziono edytowanego parametru.")
    checked_name = validate_parameter_name(name)
    if any(
        parameter.id != parameter_id
        and parameter.name == checked_name
        and parameter.scope_object_id == current.scope_object_id
        for parameter in document.parameters.values()
    ):
        raise CadValidationError(f"Parametr {checked_name} już istnieje w tym zakresie.")
    old_name = current.name
    if old_name != checked_name:
        for dependant_id, dependant in tuple(document.parameters.items()):
            replacement_alias = checked_name
            if any(binding.alias == checked_name and binding.parameter_id != parameter_id for binding in dependant.bindings):
                replacement_alias = old_name
            bindings = tuple(
                replace(binding, alias=replacement_alias) if binding.parameter_id == parameter_id else binding
                for binding in dependant.bindings
            )
            rewritten = rename_expression_identifier(dependant.expression, old_name, replacement_alias) if bindings != dependant.bindings else dependant.expression
            document.parameters[dependant_id] = replace(dependant, bindings=bindings, expression=rewritten)
        for sketch in document.sketches.values():
            for constraint_id, constraint in tuple(sketch.constraints.items()):
                replacement_alias = checked_name
                if any(binding.alias == checked_name and binding.parameter_id != parameter_id for binding in constraint.parameter_bindings):
                    replacement_alias = old_name
                bindings = tuple(
                    replace(binding, alias=replacement_alias) if binding.parameter_id == parameter_id else binding
                    for binding in constraint.parameter_bindings
                )
                rewritten = rename_expression_identifier(constraint.expression, old_name, replacement_alias) if bindings != constraint.parameter_bindings else constraint.expression
                sketch.constraints[constraint_id] = replace(constraint, parameter_bindings=bindings, expression=rewritten)
        current = replace(current, name=checked_name)
        document.parameters[parameter_id] = current
    bindings, value = bind_expression(document, expression, current.scope_object_id, exclude_id=parameter_id)
    document.parameters[parameter_id] = replace(current, name=checked_name, expression=expression.strip(), bindings=bindings, value=value)
    recompute_parameters(document)


def parameter_usage(document: CadDocument, parameter_id: str) -> tuple[str, ...]:
    usages: list[str] = []
    for parameter in document.parameters.values():
        if parameter.id != parameter_id and any(binding.parameter_id == parameter_id for binding in parameter.bindings):
            usages.append(f"parametr {parameter.name}")
    for sketch in document.sketches.values():
        for constraint in sketch.constraints.values():
            if any(binding.parameter_id == parameter_id for binding in constraint.parameter_bindings):
                usages.append(f"więz {constraint.name or constraint.id[:8]}")
    return tuple(usages)

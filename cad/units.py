from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping

from cad.model import CadValidationError


_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4}
_NUMBER_WITH_UNIT = re.compile(
    r"(?<![\w.])(?P<number>(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?)\s*(?P<unit>mm|cm|m|in|inch)\b",
    re.IGNORECASE,
)
_ALLOWED_FUNCTIONS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "abs": abs}


def _prepare_expression(text: str) -> str:
    normalized = text.strip().replace(",", ".")

    def convert(match: re.Match[str]) -> str:
        value = float(match.group("number"))
        factor = _UNIT_TO_MM[match.group("unit").lower()]
        return f"({value!r}*{factor!r})"

    normalized = _NUMBER_WITH_UNIT.sub(convert, normalized)
    # A bare number is expressed in document units (millimetres).
    return normalized


def expression_identifiers(text: str) -> tuple[str, ...]:
    """Return parameter identifiers in first-use order, excluding safe functions."""

    expression = _prepare_expression(text)
    if not expression:
        raise CadValidationError("Wyrażenie jest puste.")
    try:
        node = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CadValidationError("Wyrażenie ma nieprawidłową składnię.") from exc
    function_names = {
        current.func.id
        for current in ast.walk(node)
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Name)
    }
    names = [current.id for current in ast.walk(node) if isinstance(current, ast.Name) and current.id not in function_names]
    return tuple(dict.fromkeys(names))


def rename_expression_identifier(text: str, old_name: str, new_name: str) -> str:
    """Rename a bound identifier without touching substrings or unit suffixes."""

    return re.sub(rf"\b{re.escape(old_name)}\b", new_name, text)


def validate_length_dimensions(text: str, parameter_names: set[str] | None = None) -> None:
    """Reject expressions whose dimensional result cannot represent a length.

    Parameters and explicit unit literals have dimension L. Bare numbers are
    scalars, except that a final scalar is accepted as millimetres for the
    established CAD input convention.
    """

    names = parameter_names or set()

    def unit_literal(match: re.Match[str]) -> str:
        return f"__length__({match.group('number').replace(',', '.')})"

    prepared = _NUMBER_WITH_UNIT.sub(unit_literal, text.strip().replace(",", "."))
    try:
        root = ast.parse(prepared, mode="eval")
    except SyntaxError as exc:
        raise CadValidationError("Wyrażenie ma nieprawidłową składnię.") from exc

    def dimension(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return dimension(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return 0
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise CadValidationError(f"Nieznany parametr: {node.id}.")
            return 1
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return dimension(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = dimension(node.left), dimension(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
                if left != right and 0 not in (left, right):
                    raise CadValidationError("Nie można dodawać wartości o niezgodnych wymiarach.")
                return max(left, right)
            if isinstance(node.op, ast.Mult):
                result = left + right
            elif isinstance(node.op, ast.Div):
                result = left - right
            elif isinstance(node.op, ast.Pow):
                if right != 0 or not isinstance(node.right, ast.Constant):
                    raise CadValidationError("Wykładnik musi być bezwymiarową stałą.")
                if isinstance(node.right.value, bool) or not isinstance(node.right.value, (int, float)):
                    raise CadValidationError("Wykładnik musi być liczbą.")
                exponent = float(node.right.value)
                result = int(left * exponent) if float(left * exponent).is_integer() else 99
            else:
                raise CadValidationError("Niedozwolona operacja w wyrażeniu wymiarowym.")
            if result not in (0, 1):
                raise CadValidationError("Wyrażenie nie ma wymiaru długości ani skalaru.")
            return result
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and not node.keywords:
            if node.func.id == "__length__":
                if dimension(node.args[0]) != 0:
                    raise CadValidationError("Literał jednostki musi zawierać liczbę.")
                return 1
            if node.func.id == "abs":
                return dimension(node.args[0])
            if node.func.id in {"sqrt", "sin", "cos", "tan"}:
                if dimension(node.args[0]) != 0:
                    raise CadValidationError(f"Funkcja {node.func.id} wymaga argumentu bezwymiarowego.")
                return 0
        raise CadValidationError("Wyrażenie zawiera niedozwolony element wymiarowy.")

    dimension(root)


def parse_length(text: str, parameters: Mapping[str, float] | None = None) -> float:
    """Evaluate a small, safe length expression and return millimetres.

    Supported syntax is arithmetic, named numeric parameters and an explicit
    unit suffix on numeric literals.  No attributes, indexing or arbitrary
    Python calls are accepted.
    """

    expression = _prepare_expression(text)
    if not expression:
        raise CadValidationError("Wartość długości jest pusta.")
    try:
        node = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CadValidationError("Wyrażenie długości ma nieprawidłową składnię.") from exc
    names = {str(name): float(value) for name, value in (parameters or {}).items()}

    def evaluate(current: ast.AST) -> float:
        if isinstance(current, ast.Expression):
            return evaluate(current.body)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            return float(current.value)
        if isinstance(current, ast.Name):
            if current.id not in names:
                raise CadValidationError(f"Nieznany parametr: {current.id}.")
            return names[current.id]
        if isinstance(current, ast.UnaryOp) and isinstance(current.op, (ast.UAdd, ast.USub)):
            value = evaluate(current.operand)
            return value if isinstance(current.op, ast.UAdd) else -value
        if isinstance(current, ast.BinOp) and isinstance(current.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)):
            left, right = evaluate(current.left), evaluate(current.right)
            if isinstance(current.op, ast.Add):
                return left + right
            if isinstance(current.op, ast.Sub):
                return left - right
            if isinstance(current.op, ast.Mult):
                return left * right
            if isinstance(current.op, ast.Div):
                return left / right
            if isinstance(current.op, ast.Pow):
                if abs(right) > 16 or abs(left) > 1e9:
                    raise CadValidationError("Potęga przekracza bezpieczny zakres.")
                return left**right
            return left % right
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Name):
            function = _ALLOWED_FUNCTIONS.get(current.func.id)
            if function is None or current.keywords or len(current.args) != 1:
                raise CadValidationError("Niedozwolona funkcja w wyrażeniu długości.")
            return float(function(evaluate(current.args[0])))
        raise CadValidationError("Wyrażenie długości zawiera niedozwolony element.")

    try:
        result = evaluate(node)
    except ZeroDivisionError as exc:
        raise CadValidationError("Nie można dzielić przez zero.") from exc
    if not math.isfinite(result) or abs(result) > 1e12:
        raise CadValidationError("Wynik wyrażenia przekracza bezpieczny zakres.")
    return result

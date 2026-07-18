from __future__ import annotations

from datetime import datetime
from string import Formatter
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .models import Expression, Operation


def evaluate(expression: Expression, row: Mapping[str, Any], location: str) -> Any:
    if expression.field_name is not None:
        if expression.field_name not in row:
            raise ValueError(f"{location} references missing field {expression.field_name!r}")
        value = row[expression.field_name]
    elif expression.template is not None:
        values: dict[str, Any] = {}
        for _, placeholder, _, _ in Formatter().parse(expression.template):
            if placeholder is not None:
                if placeholder not in row:
                    raise ValueError(f"{location} template references missing field {placeholder!r}")
                values[placeholder] = row[placeholder]
        value = expression.template.format_map(values)
    elif expression.has_constant:
        value = expression.constant
    else:  # pragma: no cover - construction prevents this branch
        raise ValueError(f"{location} has no expression source")
    for operation in expression.operations:
        value = _apply(operation, value, location)
    return value


def evaluate_required_string(expression: Expression, row: Mapping[str, Any], location: str) -> str:
    value = evaluate(expression, row, location)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must produce a non-empty string")
    return value


def _apply(operation: Operation, value: Any, location: str) -> Any:
    if operation.op == "trim":
        return _string(value, location).strip()
    if operation.op == "lower":
        return _string(value, location).lower()
    if operation.op == "upper":
        return _string(value, location).upper()
    if operation.op == "integer":
        if isinstance(value, bool):
            raise ValueError(f"{location} cannot convert boolean to integer")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{location} cannot convert value to integer") from error
        if not parsed.is_integer():
            raise ValueError(f"{location} would lose precision converting to integer")
        return int(parsed)
    if operation.op == "number":
        if isinstance(value, bool):
            raise ValueError(f"{location} cannot convert boolean to number")
        try:
            return float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{location} cannot convert value to number") from error
    if operation.op == "boolean":
        if isinstance(value, bool):
            return value
        normalized = _string(value, location).strip().lower()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
        raise ValueError(f"{location} cannot convert value to boolean")
    if operation.op == "datetime":
        raw = _string(value, location).strip()
        try:
            parsed = datetime.strptime(raw, operation.format) if operation.format else datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{location} cannot parse datetime") from error
        if parsed.tzinfo is None:
            if operation.timezone is None:
                raise ValueError(f"{location} produced a naive datetime; configure operation.timezone")
            parsed = parsed.replace(tzinfo=ZoneInfo(operation.timezone))
        return parsed.isoformat()
    if operation.op == "split":
        return [item.strip() for item in _string(value, location).split(operation.delimiter or "") if item.strip()]
    if operation.op == "map":
        key = str(value)
        if key not in operation.values:
            raise ValueError(f"{location} has unmapped value {key!r}")
        return operation.values[key]
    raise ValueError(f"{location} has unsupported operation {operation.op}")


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{location} requires a string value")
    return value

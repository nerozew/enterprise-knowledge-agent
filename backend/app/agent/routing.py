from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.agent.models import RouteDecision, TodoCreateInput
from app.agent.protocols import StructuredOutput, TodoStructuredOutput

MIN_ROUTE_CONFIDENCE = 0.70


class StructuredOutputError(ValueError):
    pass


def _as_mapping(value: Mapping[str, Any] | str) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError("structured output is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise StructuredOutputError("structured output must be a JSON object")
        return decoded
    return value


def parse_route_decision(value: StructuredOutput) -> RouteDecision:
    if isinstance(value, RouteDecision):
        decision = value
    else:
        try:
            decision = RouteDecision.model_validate(_as_mapping(value))
        except (ValidationError, TypeError, ValueError) as exc:
            raise StructuredOutputError("route output failed schema validation") from exc
    if decision.confidence < MIN_ROUTE_CONFIDENCE:
        raise StructuredOutputError("route confidence is below the execution threshold")
    if decision.needs_clarification or decision.reason_code == "AMBIGUOUS":
        raise StructuredOutputError("route requires clarification")
    return decision


def parse_todo_arguments(value: TodoStructuredOutput) -> TodoCreateInput:
    if isinstance(value, TodoCreateInput):
        return value
    try:
        return TodoCreateInput.model_validate(_as_mapping(value))
    except (ValidationError, TypeError, ValueError) as exc:
        raise StructuredOutputError("todo arguments failed schema validation") from exc

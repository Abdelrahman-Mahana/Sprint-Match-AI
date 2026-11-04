"""Shared plumbing of the research agent's tools.

Every tool is a LangChain tool executed by LangGraph's ``ToolNode``. It receives the run's ``AgentContext``
through ``ToolRuntime`` (never through model-visible arguments), opens its own *read-only* unit of work with a
server-side statement timeout, calls ``AgentQueryService`` and returns a size-bounded JSON document validated
against a strict output schema.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from langgraph.prebuilt import ToolRuntime
from langgraph.prebuilt.tool_node import ToolInvocationError
from pydantic import BaseModel, ValidationError

from app.agents.context import AgentContext, AgentLimits
from app.application.agent_queries import AgentQueryService
from app.core.exceptions import SprintMatchError, categorize
from app.core.logging import redact
from app.domain.entities.task import ExecutionWindow, TaskRequirements

# Injected by the ToolNode (never visible to the model). Parameterized with Any so pydantic does not try to
# serialize the run context when tool inputs are traced.
AgentRuntimeArg = ToolRuntime[Any, Any]


class ToolExecutionError(SprintMatchError):
    code = "TOOL_EXECUTION_ERROR"
    http_status = 500


class ToolResultTooLargeError(ToolExecutionError):
    code = "TOOL_RESULT_TOO_LARGE"


def _trim(obj: Any, max_items: int, truncated: dict[str, int], path: str) -> Any:
    if isinstance(obj, list):
        if len(obj) > max_items:
            truncated[path or "$"] = len(obj)
            obj = obj[:max_items]
        return [_trim(x, max_items, truncated, f"{path}[]") for x in obj]
    if isinstance(obj, dict):
        return {k: _trim(v, max_items, truncated, f"{path}.{k}" if path else k) for k, v in obj.items()}
    return obj


def bounded_json(data: dict[str, Any], limits: AgentLimits) -> str:
    """Serialize within the configured item/character budget, marking any truncation explicitly."""
    items = limits.max_result_items
    while True:
        truncated: dict[str, int] = {}
        trimmed = _trim(data, items, truncated, "")
        if truncated:
            trimmed["_truncated"] = {"max_items": items, "original_lengths": truncated}
        text = json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(text) <= limits.max_result_chars:
            return text
        if items <= 1:
            raise ToolResultTooLargeError(
                f"Tool result exceeds {limits.max_result_chars} characters even after truncation"
            )
        items = max(1, items // 2)


def query_service(ctx: AgentContext, state: dict[str, Any], services: Any) -> AgentQueryService:
    req = state.get("requirements")
    window = state.get("execution_window")
    record = state.get("task_record") or {}
    return AgentQueryService(
        services,
        ctx.request.company,
        as_of=datetime.fromisoformat(state["as_of"]),
        config=ctx.container.config,
        analyzer=ctx.container.deterministic_analyzer,
        current_requirements=TaskRequirements.model_validate(req) if req else None,
        current_task_code=record.get("task_code"),
        current_window=ExecutionWindow.model_validate(window) if window else None,
    )


def run_query(runtime: ToolRuntime, fn: Callable[[AgentQueryService], BaseModel]) -> str:
    ctx = runtime.context
    if not isinstance(ctx, AgentContext):
        raise ToolExecutionError("Tools can only run inside a SprintMatch agent run")
    state: dict[str, Any] = dict(runtime.state or {})
    timeout_ms = int(ctx.limits.tool_timeout_seconds * 1000)
    with ctx.services(read_only=True, statement_timeout_ms=timeout_ms) as services:
        output = fn(query_service(ctx, state, services))
    return bounded_json(output.model_dump(mode="json"), ctx.limits)


def error_payload(code: str, message: str, *, retryable: bool = False) -> str:
    return json.dumps(
        {
            "error": {
                "code": code,
                "category": categorize(code).value,
                "message": redact(message)[:300],
                "retryable": retryable,
            }
        }
    )


def format_tool_error(e: Exception) -> str:
    """``ToolNode`` error handler: every failure becomes a structured, secret-free JSON error for the model."""
    if isinstance(e, SprintMatchError):
        return error_payload(e.code, e.message, retryable=e.retryable)
    if isinstance(e, ToolInvocationError | ValidationError):
        return error_payload("INVALID_TOOL_ARGUMENTS", "Arguments do not match the tool schema")
    return error_payload("INTERNAL_ERROR", "Unexpected tool failure")


def employee_codes_in(data: Any, limit: int = 50) -> list[str]:
    """Employee codes present in a tool result (schema fields ``employee_code`` / ``completed_by``)."""
    found: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "employee_code" and isinstance(v, str):
                    found.add(v)
                elif k == "completed_by" and isinstance(v, list):
                    found.update(x for x in v if isinstance(x, str))
                else:
                    walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    walk(data)
    return sorted(found)[:limit]

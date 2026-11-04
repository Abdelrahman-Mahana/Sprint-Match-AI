"""Execution policy around LangGraph's ToolNode (``wrap_tool_call`` / ``awrap_tool_call``).

For every tool call requested by the model, in order:

1. unknown tool -> rejected (``UNKNOWN_TOOL``), never executed;
2. malformed JSON or arguments not matching the strict schema (including unexpected keys) -> rejected;
3. role check (all tools are read-only; ``min_role`` still applies);
4. identical call already made in this run -> ``duplicate``: the cached result is returned, nothing re-runs;
5. per-run call budget and distinct-employee expansion limit -> rejected when exceeded;
6. execution by the ToolNode with a wall-clock timeout (plus the database statement timeout);
7. a sanitized ``ToolExecutionRecord`` and a structured log line; the ToolMessage goes back to the agent.

The model always receives a ToolMessage (success or structured error), so it can correct itself.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

from app.agents.context import AgentContext, call_signature
from app.agents.tools.base import employee_codes_in, error_payload
from app.domain.enums import Role, ToolExecutionStatus
from app.infrastructure.llm.chat_model import MALFORMED_ARGUMENTS_KEY

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sprintmatch-tool")

# Read-only tools; a higher role can be required per tool if one ever exposes more sensitive data.
TOOL_MIN_ROLE: dict[str, Role] = {}
_SCALAR_RESULT_KEYS = (
    "employee_code",
    "task_code",
    "status",
    "eligible",
    "suitability_score",
    "total",
    "returned",
    "eligible_count",
    "excluded_count",
    "found",
)


def summarize_arguments(args: dict[str, Any], signature: str) -> dict[str, Any]:
    out: dict[str, Any] = {"_signature": signature}
    for key, value in list(args.items())[:12]:
        if isinstance(value, str):
            out[key] = value[:80]
        elif isinstance(value, list):
            out[key] = [str(v)[:40] for v in value[:10]]
        elif isinstance(value, int | float | bool) or value is None:
            out[key] = value
        else:
            out[key] = type(value).__name__
    return out


def summarize_result(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except ValueError:
        return {"chars": len(content)}
    if not isinstance(data, dict):
        return {"chars": len(content)}
    if "error" in data and isinstance(data["error"], dict):
        return {"error": data["error"].get("code")}
    out: dict[str, Any] = {"chars": len(content), "employee_codes": employee_codes_in(data)}
    for key, value in data.items():
        if isinstance(value, list):
            out[f"{key}_count"] = len(value)
        elif key in _SCALAR_RESULT_KEYS:
            out[key] = value
    if "_truncated" in data:
        out["truncated"] = True
    return out


def _error_code(content: str) -> str | None:
    try:
        data = json.loads(content)
    except ValueError:
        return None
    err = data.get("error") if isinstance(data, dict) else None
    return err.get("code") if isinstance(err, dict) else None


@dataclass
class _Call:
    ctx: AgentContext
    name: str
    call_id: str
    args: dict[str, Any]
    signature: str
    round: int
    sequence: int
    started: float


class ToolCallPolicy:
    # --- shared steps ------------------------------------------------------------------------------
    def _prepare(self, request: ToolCallRequest) -> _Call:
        ctx = request.runtime.context
        if not isinstance(ctx, AgentContext):  # pragma: no cover - the graph always provides it
            raise TypeError("ToolNode must run inside a SprintMatch agent graph")
        state: dict[str, Any] = dict(request.state or {})
        messages = list(state.get("messages", []))
        records = list(state.get("agent_tool_results", []))
        ctx.workspace.sync_tool_state(records, messages)
        call = request.tool_call
        args = dict(call.get("args") or {})
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        ids = [c["id"] for c in last_ai.tool_calls] if last_ai else []
        index = ids.index(call["id"]) if call["id"] in ids else len(ids)
        return _Call(
            ctx=ctx,
            name=call["name"],
            call_id=str(call["id"]),
            args=args,
            signature=call_signature(call["name"], args),
            round=int(state.get("research_rounds", 0)),
            sequence=len(records) + index + 1,
            started=time.perf_counter(),
        )

    def _reject_reason(self, request: ToolCallRequest, c: _Call) -> tuple[str, str] | None:
        tool = request.tool
        if tool is None:
            return "UNKNOWN_TOOL", f"{c.name!r} is not an approved tool"
        if MALFORMED_ARGUMENTS_KEY in c.args:
            return "INVALID_TOOL_ARGUMENTS", "Arguments are not valid JSON"
        schema = tool.tool_call_schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            unexpected = sorted(set(c.args) - set(schema.model_fields))
            if unexpected:
                return "INVALID_TOOL_ARGUMENTS", f"Unexpected argument(s): {', '.join(unexpected)}"
            try:
                schema.model_validate(c.args)
            except ValidationError as exc:
                fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
                return "INVALID_TOOL_ARGUMENTS", f"Invalid argument(s): {', '.join(fields)}"
        min_role = TOOL_MIN_ROLE.get(c.name, Role.VIEWER)
        if c.ctx.request.role.rank < min_role.rank:
            return "FORBIDDEN", f"role {c.ctx.request.role.value} may not use {c.name}"
        return None

    def _limit_reason(self, c: _Call) -> tuple[str, str] | None:
        limits = c.ctx.limits
        ref = c.args.get("employee_id")
        if isinstance(ref, str) and not c.ctx.workspace.expand_employee(ref, limits.max_profile_expansions):
            return (
                "PROFILE_EXPANSION_LIMIT",
                f"At most {limits.max_profile_expansions} distinct employees can be inspected per run",
            )
        # Checked last so that rejected calls never consume the execution budget.
        if not c.ctx.workspace.acquire_call(limits.max_tool_calls):
            return (
                "TOOL_CALL_BUDGET_EXCEEDED",
                f"At most {limits.max_tool_calls} tool calls per recommendation",
            )
        return None

    def _finish(
        self,
        c: _Call,
        status: ToolExecutionStatus,
        content: str,
        error_code: str | None = None,
    ) -> Command[Any]:
        duration = int((time.perf_counter() - c.started) * 1000)
        record = {
            "sequence": c.sequence,
            "research_round": c.round,
            "tool_call_id": c.call_id,
            "tool_name": c.name,
            "status": status.value,
            "arguments_summary": summarize_arguments(c.args, c.signature),
            "result_summary": summarize_result(content),
            "error_code": error_code,
            "duration_ms": duration,
        }
        logger.info(
            "agent tool call",
            extra={
                "tool": c.name,
                "tool_status": status.value,
                "error_code": error_code,
                "duration_ms": duration,
                "request_id": c.ctx.request.request_id,
            },
        )
        ok = status in (ToolExecutionStatus.OK, ToolExecutionStatus.DUPLICATE)
        message = ToolMessage(
            content=content, name=c.name, tool_call_id=c.call_id, status="success" if ok else "error"
        )
        return Command(update={"messages": [message], "agent_tool_results": [record]})

    def _outcome(self, c: _Call, result: Any) -> tuple[ToolExecutionStatus, str, str | None]:
        message = result if isinstance(result, ToolMessage) else None
        if message is None:  # pragma: no cover - our tools never return Commands
            return (
                ToolExecutionStatus.ERROR,
                error_payload("TOOL_EXECUTION_ERROR", "Unexpected tool output"),
                ("TOOL_EXECUTION_ERROR"),
            )
        content = message.content if isinstance(message.content, str) else json.dumps(message.content)
        if message.status == "error":
            code = _error_code(content) or "TOOL_EXECUTION_ERROR"
            return ToolExecutionStatus.ERROR, content, code
        return ToolExecutionStatus.OK, content, None

    def _pre(self, request: ToolCallRequest) -> tuple[_Call, Command[Any] | Future[str] | None, bool]:
        """Returns (call, early result | duplicate future | None, owns_future)."""
        c = self._prepare(request)
        rejected = self._reject_reason(request, c)
        if rejected:
            code, message = rejected
            return c, self._finish(c, ToolExecutionStatus.REJECTED, error_payload(code, message), code), False
        first, fut = c.ctx.workspace.claim(c.signature)
        if not first:
            return c, fut, False
        limited = self._limit_reason(c)
        if limited:
            code, message = limited
            content = error_payload(code, message)
            fut.set_result(content)
            return c, self._finish(c, ToolExecutionStatus.REJECTED, content, code), True
        return c, fut, True

    def _duplicate(self, c: _Call, content: str) -> Command[Any]:
        return self._finish(c, ToolExecutionStatus.DUPLICATE, content, _error_code(content))

    def _timeout(self, c: _Call, fut: Future[str]) -> Command[Any]:
        content = error_payload(
            "TOOL_TIMEOUT", f"Tool exceeded {c.ctx.limits.tool_timeout_seconds:g}s", retryable=True
        )
        if not fut.done():
            fut.set_result(content)
        return self._finish(c, ToolExecutionStatus.TIMEOUT, content, "TOOL_TIMEOUT")

    # --- sync (graph.invoke / graph.stream) ------------------------------------------------------------
    def wrap(
        self, request: ToolCallRequest, execute: Callable[[ToolCallRequest], ToolMessage | Command[Any]]
    ) -> ToolMessage | Command[Any]:
        c, early, owns = self._pre(request)
        if isinstance(early, Command):
            return early
        assert early is not None
        if not owns:
            try:
                return self._duplicate(c, early.result(timeout=c.ctx.limits.tool_timeout_seconds))
            except FutureTimeout:
                return self._timeout(c, Future())
        context = contextvars.copy_context()
        job = _EXECUTOR.submit(context.run, execute, request)
        try:
            result = job.result(timeout=c.ctx.limits.tool_timeout_seconds)
        except FutureTimeout:
            return self._timeout(c, early)
        status, content, code = self._outcome(c, result)
        early.set_result(content)
        return self._finish(c, status, content, code)

    # --- async (graph.ainvoke / graph.astream) ---------------------------------------------------------
    async def awrap(
        self,
        request: ToolCallRequest,
        execute: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        c, early, owns = self._pre(request)
        if isinstance(early, Command):
            return early
        assert early is not None
        timeout = c.ctx.limits.tool_timeout_seconds
        if not owns:
            try:
                content = await asyncio.wait_for(asyncio.wrap_future(early), timeout)
            except TimeoutError:
                return self._timeout(c, Future())
            return self._duplicate(c, content)
        try:
            result = await asyncio.wait_for(execute(request), timeout)
        except TimeoutError:
            return self._timeout(c, early)
        status, content, code = self._outcome(c, result)
        early.set_result(content)
        return self._finish(c, status, content, code)

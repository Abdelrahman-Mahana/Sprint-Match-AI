"""Read-only LangGraph agent tools and 7-step ToolCallPolicy execution guardrails."""

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
from datetime import date, datetime
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolNode, ToolRuntime
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolInvocationError
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError

from app.agents.state import AgentContext, AgentLimits, call_signature
from app.application.queries import AgentQueryService
from app.core import SprintMatchError, categorize, redact
from app.domain import (
    EmploymentStatus,
    ExecutionWindow,
    Role,
    TaskCategory,
    TaskComplexity,
    TaskRequirements,
    ToolExecutionStatus,
)
from app.infrastructure.llm import MALFORMED_ARGUMENTS_KEY

logger = logging.getLogger(__name__)


# ===========================================================================
# Tool Base Helpers & Safety
# ===========================================================================

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

# ===========================================================================
# Tool Call Policy Guardrails
# ===========================================================================

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
    # shared steps
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

    # sync (graph.invoke / graph.stream)
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

    # async (graph.ainvoke / graph.astream)
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

# ===========================================================================
# Employee Lookup Tools
# ===========================================================================

EmployeeId = Annotated[str, Field(description="Employee code (e.g. EMP-001) or employee UUID", max_length=64)]
CategoryName = Literal[
    "backend_development",
    "frontend_development",
    "mobile_development",
    "data_engineering",
    "machine_learning",
    "devops",
    "qa_testing",
    "database",
    "security",
    "other",
]


@tool("search_employees")
def search_employees(
    runtime: AgentRuntimeArg,
    department: Annotated[str | None, Field(description="Exact department name", max_length=120)] = None,
    team: Annotated[str | None, Field(description="Exact team name", max_length=120)] = None,
    job_title: Annotated[str | None, Field(description="Part of the job title", max_length=120)] = None,
    required_skills: Annotated[
        list[str], Field(description="Skill names every result must hold", max_length=10)
    ] = [],  # noqa: B006 - immutable default for the tool schema
    employment_status: Literal["ACTIVE", "SUSPENDED", "INACTIVE"] | None = "ACTIVE",
    limit: Annotated[int, Field(ge=1, le=50)] = 25,
) -> str:
    """Find employees with database filters (department, team, job title, skills held, employment status).

    Returns employee codes and work-related profile fields only. Use it to discover candidates; it does not
    evaluate suitability.
    """
    return run_query(
        runtime,
        lambda q: q.search_employees(
            department=department,
            team=team,
            job_title=job_title,
            required_skills=list(required_skills),
            employment_status=EmploymentStatus(employment_status) if employment_status else None,
            limit=limit,
        ),
    )


@tool("get_employee_profile")
def get_employee_profile(employee_id: EmployeeId, runtime: AgentRuntimeArg) -> str:
    """Retrieve the Employee 360 professional profile: skills (declared, verification, effective level),
    certifications, projects, reviewed-performance indicators with sample counts, and availability in the
    current execution window."""
    return run_query(runtime, lambda q: q.employee_profile(employee_id))


@tool("get_employee_skills")
def get_employee_skills(employee_id: EmployeeId, runtime: AgentRuntimeArg) -> str:
    """Retrieve an employee's skills with declared level, verification status, effective level (after
    verification and staleness factors) and the types of supporting evidence."""
    return run_query(runtime, lambda q: q.employee_skills(employee_id))


@tool("get_employee_experience")
def get_employee_experience(employee_id: EmployeeId, runtime: AgentRuntimeArg) -> str:
    """Retrieve previous projects and responsibilities, relevant experience entries with their skills,
    completed tasks per project, and certifications/training."""
    return run_query(runtime, lambda q: q.employee_experience(employee_id))


@tool("get_employee_performance")
def get_employee_performance(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    date_from: Annotated[date | None, Field(description="Only outcomes reviewed on/after this date")] = None,
    date_to: Annotated[date | None, Field(description="Only outcomes reviewed on/before this date")] = None,
    task_category: CategoryName | None = None,
) -> str:
    """Retrieve historical task performance (quality, acceptance, rework, on-time delivery, delays) with
    sample counts. Missing evidence is reported as null, never as zero."""
    return run_query(
        runtime,
        lambda q: q.employee_performance(
            employee_id,
            date_from=date_from,
            date_to=date_to,
            task_category=TaskCategory(task_category) if task_category else None,
            recent_limit=10,
        ),
    )


EMPLOYEE_TOOLS = [
    search_employees,
    get_employee_profile,
    get_employee_skills,
    get_employee_experience,
    get_employee_performance,
]

# ===========================================================================
# Task Analysis & Requirement Tools
# ===========================================================================

TaskId = Annotated[
    str | None,
    Field(
        description="Task code (e.g. TASK-SCN-01) or UUID; default: the task being researched", max_length=64
    ),
]


@tool("get_task_details")
def get_task_details(runtime: AgentRuntimeArg, task_id: TaskId = None) -> str:
    """Retrieve a task with its project, sprint, priority, complexity, estimate, dates, required skills,
    required certifications and dependencies."""
    return run_query(runtime, lambda q: q.task_details(task_id))


@tool("get_task_required_skills")
def get_task_required_skills(runtime: AgentRuntimeArg, task_id: TaskId = None) -> str:
    """Retrieve the mandatory and optional skill requirements (with minimum levels and their source) and
    required certifications of a task."""
    return run_query(runtime, lambda q: q.task_required_skills(task_id))


@tool("search_similar_tasks")
def search_similar_tasks(
    runtime: AgentRuntimeArg,
    skills: Annotated[list[str], Field(description="Skill names of the new task", max_length=10)] = [],  # noqa: B006
    task_category: CategoryName | None = None,
    complexity: Literal["low", "medium", "high", "critical"] | None = None,
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
) -> str:
    """Find previously completed, reviewed tasks similar to the given skills/category/complexity, with who
    completed them and their average reviewed quality."""
    return run_query(
        runtime,
        lambda q: q.similar_tasks(
            skills=list(skills),
            task_category=TaskCategory(task_category) if task_category else None,
            complexity=TaskComplexity(complexity) if complexity else None,
            limit=limit,
        ),
    )


TASK_TOOLS = [get_task_details, get_task_required_skills, search_similar_tasks]

# ===========================================================================
# Workforce Capacity Tools
# ===========================================================================

WindowStart = Annotated[
    datetime | None, Field(description="Window start (ISO-8601 with offset); default: the task's window")
]
WindowEnd = Annotated[
    datetime | None, Field(description="Window end (ISO-8601 with offset); default: the task's window")
]
StartDate = Annotated[date | None, Field(description="First day (inclusive)")]
EndDate = Annotated[date | None, Field(description="Last day (inclusive)")]


@tool("get_employee_attendance")
def get_employee_attendance(
    employee_id: EmployeeId, runtime: AgentRuntimeArg, start_date: StartDate = None, end_date: EndDate = None
) -> str:
    """Retrieve attendance evidence (check-in/check-out records) and presence at evaluation time.
    Default range: the last 14 days. Presence today does not imply capacity for the task."""
    return run_query(runtime, lambda q: q.attendance(employee_id, start_date, end_date))


@tool("get_employee_leave")
def get_employee_leave(
    employee_id: EmployeeId, runtime: AgentRuntimeArg, start_date: StartDate = None, end_date: EndDate = None
) -> str:
    """Retrieve approved and requested leave overlapping a date range (default: next 14 days), with the
    approved and pending leave hours inside the range."""
    return run_query(runtime, lambda q: q.leaves(employee_id, start_date, end_date))


@tool("get_employee_schedule")
def get_employee_schedule(
    employee_id: EmployeeId, runtime: AgentRuntimeArg, start_date: StartDate = None, end_date: EndDate = None
) -> str:
    """Retrieve the weekly working schedule, company holidays and scheduled hours for a date range
    (default: the next 7 days)."""
    return run_query(runtime, lambda q: q.schedule(employee_id, start_date, end_date))


@tool("get_employee_assignments")
def get_employee_assignments(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Retrieve current planned/active task assignments relevant to an execution window."""
    return run_query(runtime, lambda q: q.assignments(employee_id, q.window(window_start, window_end)))


@tool("calculate_employee_workload")
def calculate_employee_workload(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Calculate remaining committed work inside an execution window per assignment (with the effort basis
    and certainty; unknown remaining effort is flagged, never assumed)."""
    return run_query(runtime, lambda q: q.workload(employee_id, q.window(window_start, window_end)))


@tool("calculate_employee_capacity")
def calculate_employee_capacity(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Calculate remaining working capacity in an execution window: scheduled, holiday and leave hours,
    committed work, other commitments and available capacity (with the formula used)."""
    return run_query(runtime, lambda q: q.capacity(employee_id, q.window(window_start, window_end)))


@tool("get_employee_availability")
def get_employee_availability(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Return the availability status (AVAILABLE, PARTIALLY_AVAILABLE, BUSY, UNAVAILABLE, UNKNOWN) for an
    execution window with the supporting hours and warnings."""
    return run_query(runtime, lambda q: q.availability(employee_id, q.window(window_start, window_end)))


WORKFORCE_TOOLS = [
    get_employee_attendance,
    get_employee_leave,
    get_employee_schedule,
    get_employee_assignments,
    calculate_employee_workload,
    calculate_employee_capacity,
    get_employee_availability,
]

# ===========================================================================
# Official Ranking & Eligibility Tools
# ===========================================================================

@tool("evaluate_employee_eligibility")
def evaluate_employee_eligibility(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    task_id: TaskId = None,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Execute the deterministic eligibility rules (employment status, mandatory skills and thresholds,
    certifications, access, leave, capacity) for one employee and a task."""
    return run_query(
        runtime, lambda q: q.eligibility(employee_id, task_id, q.window(window_start, window_end))
    )


@tool("get_candidate_score")
def get_candidate_score(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    task_id: TaskId = None,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Retrieve the deterministic candidate evaluation: suitability score (0-100, not a probability),
    criterion breakdown with weights and evidence, data coverage and confidence."""
    return run_query(
        runtime, lambda q: q.candidate_score(employee_id, task_id, q.window(window_start, window_end))
    )


@tool("rank_task_candidates")
def rank_task_candidates(
    runtime: AgentRuntimeArg,
    task_id: TaskId = None,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
    top_n: Annotated[int, Field(ge=1, le=10)] = 5,
) -> str:
    """Execute the official recommendation engine for a task and return the ranked eligible candidates and
    a summary of exclusion reasons."""
    return run_query(runtime, lambda q: q.rank(task_id, q.window(window_start, window_end), top_n))


@tool("get_recommendation_evidence")
def get_recommendation_evidence(
    recommendation_id: Annotated[str, Field(description="Recommendation UUID", max_length=64)],
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
) -> str:
    """Retrieve the stored evidence (score components, exclusion reasons) of an employee in an existing
    recommendation."""
    return run_query(runtime, lambda q: q.recommendation_evidence(recommendation_id, employee_id))


@tool("explain_exclusion_reason")
def explain_exclusion_reason(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    task_id: TaskId = None,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Return the structured reasons why an employee is excluded from a task (or confirm eligibility)."""
    return run_query(
        runtime, lambda q: q.explain_exclusion(employee_id, task_id, q.window(window_start, window_end))
    )


RECOMMENDATION_TOOLS = [
    evaluate_employee_eligibility,
    get_candidate_score,
    rank_task_candidates,
    get_recommendation_evidence,
    explain_exclusion_reason,
]

# ===========================================================================
# ToolNode Builder & Registry
# ===========================================================================

ALL_TOOLS: list[BaseTool] = [*EMPLOYEE_TOOLS, *WORKFORCE_TOOLS, *TASK_TOOLS, *RECOMMENDATION_TOOLS]
TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in ALL_TOOLS)
TOOL_NODE_NAME = "tool_execution"

assert len(set(TOOL_NAMES)) == len(TOOL_NAMES), "tool names must be unique"


def build_tool_node() -> ToolNode:
    policy = ToolCallPolicy()
    return ToolNode(
        ALL_TOOLS,
        name=TOOL_NODE_NAME,
        handle_tool_errors=format_tool_error,
        wrap_tool_call=policy.wrap,
        awrap_tool_call=policy.awrap,
    )

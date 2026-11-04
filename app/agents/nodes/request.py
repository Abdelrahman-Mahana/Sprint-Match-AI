"""Nodes 1-3: request validation, task context, task analysis."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.agents.context import AgentContext
from app.agents.nodes.common import FAILED, State, as_of, error_entry, traced
from app.application.tasks import task_input_from_record
from app.core.exceptions import InputValidationError
from app.domain.enums import RecommendationStatus
from app.schemas.recommendations import RecommendationRequest


@traced("validate_request")
def validate_request(state: State, ctx: AgentContext) -> dict[str, Any]:
    """Request structure, task reference, as_of and window shape, tenant, and database availability."""
    try:
        req = RecommendationRequest.model_validate(state["request"])
    except ValidationError as exc:
        return {
            "errors": [
                error_entry("validate_request", "INVALID_INPUT", f"{exc.error_count()} validation error(s)")
            ],
            "execution_status": FAILED,
        }
    if as_of(state).tzinfo is None:
        raise InputValidationError("as_of must include a timezone offset")
    if state.get("company_id") != str(ctx.request.company.id):
        raise InputValidationError("Run does not belong to the caller's company")
    w = req.execution_window
    if w is not None and w.start and w.deadline and w.deadline <= w.start:
        raise InputValidationError("execution_window.deadline must be after start")
    with ctx.services() as s:
        s.uow.ping()  # DataSourceUnavailableError is retryable: RetryPolicy, then a resumable stop
    return {}


@traced("load_task_context")
def load_task_context(state: State, ctx: AgentContext) -> dict[str, Any]:
    req = RecommendationRequest.model_validate(state["request"])
    with ctx.services() as s:
        record = None
        if req.task is not None:
            task_input = req.task.to_task_input()
            planned_start, deadline, sprint_id = (
                task_input.planned_start,
                task_input.deadline,
                task_input.sprint_id,
            )
        else:
            record = s.tasks.get(ctx.request.company, task_id=req.task_id, task_code=req.task_code)
            task_input = task_input_from_record(record)
            planned_start, deadline, sprint_id = record.planned_start, record.deadline, record.sprint_id
        window, warnings = s.workforce.resolve_window(
            ctx.request.company,
            as_of(state),
            req.execution_window,
            planned_start=planned_start,
            deadline=deadline,
            sprint_id=sprint_id,
        )
    open_deps = (
        [d.task_code for d in record.dependencies if d.status.value not in ("DONE", "CANCELLED")]
        if record
        else []
    )
    if open_deps:
        warnings.append(f"OPEN_DEPENDENCIES:{','.join(open_deps)}")
    return {
        "task_record": record.model_dump(mode="json") if record else None,
        "task_input": task_input.model_dump(mode="json"),
        "execution_window": window.model_dump(mode="json"),
        "agent_warnings": list(warnings),
    }


@traced("task_analysis")
def task_analysis(state: State, ctx: AgentContext) -> dict[str, Any]:
    """Manager/task-record values first, validated LLM suggestions second, deterministic defaults last."""
    from app.domain.entities.task import TaskInput

    task_input = TaskInput.model_validate(state["task_input"])
    with ctx.services() as s:
        analysis = s.tasks.analyze(ctx.request.company, task_input, persist=False)
    update: dict[str, Any] = {
        "task_analysis": analysis.model_dump(mode="json"),
        "agent_warnings": list(analysis.warnings),
    }
    if analysis.status == "READY" and analysis.requirements is not None:
        update["requirements"] = analysis.requirements.model_dump(mode="json")
    elif analysis.status == "PROVIDER_UNAVAILABLE":
        update["execution_status"] = RecommendationStatus.PROVIDER_UNAVAILABLE.value
    else:
        update["execution_status"] = RecommendationStatus.NEEDS_INPUT.value
    return update

"""Node plumbing: tracing, failure semantics and access to authoritative data.

Failure semantics of every node:
* retryable ``SprintMatchError`` (database unavailable, provider timeout ...) is re-raised: LangGraph's
  ``RetryPolicy`` retries the node, and if it still fails the run stops *at that node* with its checkpoint
  intact, so it can be resumed later (``POST /agent/runs/{id}/retry``);
* any other ``SprintMatchError`` becomes a structured error with ``execution_status = FAILED`` and the graph
  routes to the failure node;
* an unexpected exception fails closed (``INTERNAL_ERROR``): a result is never fabricated.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime

from app.agents.context import AgentContext
from app.agents.state import SprintMatchAgentState
from app.application.pipeline import RecommendationPipeline
from app.core.exceptions import SprintMatchError, categorize
from app.domain.entities.recommendation import EligibilityResult, EngineResult
from app.domain.entities.task import ExecutionWindow, TaskRequirements
from app.domain.entities.workforce import AvailabilityResult
from app.domain.enums import RecommendationStatus
from app.infrastructure.llm.usage import current_meter
from app.intelligence.recommendation.engine import CandidateInput

logger = logging.getLogger(__name__)

State = SprintMatchAgentState
NodeFn = Callable[[State, AgentContext], dict[str, Any]]
FAILED = RecommendationStatus.FAILED.value


def error_entry(node: str, code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {
        "code": code,
        "category": categorize(code).value,
        "message": message,
        "node": node,
        "retryable": retryable,
    }


def _summary(update: dict[str, Any]) -> dict[str, Any]:
    """Sanitized output summary: sizes and statuses only, never personal data."""
    out: dict[str, Any] = {}
    for key, value in update.items():
        if key in ("trace", "errors", "messages"):
            continue
        if isinstance(value, dict | list | tuple):
            out[key] = len(value)
        elif key in ("execution_status", "persisted", "research_rounds") or value is None:
            out[key] = value
        else:
            out[key] = type(value).__name__
    return out


def traced(name: str) -> Callable[[NodeFn], Callable[[State, Runtime[AgentContext]], dict[str, Any]]]:
    def decorate(fn: NodeFn) -> Callable[[State, Runtime[AgentContext]], dict[str, Any]]:
        def node(state: State, runtime: Runtime[AgentContext]) -> dict[str, Any]:
            ctx = runtime.context
            attempt = ctx.workspace.count_attempt(name)
            started_at = datetime.now(UTC)
            started = time.perf_counter()
            meter = current_meter()
            before = meter.snapshot() if meter else (0, 0, 0)
            error_message = None
            try:
                update = fn(state, ctx) or {}
                status = "ok"
            except GraphBubbleUp:
                raise
            except SprintMatchError as exc:
                if exc.retryable:
                    logger.warning(
                        "node failed (retryable)",
                        extra={"node": name, "attempt": attempt, "error_code": exc.code},
                    )
                    raise
                update = {
                    "errors": [error_entry(name, exc.code, exc.message, exc.retryable)],
                    "execution_status": FAILED,
                }
                status, error_message = "error", exc.code
            except Exception as exc:  # unexpected defect: fail closed, never fabricate a result
                logger.exception("node crashed", extra={"node": name})
                update = {
                    "errors": [error_entry(name, "INTERNAL_ERROR", type(exc).__name__)],
                    "execution_status": FAILED,
                }
                status, error_message = "error", "INTERNAL_ERROR"
            after = meter.snapshot() if meter else (0, 0, 0)
            duration = int((time.perf_counter() - started) * 1000)
            summary = _summary(update)
            if after[0] - before[0]:
                summary["llm_calls"] = after[0] - before[0]
            entry = {
                "node_name": name,
                "execution_status": status,
                "duration_ms": duration,
                "attempts": attempt,
                "input_summary": {"status_in": str(state.get("execution_status") or "")},
                "output_summary": summary,
                "error_message": error_message,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "llm_prompt_tokens": (after[1] - before[1]) or None,
                "llm_completion_tokens": (after[2] - before[2]) or None,
            }
            logger.info(
                "node finished",
                extra={
                    "node": name,
                    "status": status,
                    "duration_ms": duration,
                    "request_id": state.get("request_id"),
                },
            )
            return {**update, "trace": [*update.get("trace", []), entry]}

        node.__name__ = name
        return node

    return decorate


# --- typed views over the JSON state -------------------------------------------------------------------
def as_of(state: State) -> datetime:
    return datetime.fromisoformat(state["as_of"])


def window(state: State) -> ExecutionWindow:
    return ExecutionWindow.model_validate(state["execution_window"])


def requirements(state: State) -> TaskRequirements:
    return TaskRequirements.model_validate(state["requirements"])


def employee_ids(state: State) -> list[UUID]:
    return [UUID(x) for x in state.get("employee_ids", [])]


def availability(state: State) -> dict[UUID, AvailabilityResult]:
    return {
        UUID(k): AvailabilityResult.model_validate(v)
        for k, v in state.get("availability_results", {}).items()
    }


def eligibility(state: State) -> dict[UUID, EligibilityResult]:
    return {
        UUID(k): EligibilityResult.model_validate(v) for k, v in state.get("eligibility_results", {}).items()
    }


def engine_result(state: State) -> EngineResult | None:
    raw = state.get("engine_result")
    return EngineResult.model_validate(raw) if raw else None


# --- authoritative data (workspace, rebuilt from PostgreSQL after a resume) ---------------------------------
def ensure_profiles(state: State, ctx: AgentContext, pipeline: RecommendationPipeline) -> None:
    if ctx.workspace.profiles is None:
        ctx.workspace.profiles = pipeline.profiles(employee_ids(state))


def ensure_workforce(state: State, ctx: AgentContext, pipeline: RecommendationPipeline) -> None:
    if ctx.workspace.workforce is None or ctx.workspace.holidays is None:
        data, holidays = pipeline.workforce(employee_ids(state), window(state), as_of(state))
        ctx.workspace.workforce, ctx.workspace.holidays = data, holidays


def ensure_performance(state: State, ctx: AgentContext, pipeline: RecommendationPipeline) -> None:
    if ctx.workspace.performance is None:
        ctx.workspace.performance = pipeline.performance(employee_ids(state))


def candidates(
    state: State, ctx: AgentContext, pipeline: RecommendationPipeline, *, with_performance: bool
) -> tuple[CandidateInput, ...]:
    ensure_profiles(state, ctx, pipeline)
    if with_performance:
        ensure_performance(state, ctx, pipeline)
    assert ctx.workspace.profiles is not None
    return pipeline.candidates(
        employee_ids(state),
        ctx.workspace.profiles,
        availability(state),
        ctx.workspace.performance if with_performance else None,
    )


def pipeline_for(ctx: AgentContext, services: Any) -> RecommendationPipeline:
    return RecommendationPipeline(services, ctx.request.company)

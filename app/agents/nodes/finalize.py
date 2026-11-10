"""Nodes 13-16: explanation, final validation, failure handling and persistence.

``build_response`` and ``persist_run`` are shared with the service layer, which uses them for runs that stop
on a retryable error (resumable from their checkpoint) so every outcome has the same contract.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from app.agents.context import AgentContext
from app.agents.nodes.common import (
    FAILED,
    State,
    as_of,
    candidates,
    eligibility,
    engine_result,
    error_entry,
    pipeline_for,
    requirements,
    traced,
)
from app.agents.prompts import RESEARCH_PROMPT_VERSION
from app.core.exceptions import ErrorCategory, SprintMatchError, categorize
from app.domain.entities.recommendation import EngineResult
from app.domain.enums import AgentMode, AvailabilityStatus, RecommendationStatus, ToolExecutionStatus
from app.domain.interfaces import (
    ExecutionTraceEntry,
    RecommendationRunRecord,
    RunErrorRecord,
    ToolExecutionRecord,
)
from app.domain.policies import ALGORITHM_VERSION
from app.intelligence.prompts import PROMPT_VERSION
from app.intelligence.recommendation.explanation import (
    deterministic_explanation,
    explanation_evidence,
    generate_explanation,
    is_grounded,
)
from app.schemas.recommendations import (
    CandidateOut,
    DataQualityOut,
    ErrorOut,
    ExecutionWindowOut,
    LLMUsageOut,
    RecommendationRequest,
    RecommendationResponse,
    RevalidationOut,
    TraceEntryOut,
)

logger = logging.getLogger(__name__)
_USABLE = (
    RecommendationStatus.SUCCESS,
    RecommendationStatus.PARTIAL_DATA,
    RecommendationStatus.NO_SUITABLE_CANDIDATE,
)


# --- explanation -------------------------------------------------------------------------------------
@traced("explanation_agent")
def explanation_agent(state: State, ctx: AgentContext) -> dict[str, Any]:
    """LLM rewrite of validated results (grounding-checked) or the deterministic explanation; never rescoring."""
    result = engine_result(state)
    assert result is not None
    text, source, warnings = generate_explanation(
        requirements(state), result, ctx.container.llm, use_llm=ctx.explanation_mode == "llm"
    )
    return {"final_explanation": {"text": text, "source": source}, "agent_warnings": list(warnings)}


# --- final validation --------------------------------------------------------------------------------
def check_invariants(result: EngineResult) -> list[str]:
    problems = []
    for i, c in enumerate(result.ranked, start=1):
        if (
            not c.eligible
            or c.rank != i
            or c.suitability_score is None
            or not 0 <= c.suitability_score <= 100
        ):
            problems.append(f"invalid ranked candidate {c.employee_code}")
        if abs(sum(x.weight for x in c.criteria) - 1.0) > 1e-3:
            problems.append(f"weights do not sum to 1 for {c.employee_code}")
    if any(c.rank is not None or c.eligible for c in result.excluded):
        problems.append("excluded candidate carries a rank")
    if (
        result.status in (RecommendationStatus.SUCCESS, RecommendationStatus.PARTIAL_DATA)
        and not result.ranked
    ):
        problems.append("successful status without an eligible candidate")
    if result.status == RecommendationStatus.NO_SUITABLE_CANDIDATE and result.ranked:
        problems.append("NO_SUITABLE_CANDIDATE with ranked candidates")
    return problems


def _validate_primary(state: State, ctx: AgentContext, result: EngineResult) -> dict[str, bool]:
    """Independent re-checks of the primary candidate against PostgreSQL and the deterministic engine."""
    primary = result.primary
    assert primary is not None
    req = requirements(state)
    elig = eligibility(state).get(primary.employee_id)
    checks: dict[str, bool] = {
        "exactly_one_primary": sum(1 for c in result.ranked if c.rank == 1) == 1,
        "candidate_eligible": bool(elig and elig.eligible and not elig.exclusion_reasons),
        "required_evidence_present": bool(primary.criteria) and any(c.has_evidence for c in primary.criteria),
    }
    with ctx.services() as s:
        p = pipeline_for(ctx, s)
        checks["employee_exists"] = (
            s.uow.employees.get_employee(ctx.request.company.id, primary.employee_id) is not None
        )
        candidate = next(
            (
                c
                for c in candidates(state, ctx, p, with_performance=True)
                if c.profile.employee.id == primary.employee_id
            ),
            None,
        )
        if candidate is None or elig is None:
            checks["score_matches_engine"] = False
            checks["mandatory_constraints_met"] = False
            checks["availability_sufficient"] = False
            return checks
        rescored = s.engine.score_candidate(req, candidate, elig, as_of(state))
        assessments = s.engine.assess_skills(candidate.profile, as_of(state))
    checks["score_matches_engine"] = (
        rescored.suitability_score is not None
        and primary.suitability_score is not None
        and abs(rescored.suitability_score - primary.suitability_score) <= 0.01
    )
    checks["mandatory_constraints_met"] = all(
        r.skill_id in assessments and assessments[r.skill_id].effective_level >= r.minimum_level
        for r in req.mandatory_skills
    )
    buffer = ctx.container.config.eligibility.capacity_buffer
    checks["availability_sufficient"] = (
        candidate.availability.status
        in (
            AvailabilityStatus.AVAILABLE,
            AvailabilityStatus.PARTIALLY_AVAILABLE,
        )
        and candidate.availability.available_capacity_hours + 1e-6 >= req.estimated_hours * buffer
    )
    return checks


@traced("final_validation")
def final_validation(state: State, ctx: AgentContext) -> dict[str, Any]:
    status = state.get("execution_status")
    result = engine_result(state)
    checks: dict[str, bool] = {}
    problems: list[str] = []
    update: dict[str, Any] = {}
    if result is not None and status is None:
        problems = check_invariants(result)
        checks["engine_invariants"] = not problems
        if result.primary is not None:
            checks.update(_validate_primary(state, ctx, result))
            problems += [
                f"check failed: {k}" for k, ok in checks.items() if not ok and k != "engine_invariants"
            ]
        explanation = state.get("final_explanation") or {}
        if explanation.get("source") == "llm":
            evidence = explanation_evidence(requirements(state), result)
            name = result.primary.full_name if result.primary else None
            grounded = is_grounded(explanation.get("text", ""), evidence, name)
            checks["explanation_grounded"] = grounded
            if not grounded:  # invalid explanation -> deterministic explanation, not a failed run
                update["final_explanation"] = {
                    "text": deterministic_explanation(requirements(state), result),
                    "source": "deterministic",
                }
                update["agent_warnings"] = ["LLM_EXPLANATION_REJECTED_AT_FINAL_VALIDATION"]
        status = FAILED if problems else result.status.value
    elif status is None:
        problems = ["no deterministic result was produced"]
        status = FAILED
    update["validation"] = {"valid": not problems, "checks": checks, "problems": problems}
    if problems:
        update["errors"] = [error_entry("final_validation", "FINAL_VALIDATION_FAILED", "; ".join(problems))]
        update["execution_status"] = FAILED
        return update
    merged = {**state, **update, "execution_status": status}
    update["execution_status"] = status
    update["final_recommendation"] = build_response(merged, ctx).model_dump(mode="json")
    return update


@traced("failure_node")
def failure_node(state: State, ctx: AgentContext) -> dict[str, Any]:
    """Standardized failure: status FAILED with categorized errors and no candidate, never a fabricated result."""
    errors = state.get("errors", [])
    extra = (
        [] if errors else [error_entry("failure_node", "GRAPH_EXECUTION_ERROR", "run failed without detail")]
    )
    merged = {**state, "errors": [*errors, *extra], "execution_status": FAILED}
    out: dict[str, Any] = {
        "execution_status": FAILED,
        "final_recommendation": build_response(merged, ctx).model_dump(mode="json"),
    }
    if extra:
        out["errors"] = extra
    return out


@traced("save_recommendation")
def save_recommendation(state: State, ctx: AgentContext) -> dict[str, Any]:
    response = _with_current_trace(
        RecommendationResponse.model_validate(state["final_recommendation"]), state
    )
    if not ctx.persist:
        return {"final_recommendation": response.model_dump(mode="json"), "persisted": False}
    try:
        now = datetime.now(UTC).isoformat()
        own = {  # the persisted log includes the persistence step itself
            "node_name": "save_recommendation",
            "execution_status": "ok",
            "duration_ms": 0,
            "attempts": 1,
            "input_summary": {"status_in": str(state.get("execution_status") or "")},
            "output_summary": {"persisted": True},
            "error_message": None,
            "started_at": now,
            "finished_at": now,
        }
        final = persist_run({**state, "trace": [*state.get("trace", []), own]}, ctx, response)
    except SprintMatchError as exc:
        if exc.retryable:
            raise  # database unavailable: retried, then a resumable stop right before persistence
        logger.error("persisting recommendation failed", extra={"error_code": exc.code})
        failed = response.model_copy(
            update={
                "errors": [
                    *response.errors,
                    ErrorOut(code="PERSISTENCE_FAILED", message=exc.message, node="save_recommendation"),
                ]
            }
        )
        return {"final_recommendation": failed.model_dump(mode="json"), "persisted": False}
    return {"final_recommendation": final.model_dump(mode="json"), "persisted": True}


def _with_current_trace(response: RecommendationResponse, state: Mapping[str, Any]) -> RecommendationResponse:
    """The response was built in final_validation/failure_node; refresh the execution trace up to now."""
    trace = state.get("trace", [])
    return response.model_copy(
        update={
            "trace": [
                TraceEntryOut(
                    node=t["node_name"],
                    status=t["execution_status"],
                    duration_ms=t["duration_ms"],
                    attempts=t.get("attempts", 1),
                    error=t.get("error_message"),
                )
                for t in trace
            ],
            "execution_time_ms": sum(int(t.get("duration_ms", 0)) for t in trace),
            "llm_usage": llm_usage(state),
        }
    )


# --- shared builders ---------------------------------------------------------------------------------------
def _non_candidate_explanation(
    state: Mapping[str, Any], status: RecommendationStatus, is_ar: bool = False
) -> str:
    analysis = state.get("task_analysis") or {}
    missing = analysis.get("missing_information", [])
    if is_ar:
        if status == RecommendationStatus.NEEDS_INPUT:
            missing_ar = [
                "المهارات المطلوبة (required_skills)"
                if m == "required_skills"
                else "ساعات العمل المقدرة (estimated_hours)"
                if m == "estimated_hours"
                else m
                for m in missing
            ]
            return (
                "المعلومات الأساسية للمهمة غير مكتملة: "
                + "، ".join(missing_ar)
                + ". يُرجى توفيرها (المتطلبات المحددة لها الأولوية) وإعادة طلب الترشيح."
            )
        if status == RecommendationStatus.PROVIDER_UNAVAILABLE:
            return (
                "المهمة لا تحتوي على مهارات محددة ومزود الذكاء الاصطناعي غير متاح حالياً لاستخراجها تلقائياً. "
                "يُرجى تحديد المهارات المطلوبة صراحةً أو إعادة المحاولة لاحقاً."
            )
        if status == RecommendationStatus.NO_SUITABLE_CANDIDATE:
            return "لا توجد سجلات موظفين متاحة لهذه الشركة."
        return "تعذر إكمال طلب الترشيح؛ راجع الأخطاء المسجلة."

    if status == RecommendationStatus.NEEDS_INPUT:
        return (
            "Critical task information is missing: "
            + ", ".join(missing)
            + ". Provide it (structured requirements take priority) and request the recommendation again."
        )
    if status == RecommendationStatus.PROVIDER_UNAVAILABLE:
        return (
            "The task has no structured skill requirements and the configured LLM provider could not be used "
            "to extract them. Provide required skills explicitly or retry when the provider is available."
        )
    if status == RecommendationStatus.NO_SUITABLE_CANDIDATE:
        return "No employee records are available for this company."
    return "The recommendation could not be completed. See errors."


def _revalidation(
    state: Mapping[str, Any], status: RecommendationStatus, primary_id: UUID | None, eligible_ids: set[UUID]
) -> RevalidationOut | None:
    prev = state.get("previous_snapshot")
    if not prev or not state.get("revalidated_from_id"):
        return None
    prev_primary = (prev.get("primary_candidate") or {}).get("employee_id")
    prev_primary_id = str(prev_primary) if prev_primary else None
    current = str(primary_id) if primary_id else None
    changes = []
    if prev.get("status") != status.value:
        changes.append(f"STATUS_CHANGED:{prev.get('status')}->{status.value}")
    if prev_primary_id != current:
        changes.append("PRIMARY_CANDIDATE_CHANGED")
    if prev_primary_id and prev_primary_id not in {str(x) for x in eligible_ids}:
        changes.append("PREVIOUS_PRIMARY_NO_LONGER_ELIGIBLE")
    return RevalidationOut(
        revalidated_from=UUID(str(state["revalidated_from_id"])),
        previous_primary_employee_id=prev_primary_id,
        current_primary_employee_id=current,
        still_valid=not changes,
        changes=changes,
    )


def _request(state: Mapping[str, Any]) -> RecommendationRequest | None:
    try:
        return RecommendationRequest.model_validate(state.get("request") or {})
    except ValueError:
        return None


def llm_usage(state: Mapping[str, Any]) -> LLMUsageOut | None:
    calls = prompt = completion = 0
    for t in state.get("trace", []):
        calls += int((t.get("output_summary") or {}).get("llm_calls", 0) or 0)
        prompt += t.get("llm_prompt_tokens") or 0
        completion += t.get("llm_completion_tokens") or 0
    if not (calls or prompt or completion):
        return None
    return LLMUsageOut(calls=calls, prompt_tokens=prompt, completion_tokens=completion)


def build_response(
    state: Mapping[str, Any],
    ctx: AgentContext,
    *,
    extra_errors: list[dict[str, Any]] | None = None,
    resumable: bool = False,
) -> RecommendationResponse:
    status = RecommendationStatus(state.get("execution_status") or FAILED)
    req = _request(state)
    result = engine_result(cast(State, state)) if status in _USABLE else None
    explanation_state = state.get("final_explanation") or {}
    explanation = explanation_state.get("text")
    source = explanation_state.get("source")
    record = state.get("task_record") or None
    task_input = state.get("task_input") or {}
    task_title = record["title"] if record else task_input.get("title")
    from app.intelligence.recommendation.explanation import is_arabic
    is_ar = is_arabic(task_title)

    if status != RecommendationStatus.FAILED and result is None and explanation is None:
        explanation, source = _non_candidate_explanation(state, status, is_ar=is_ar), "deterministic"
    ranked = list(result.ranked) if result else []
    primary = ranked[0] if ranked else None
    include = req.include_alternatives if req else True
    max_alt = req.max_alternatives if req else 3
    analysis = state.get("task_analysis")
    window = state.get("execution_window")
    trace = state.get("trace", [])
    errors = [*state.get("errors", []), *(extra_errors or [])]
    research_used = ctx.agent_mode != AgentMode.DISABLED
    return RecommendationResponse(
        recommendation_id=UUID(state["recommendation_id"]),
        request_id=state.get("request_id", ""),
        status=status,
        task_id=UUID(record["id"]) if record else None,
        task_code=record["task_code"] if record else None,
        task_title=task_title,
        execution_window=ExecutionWindowOut.model_validate(window) if window else None,
        primary_candidate=CandidateOut.from_evaluation(primary, is_arabic_task=is_ar) if primary else None,
        alternatives=[CandidateOut.from_evaluation(c, is_arabic_task=is_ar) for c in ranked[1 : 1 + max_alt]] if include else [],
        excluded_candidates=[CandidateOut.from_evaluation(c, is_arabic_task=is_ar) for c in result.excluded] if result else [],
        explanation=explanation if status != RecommendationStatus.FAILED else None,
        explanation_source=source if status != RecommendationStatus.FAILED else None,
        warnings=list(dict.fromkeys(state.get("agent_warnings", []))),
        missing_information=list((analysis or {}).get("missing_information", [])),
        task_analysis=analysis,
        data_quality=DataQualityOut(**result.data_quality.model_dump(exclude={"notes"})) if result else None,
        algorithm_version=ALGORITHM_VERSION,
        scoring_config_fingerprint=result.scoring_config_fingerprint if result else None,
        prompt_version=(f"{PROMPT_VERSION}+{RESEARCH_PROMPT_VERSION}" if research_used else PROMPT_VERSION)
        if ctx.llm_provider
        else None,
        llm_provider=ctx.llm_provider,
        llm_model=ctx.llm_model,
        as_of=state["as_of"],
        execution_time_ms=sum(int(t.get("duration_ms", 0)) for t in trace),
        trace=[
            TraceEntryOut(
                node=t["node_name"],
                status=t["execution_status"],
                duration_ms=t["duration_ms"],
                attempts=t.get("attempts", 1),
                error=t.get("error_message"),
            )
            for t in trace
        ],
        errors=[
            ErrorOut(**{k: e[k] for k in ("code", "message", "node", "retryable") if k in e}) for e in errors
        ],
        persisted=False,
        revalidation=_revalidation(
            state, status, primary.employee_id if primary else None, {c.employee_id for c in ranked}
        ),
        thread_id=state.get("thread_id"),
        agent_mode=ctx.agent_mode,
        research=state.get("research"),
        tool_calls=sum(
            1
            for r in state.get("agent_tool_results", [])
            if r.get("status") != ToolExecutionStatus.DUPLICATE.value
        ),
        llm_usage=llm_usage(state),
        resumable=resumable,
        retry_of_id=UUID(state["retry_of_id"]) if state.get("retry_of_id") else None,
    )


def persist_run(
    state: Mapping[str, Any], ctx: AgentContext, response: RecommendationResponse
) -> RecommendationResponse:
    """Upsert the run (resumed runs replace their failed record) with candidates, logs, tools and errors."""
    final = response.model_copy(update={"persisted": True})
    result = engine_result(cast(State, state)) if response.status != RecommendationStatus.FAILED else None
    usage = response.llm_usage
    first_error = response.errors[0] if response.errors else None
    run = RecommendationRunRecord(
        id=response.recommendation_id,
        company_id=ctx.request.company.id,
        request_id=response.request_id,
        task_id=response.task_id,
        status=response.status,
        requested_by=state.get("requested_by") or ctx.request.actor,
        as_of=response.as_of,
        window_start=response.execution_window.start if response.execution_window else None,
        window_end=response.execution_window.end if response.execution_window else None,
        task_analysis=response.task_analysis,
        algorithm_version=response.algorithm_version,
        scoring_config_fingerprint=response.scoring_config_fingerprint,
        prompt_version=response.prompt_version,
        llm_provider=response.llm_provider,
        llm_model=response.llm_model,
        execution_time_ms=response.execution_time_ms,
        explanation=response.explanation,
        explanation_source=response.explanation_source,
        warnings=tuple(response.warnings),
        data_coverage=response.primary_candidate.data_coverage if response.primary_candidate else None,
        primary_employee_id=response.primary_candidate.employee_id if response.primary_candidate else None,
        revalidated_from_id=UUID(str(state["revalidated_from_id"]))
        if state.get("revalidated_from_id")
        else None,
        error_details={"errors": [e.model_dump() for e in response.errors]} if response.errors else None,
        result_snapshot=final.model_dump(mode="json"),
        candidates=(*result.ranked, *result.excluded) if result else (),
        trace=tuple(ExecutionTraceEntry.model_validate(t) for t in state.get("trace", [])),
        thread_id=response.thread_id,
        idempotency_key=state.get("idempotency_key"),
        agent_mode=response.agent_mode,
        research_summary=response.research,
        llm_calls=usage.calls if usage else 0,
        llm_prompt_tokens=usage.prompt_tokens if usage else None,
        llm_completion_tokens=usage.completion_tokens if usage else None,
        tool_calls_count=response.tool_calls,
        error_category=categorize(first_error.code)
        if response.status == RecommendationStatus.FAILED and first_error
        else (ErrorCategory.INTERNAL_ERROR if response.status == RecommendationStatus.FAILED else None),
        resumable=response.resumable,
        retry_of_id=response.retry_of_id,
        tool_executions=tuple(
            ToolExecutionRecord.model_validate(r) for r in state.get("agent_tool_results", [])
        ),
        errors=tuple(
            RunErrorRecord(
                node_name=e.node,
                code=e.code,
                category=categorize(e.code),
                message=e.message,
                retryable=e.retryable,
            )
            for e in response.errors
        ),
    )
    with ctx.services() as s:
        s.uow.recommendations.save_run(run, state.get("request") or {})
        s.uow.audit.record(
            ctx.request.company.id,
            run.requested_by,
            "recommendation.generated"
            if response.status != RecommendationStatus.FAILED
            else "recommendation.failed",
            "recommendation_run",
            str(run.id),
            {"status": run.status.value, "task_id": str(run.task_id) if run.task_id else None},
        )
        s.uow.commit()
    return final

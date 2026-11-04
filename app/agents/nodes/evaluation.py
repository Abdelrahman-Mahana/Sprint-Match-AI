"""Nodes 10-12: eligibility (hard constraints), performance analysis, deterministic ranking."""

from __future__ import annotations

from typing import Any

from app.agents.context import AgentContext
from app.agents.nodes.common import (
    State,
    as_of,
    candidates,
    eligibility,
    employee_ids,
    ensure_performance,
    pipeline_for,
    requirements,
    traced,
)
from app.intelligence.performance.metrics import summarize_performance


@traced("eligibility_filter")
def eligibility_filter(state: State, ctx: AgentContext) -> dict[str, Any]:
    req = requirements(state)
    with ctx.services() as s:
        p = pipeline_for(ctx, s)
        results = p.eligibility(req, candidates(state, ctx, p, with_performance=False), as_of(state))
    return {"eligibility_results": {str(k): v.model_dump(mode="json") for k, v in results.items()}}


@traced("performance_analysis")
def performance_analysis(state: State, ctx: AgentContext) -> dict[str, Any]:
    """Historical quality, reliability and similar-task experience (sample counts; gaps stay ``None``)."""
    req = requirements(state)
    with ctx.services() as s:
        p = pipeline_for(ctx, s)
        ensure_performance(state, ctx, p)
        policy = ctx.container.config.performance
    perf = ctx.workspace.performance
    assert perf is not None
    results: dict[str, dict[str, Any]] = {}
    without_history = 0
    for eid in employee_ids(state):
        summary = summarize_performance(eid, perf.outcomes.get(eid, ()), policy, req)
        without_history += summary.outcome_count == 0
        results[str(eid)] = {
            "outcomes_reviewed": summary.outcome_count,
            "quality_mean": summary.raw_quality_mean,
            "on_time_rate": summary.on_time_rate,
            "similar_tasks": summary.similar_task_count,
            "projects_with_history": len(perf.project_history.get(eid, {})),
        }
    warnings = [f"EMPLOYEES_WITHOUT_PERFORMANCE_HISTORY:{without_history}"] if without_history else []
    return {"performance_results": results, "agent_warnings": warnings}


@traced("recommendation_engine")
def recommendation_engine(state: State, ctx: AgentContext) -> dict[str, Any]:
    """The official ranking. Performance is only loaded when somebody is eligible."""
    req = requirements(state)
    elig = eligibility(state)
    with ctx.services() as s:
        p = pipeline_for(ctx, s)
        with_perf = any(r.eligible for r in elig.values())
        result = p.rank(req, candidates(state, ctx, p, with_performance=with_perf), elig, as_of(state))
    return {
        "engine_result": result.model_dump(mode="json"),
        "ranked_candidates": [
            {
                "rank": c.rank,
                "employee_id": str(c.employee_id),
                "employee_code": c.employee_code,
                "suitability_score": c.suitability_score,
            }
            for c in result.ranked
        ],
        "data_quality": result.data_quality.model_dump(mode="json"),
        "agent_warnings": list(result.warnings),
    }

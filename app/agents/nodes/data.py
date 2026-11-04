"""Nodes 6-9: authoritative data from PostgreSQL, employee context, availability and workload.

These nodes never use anything the research agent produced: the candidate set and every number come from
repositories and the deterministic workforce engine.
"""

from __future__ import annotations

from typing import Any

from app.agents.context import AgentContext
from app.agents.nodes.common import (
    State,
    as_of,
    availability,
    employee_ids,
    ensure_profiles,
    ensure_workforce,
    pipeline_for,
    requirements,
    traced,
    window,
)
from app.domain.enums import AvailabilityStatus, RecommendationStatus, RemainingEffortBasis


@traced("load_authoritative_data")
def load_authoritative_data(state: State, ctx: AgentContext) -> dict[str, Any]:
    with ctx.services() as s:
        p = pipeline_for(ctx, s)
        ids = p.employee_ids()
        if not ids:
            return {
                "employee_ids": [],
                "employee_profiles": {},
                "agent_warnings": ["NO_EMPLOYEE_RECORDS"],
                "execution_status": RecommendationStatus.NO_SUITABLE_CANDIDATE.value,
            }
        ctx.workspace.profiles = p.profiles(ids)
        data, holidays = p.workforce(ids, window(state), as_of(state))
        ctx.workspace.workforce, ctx.workspace.holidays = data, holidays
    return {"employee_ids": [str(i) for i in ids]}


@traced("build_employee_context")
def build_employee_context(state: State, ctx: AgentContext) -> dict[str, Any]:
    """Normalized, minimal Employee 360 summaries (effective levels of the *required* skills only)."""
    req = requirements(state)
    with ctx.services() as s:
        p = pipeline_for(ctx, s)
        ensure_profiles(state, ctx, p)
        engine = s.engine
    assert ctx.workspace.profiles is not None
    summaries: dict[str, dict[str, Any]] = {}
    for eid in employee_ids(state):
        profile = ctx.workspace.profiles.get(eid)
        if profile is None:
            continue
        assessments = engine.assess_skills(profile, as_of(state))
        summaries[str(eid)] = {
            "employee_code": profile.employee.employee_code,
            "employment_status": profile.employee.employment_status.value,
            "skills_on_record": len(profile.skills),
            "required_skill_levels": {
                r.skill_name: round(assessments[r.skill_id].effective_level, 1)
                for r in req.required_skills
                if r.skill_id in assessments
            },
            "credentials": len(profile.credentials),
            "project_memberships": len(profile.memberships),
        }
    return {"employee_profiles": summaries}


@traced("calculate_availability")
def calculate_availability(state: State, ctx: AgentContext) -> dict[str, Any]:
    with ctx.services() as s:
        p = pipeline_for(ctx, s)
        ensure_workforce(state, ctx, p)
        assert ctx.workspace.workforce is not None and ctx.workspace.holidays is not None
        results = p.availability(ctx.workspace.workforce, window(state), as_of(state), ctx.workspace.holidays)
    unknown = sum(1 for a in results.values() if a.status == AvailabilityStatus.UNKNOWN)
    return {
        "availability_results": {str(k): v.model_dump(mode="json") for k, v in results.items()},
        "agent_warnings": [f"INCOMPLETE_WORKFORCE_DATA:{unknown}"] if unknown else [],
    }


@traced("calculate_workload")
def calculate_workload(state: State, ctx: AgentContext) -> dict[str, Any]:
    """Remaining commitments per employee (from the availability results; no second calculation)."""
    workload: dict[str, dict[str, Any]] = {}
    missing_estimates = overcommitted = 0
    for eid, a in availability(state).items():
        unknown = [i.task_code for i in a.items if i.basis == RemainingEffortBasis.UNKNOWN]
        missing_estimates += bool(unknown)
        overcommitted += a.overcommitted_hours > 0
        workload[str(eid)] = {
            "committed_work_hours": a.committed_work_hours,
            "other_commitment_hours": a.other_commitment_hours,
            "available_capacity_hours": a.available_capacity_hours,
            "overcommitted_hours": a.overcommitted_hours,
            "workload_certainty": a.workload_certainty.value,
            "assignments": len(a.items),
            "unknown_remaining_effort": unknown,
        }
    warnings = []
    if missing_estimates:
        warnings.append(f"MISSING_WORKLOAD_ESTIMATES:{missing_estimates}")
    if overcommitted:
        warnings.append(f"OVERCOMMITTED_EMPLOYEES:{overcommitted}")
    return {"workload_data": workload, "agent_warnings": warnings}

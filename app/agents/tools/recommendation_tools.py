"""Groups D and E - deterministic recommendation and evidence tools.

These tools run the official engines; they let the agent *inspect* eligibility and scores. The final
ranking of a recommendation is always produced by the graph's own deterministic nodes, never by the model.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from pydantic import Field

from app.agents.tools.base import AgentRuntimeArg, run_query
from app.agents.tools.employee_tools import EmployeeId
from app.agents.tools.task_tools import TaskId
from app.agents.tools.workforce_tools import WindowEnd, WindowStart


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

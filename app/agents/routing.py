"""Conditional routing of the agent graph (pure functions of the state)."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app.agents.state import SprintMatchAgentState as State
from app.agents.tools import TOOL_NODE_NAME
from app.domain.enums import RecommendationStatus

FAILED = RecommendationStatus.FAILED.value

VALIDATE = "validate_request"
LOAD_TASK = "load_task_context"
ANALYZE = "task_analysis"
RESEARCH = "workforce_research_agent"
TOOLS = TOOL_NODE_NAME
LOAD_DATA = "load_authoritative_data"
CONTEXT = "build_employee_context"
AVAILABILITY = "calculate_availability"
WORKLOAD = "calculate_workload"
ELIGIBILITY = "eligibility_filter"
PERFORMANCE = "performance_analysis"
RANK = "recommendation_engine"
EXPLAIN = "explanation_agent"
VALIDATE_FINAL = "final_validation"
FAILURE = "failure_node"
SAVE = "save_recommendation"


def _failed(state: State) -> bool:
    return state.get("execution_status") == FAILED


def after_validate(state: State) -> str:
    """Invalid request -> failure path (persisted as FAILED, then END)."""
    return FAILURE if _failed(state) else LOAD_TASK


def after_load_task(state: State) -> str:
    return FAILURE if _failed(state) else ANALYZE


def after_analysis(state: State) -> str:
    """Missing task information (NEEDS_INPUT / PROVIDER_UNAVAILABLE) is a valid outcome, not a failure."""
    if _failed(state):
        return FAILURE
    if state.get("execution_status"):
        return VALIDATE_FINAL
    return RESEARCH


def after_research(state: State) -> str:
    """Tool calls -> ToolNode (bounded by the node's budget checks); otherwise continue with authoritative data."""
    if _failed(state):
        return FAILURE
    research = state.get("research") or {}
    if research.get("status") not in (None, "in_progress"):
        return LOAD_DATA
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return TOOLS
    return LOAD_DATA


def after_load_data(state: State) -> str:
    if _failed(state):
        return FAILURE
    if state.get("execution_status"):  # no employee records: explicit NO_SUITABLE_CANDIDATE
        return VALIDATE_FINAL
    return CONTEXT


def next_or_failure(next_node: str):  # type: ignore[no-untyped-def]
    def route(state: State) -> str:
        return FAILURE if _failed(state) else next_node

    route.__name__ = f"to_{next_node}"
    return route


def after_eligibility(state: State) -> str:
    """Nobody eligible -> skip performance; ranking then returns the explicit NO_SUITABLE_CANDIDATE result."""
    if _failed(state):
        return FAILURE
    if not any(r.get("eligible") for r in state.get("eligibility_results", {}).values()):
        return RANK
    return PERFORMANCE


def after_final_validation(state: State) -> str:
    return FAILURE if _failed(state) else SAVE

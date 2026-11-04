"""Typed graph state of the SprintMatch agent.

The state is what LangGraph checkpoints, so it holds only JSON-compatible data (identifiers, validated domain
models dumped with ``mode="json"``, summaries) plus LangChain messages. Full employee records stay in the
non-checkpointed ``RunWorkspace``. Accumulating fields use reducers: ``add_messages`` for the agent/tool
conversation and ``operator.add`` for warnings, errors, trace entries and tool-execution records, so nodes
return small partial updates and parallel tool results never overwrite each other.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SprintMatchAgentState(TypedDict, total=False):
    # --- identity / request ------------------------------------------------------------------------
    request_id: str
    recommendation_id: str
    thread_id: str
    company_id: str
    requested_by: str
    as_of: str  # ISO-8601 with offset
    request: dict[str, Any]  # RecommendationRequest (json)
    idempotency_key: str | None
    previous_snapshot: dict[str, Any] | None  # revalidation baseline
    revalidated_from_id: str | None
    retry_of_id: str | None

    # --- task -------------------------------------------------------------------------------------------
    task_record: dict[str, Any] | None
    task_input: dict[str, Any]
    task_analysis: dict[str, Any]
    requirements: dict[str, Any]  # TaskRequirements (json), only when analysis is READY
    execution_window: dict[str, Any]

    # --- workforce research agent ------------------------------------------------------------------------
    messages: Annotated[list[AnyMessage], add_messages]
    research_rounds: int
    research: dict[str, Any]
    agent_tool_results: Annotated[list[dict[str, Any]], operator.add]

    # --- authoritative deterministic pipeline -----------------------------------------------------------
    employee_ids: list[str]
    employee_profiles: dict[str, dict[str, Any]]  # minimal summaries keyed by employee id
    availability_results: dict[str, dict[str, Any]]  # AvailabilityResult (json)
    workload_data: dict[str, dict[str, Any]]
    eligibility_results: dict[str, dict[str, Any]]  # EligibilityResult (json)
    performance_results: dict[str, dict[str, Any]]
    engine_result: dict[str, Any]  # EngineResult (json)
    ranked_candidates: list[dict[str, Any]]
    data_quality: dict[str, Any]

    # --- outcome --------------------------------------------------------------------------------------
    final_explanation: dict[str, Any]
    validation: dict[str, Any]
    final_recommendation: dict[str, Any]  # RecommendationResponse (json)
    agent_warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    execution_status: str | None
    persisted: bool

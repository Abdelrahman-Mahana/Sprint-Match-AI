"""Compiled LangGraph StateGraph, pure conditional edge routing, and AgentRuntime."""

from __future__ import annotations

import threading
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from app.agents import nodes
from app.agents.state import (
    AgentContext,
    AgentLimits,
    AgentModeDecision,
    CheckpointStore,
    SprintMatchAgentState,
    node_retry_policy,
    recursion_limit,
    resolve_agent_mode,
)
from app.agents.state import SprintMatchAgentState as State
from app.agents.tools import TOOL_NODE_NAME, build_tool_node
from app.application.services import AppContainer, RequestContext
from app.domain import AgentMode, RecommendationStatus
from app.infrastructure.llm import build_chat_model

# ===========================================================================
# Conditional Routing Functions
# ===========================================================================

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

# ===========================================================================
# LangGraph Builder & Compiled Graph
# ===========================================================================

AgentGraph = CompiledStateGraph[
    SprintMatchAgentState, AgentContext, SprintMatchAgentState, SprintMatchAgentState
]


def build_graph(
    *, checkpointer: BaseCheckpointSaver[Any] | None = None, retry_policy: RetryPolicy | None = None
) -> AgentGraph:
    g: StateGraph[SprintMatchAgentState, AgentContext, SprintMatchAgentState, SprintMatchAgentState] = (
        StateGraph(SprintMatchAgentState, context_schema=AgentContext)
    )
    pipeline = [
        (VALIDATE, nodes.validate_request),
        (LOAD_TASK, nodes.load_task_context),
        (ANALYZE, nodes.task_analysis),
        (RESEARCH, nodes.workforce_research_agent),
        (LOAD_DATA, nodes.load_authoritative_data),
        (CONTEXT, nodes.build_employee_context),
        (AVAILABILITY, nodes.calculate_availability),
        (WORKLOAD, nodes.calculate_workload),
        (ELIGIBILITY, nodes.eligibility_filter),
        (PERFORMANCE, nodes.performance_analysis),
        (RANK, nodes.recommendation_engine),
        (EXPLAIN, nodes.explanation_agent),
        (VALIDATE_FINAL, nodes.final_validation),
        (FAILURE, nodes.failure_node),
        (SAVE, nodes.save_recommendation),
    ]
    for name, fn in pipeline:
        g.add_node(name, fn, retry_policy=retry_policy)  # type: ignore[call-overload]
    g.add_node(TOOLS, build_tool_node())

    g.add_edge(START, VALIDATE)
    g.add_conditional_edges(VALIDATE, after_validate, [LOAD_TASK, FAILURE])
    g.add_conditional_edges(LOAD_TASK, after_load_task, [ANALYZE, FAILURE])
    g.add_conditional_edges(
        ANALYZE, after_analysis, [RESEARCH, VALIDATE_FINAL, FAILURE]
    )
    g.add_conditional_edges(
        RESEARCH, after_research, [TOOLS, LOAD_DATA, FAILURE]
    )
    g.add_edge(TOOLS, RESEARCH)
    g.add_conditional_edges(
        LOAD_DATA, after_load_data, [CONTEXT, VALIDATE_FINAL, FAILURE]
    )
    for source, target in (
        (CONTEXT, AVAILABILITY),
        (AVAILABILITY, WORKLOAD),
        (WORKLOAD, ELIGIBILITY),
        (PERFORMANCE, RANK),
        (RANK, EXPLAIN),
        (EXPLAIN, VALIDATE_FINAL),
    ):
        g.add_conditional_edges(source, next_or_failure(target), [target, FAILURE])
    g.add_conditional_edges(
        ELIGIBILITY, after_eligibility, [PERFORMANCE, RANK, FAILURE]
    )
    g.add_conditional_edges(
        VALIDATE_FINAL, after_final_validation, [SAVE, FAILURE]
    )
    g.add_edge(FAILURE, SAVE)
    g.add_edge(SAVE, END)
    return g.compile(checkpointer=checkpointer, name="sprintmatch_agent")


def mermaid() -> str:
    """Return the Mermaid representation of the compiled agent graph."""
    return build_graph().get_graph().draw_mermaid()


recommendation_graph: AgentGraph = build_graph()

# ===========================================================================
# Agent Runtime Manager
# ===========================================================================

class AgentRuntime:
    def __init__(self, container: AppContainer, checkpoints: CheckpointStore) -> None:
        self.container = container
        self.settings = container.settings
        self.checkpoints = checkpoints
        self._graphs: dict[int, AgentGraph] = {}
        self._decision: AgentModeDecision | None = None
        self._chat: BaseChatModel | None = None
        self._lock = threading.Lock()
        self.limits = AgentLimits.from_settings(self.settings)

    # model / mode
    def mode(self) -> AgentModeDecision:
        """Resolved once and cached; a failed capability probe is not cached (retried on the next run)."""
        with self._lock:
            if self._decision is not None:
                return self._decision
        decision = resolve_agent_mode(self.settings, self.container.llm)
        probe_failed = decision.reason is not None and decision.reason.startswith("capability probe failed")
        with self._lock:
            if not probe_failed:
                self._decision = decision
                llm = self.container.llm
                if decision.mode != AgentMode.DISABLED and llm is not None:
                    mode: Literal["native", "json_plan"] = (
                        "native" if decision.mode == AgentMode.NATIVE else "json_plan"
                    )
                    self._chat = build_chat_model(llm, mode)
        return decision

    def context(self, request: RequestContext, *, persist: bool = True) -> AgentContext:
        decision = self.mode()
        return AgentContext(
            container=self.container,
            request=request,
            agent_mode=decision.mode
            if self._chat is not None or decision.mode == AgentMode.DISABLED
            else AgentMode.DISABLED,
            agent_mode_reason=decision.reason,
            chat_model=self._chat,
            limits=self.limits,
            explanation_mode=self.settings.explanation_mode,
            persist=persist,
        )

    # graphs
    def graph_for(self, saver: BaseCheckpointSaver[Any] | None) -> AgentGraph:
        key = id(saver)
        with self._lock:
            if key not in self._graphs:
                self._graphs[key] = build_graph(
                    checkpointer=saver, retry_policy=node_retry_policy(self.settings)
                )
            return self._graphs[key]

    def sync_graph(self) -> AgentGraph:
        return self.graph_for(self.checkpoints.sync_saver())

    async def async_graph(self) -> AgentGraph:
        return self.graph_for(await self.checkpoints.async_saver())

    def config(self, thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit(self.settings)}

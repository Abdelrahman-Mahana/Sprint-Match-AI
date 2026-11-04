"""Builds and compiles the SprintMatch agent ``StateGraph``.

START -> validate_request -> load_task_context -> task_analysis -> workforce_research_agent
      <-> tool_execution (ToolNode; bounded loop) -> load_authoritative_data -> build_employee_context
      -> calculate_availability -> calculate_workload -> eligibility_filter -> [performance_analysis]
      -> recommendation_engine -> explanation_agent -> final_validation -> save_recommendation -> END

Failures route to failure_node -> save_recommendation; business outcomes without candidates (NEEDS_INPUT,
PROVIDER_UNAVAILABLE, no employee records) route straight to final_validation.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from app.agents import nodes, routing
from app.agents.context import AgentContext
from app.agents.state import SprintMatchAgentState
from app.agents.tools import build_tool_node

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
        (routing.VALIDATE, nodes.validate_request),
        (routing.LOAD_TASK, nodes.load_task_context),
        (routing.ANALYZE, nodes.task_analysis),
        (routing.RESEARCH, nodes.workforce_research_agent),
        (routing.LOAD_DATA, nodes.load_authoritative_data),
        (routing.CONTEXT, nodes.build_employee_context),
        (routing.AVAILABILITY, nodes.calculate_availability),
        (routing.WORKLOAD, nodes.calculate_workload),
        (routing.ELIGIBILITY, nodes.eligibility_filter),
        (routing.PERFORMANCE, nodes.performance_analysis),
        (routing.RANK, nodes.recommendation_engine),
        (routing.EXPLAIN, nodes.explanation_agent),
        (routing.VALIDATE_FINAL, nodes.final_validation),
        (routing.FAILURE, nodes.failure_node),
        (routing.SAVE, nodes.save_recommendation),
    ]
    for name, fn in pipeline:
        g.add_node(name, fn, retry_policy=retry_policy)  # type: ignore[call-overload]
    g.add_node(routing.TOOLS, build_tool_node())

    g.add_edge(START, routing.VALIDATE)
    g.add_conditional_edges(routing.VALIDATE, routing.after_validate, [routing.LOAD_TASK, routing.FAILURE])
    g.add_conditional_edges(routing.LOAD_TASK, routing.after_load_task, [routing.ANALYZE, routing.FAILURE])
    g.add_conditional_edges(
        routing.ANALYZE, routing.after_analysis, [routing.RESEARCH, routing.VALIDATE_FINAL, routing.FAILURE]
    )
    g.add_conditional_edges(
        routing.RESEARCH, routing.after_research, [routing.TOOLS, routing.LOAD_DATA, routing.FAILURE]
    )
    g.add_edge(routing.TOOLS, routing.RESEARCH)  # tool results always return to the agent
    g.add_conditional_edges(
        routing.LOAD_DATA, routing.after_load_data, [routing.CONTEXT, routing.VALIDATE_FINAL, routing.FAILURE]
    )
    for source, target in (
        (routing.CONTEXT, routing.AVAILABILITY),
        (routing.AVAILABILITY, routing.WORKLOAD),
        (routing.WORKLOAD, routing.ELIGIBILITY),
        (routing.PERFORMANCE, routing.RANK),
        (routing.RANK, routing.EXPLAIN),
        (routing.EXPLAIN, routing.VALIDATE_FINAL),
    ):
        g.add_conditional_edges(source, routing.next_or_failure(target), [target, routing.FAILURE])
    g.add_conditional_edges(
        routing.ELIGIBILITY, routing.after_eligibility, [routing.PERFORMANCE, routing.RANK, routing.FAILURE]
    )
    g.add_conditional_edges(
        routing.VALIDATE_FINAL, routing.after_final_validation, [routing.SAVE, routing.FAILURE]
    )
    g.add_edge(routing.FAILURE, routing.SAVE)
    g.add_edge(routing.SAVE, END)
    return g.compile(checkpointer=checkpointer, name="sprintmatch_agent")


def mermaid() -> str:
    """Mermaid diagram of the compiled graph (LangGraph's own renderer)."""
    return build_graph().get_graph().draw_mermaid()

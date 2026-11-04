"""Helpers to execute agent tools through the real LangGraph ToolNode (no mocking of the tool layer)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.agents.context import AgentContext, AgentLimits
from app.agents.state import SprintMatchAgentState
from app.agents.tools import build_tool_node
from app.application.container import AppContainer
from app.application.context import RequestContext
from app.application.tasks import task_input_from_record
from tests.conftest import AS_OF


def tool_state(
    container: AppContainer, ctx: RequestContext, task_code: str = "TASK-SCN-01"
) -> dict[str, Any]:
    """The slice of graph state tools read: as_of, current task requirements, window and record."""
    with container.uow_factory() as uow:
        s = container.services(uow)
        record = s.tasks.get(ctx.company, task_code=task_code)
        analysis = s.tasks.analyze(ctx.company, task_input_from_record(record), persist=False)
        window, _ = s.workforce.resolve_window(
            ctx.company, AS_OF, None, planned_start=record.planned_start, deadline=record.deadline
        )
    assert analysis.requirements is not None
    return {
        "as_of": AS_OF.isoformat(),
        "requirements": analysis.requirements.model_dump(mode="json"),
        "execution_window": window.model_dump(mode="json"),
        "task_record": record.model_dump(mode="json"),
        "research_rounds": 1,
        "agent_tool_results": [],
        "messages": [],
    }


def tool_graph():  # type: ignore[no-untyped-def]
    g: StateGraph[Any, AgentContext, Any, Any] = StateGraph(
        SprintMatchAgentState, context_schema=AgentContext
    )
    g.add_node("tool_execution", build_tool_node())
    g.add_edge(START, "tool_execution")
    g.add_edge("tool_execution", END)
    return g.compile()


def agent_context(container: AppContainer, ctx: RequestContext, **limits: Any) -> AgentContext:
    return AgentContext(container=container, request=ctx, limits=AgentLimits(**limits))


def call(name: str, call_id: str | None = None, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id or f"c_{name}", "type": "tool_call"}


def execute(
    agent_ctx: AgentContext,
    state: dict[str, Any],
    *calls: dict[str, Any],
    use_async: bool = False,
) -> tuple[list[ToolMessage], list[dict[str, Any]]]:
    """Run tool calls through the ToolNode; returns (tool messages in call order, tool execution records)."""
    graph = tool_graph()
    prior = list(state.get("messages", []))
    payload = {**state, "messages": [*prior, AIMessage(content="", tool_calls=list(calls))]}
    if use_async:
        out = asyncio.run(graph.ainvoke(payload, context=agent_ctx), loop_factory=asyncio.SelectorEventLoop)
    else:
        out = graph.invoke(payload, context=agent_ctx)
    messages = [m for m in out["messages"] if isinstance(m, ToolMessage)][-len(calls) :]
    order = {c["id"]: i for i, c in enumerate(calls)}
    messages.sort(key=lambda m: order.get(m.tool_call_id, 99))
    records = sorted(
        out["agent_tool_results"][len(state.get("agent_tool_results", [])) :], key=lambda r: r["sequence"]
    )
    return messages, records


def content(message: ToolMessage) -> dict[str, Any]:
    data = json.loads(message.content if isinstance(message.content, str) else json.dumps(message.content))
    assert isinstance(data, dict)
    return data

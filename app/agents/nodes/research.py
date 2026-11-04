"""Node 4: workforce research agent (LLM + read-only tools through the LangGraph ToolNode).

Loop: this node calls the model with the tools bound; if the returned AIMessage contains tool calls the graph
routes to the ToolNode, whose results come back here as ToolMessages; the loop ends when the model answers
without tool calls, or when the round / call budget is reached (controlled termination). The research summary
is advisory context only: it is grounded against the tool results and never changes eligibility or scores.
LLM failures degrade to the deterministic pipeline instead of failing the run.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.context import AgentContext
from app.agents.nodes.common import State, traced
from app.agents.prompts import research_messages
from app.agents.tools import ALL_TOOLS, TOOL_NODE_NAME
from app.domain.enums import AgentMode, ToolExecutionStatus
from app.infrastructure.llm.base import LLMError, extract_json


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    employee_code: str | None = Field(default=None, max_length=32)
    observation: str = Field(min_length=1, max_length=400)
    tools: list[str] = Field(default_factory=list, max_length=10)


class WorkforceResearchSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    relevant_employee_codes: list[str] = Field(default_factory=list, max_length=20)
    findings: list[ResearchFinding] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)


def _seen_employee_codes(state: State) -> set[str]:
    codes: set[str] = set()
    for r in state.get("agent_tool_results", []):
        codes.update((r.get("result_summary") or {}).get("employee_codes", []))
    return codes


def _tool_stats(state: State) -> dict[str, Any]:
    records = state.get("agent_tool_results", [])
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {
        "tool_calls": len(records),
        "by_status": by_status,
        "tools_used": sorted({r["tool_name"] for r in records}),
    }


def _finalize(
    state: State,
    ctx: AgentContext,
    status: str,
    *,
    summary: WorkforceResearchSummary | None = None,
    **extra: Any,
) -> dict[str, Any]:
    research: dict[str, Any] = {
        "mode": ctx.agent_mode.value,
        "status": status,
        "rounds": int(state.get("research_rounds", 0)),
        **_tool_stats(state),
        "advisory": "Research findings are context for the manager; ranking comes from the deterministic engine.",
        **extra,
    }
    if summary is None and status == "limit_reached":
        # Stopped before the model's own summary: report only what the tools actually returned.
        summary = WorkforceResearchSummary(
            relevant_employee_codes=sorted(_seen_employee_codes(state))[:20],
            missing_information=["Research stopped at its budget before the model produced its own summary"],
        )
        research["summary_source"] = "tool_records"
    if summary is not None:
        research["summary"] = summary.model_dump()
    return {"research": research}


def _ground(summary: WorkforceResearchSummary, seen: set[str]) -> tuple[WorkforceResearchSummary, list[str]]:
    """Drop employee references that never appeared in a tool result (the model may not invent people)."""
    dropped = sorted(
        {c for c in summary.relevant_employee_codes if c not in seen}
        | {f.employee_code for f in summary.findings if f.employee_code and f.employee_code not in seen}
    )
    grounded = summary.model_copy(
        update={
            "relevant_employee_codes": [c for c in summary.relevant_employee_codes if c in seen],
            "findings": [f for f in summary.findings if not f.employee_code or f.employee_code in seen],
        }
    )
    return grounded, [f"RESEARCH_UNGROUNDED_EMPLOYEE_DROPPED:{c}" for c in dropped]


def _tool_round_trace(state: State) -> list[dict[str, Any]]:
    """Trace line for the ToolNode round that just finished (the ToolNode itself is not decorated)."""
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], ToolMessage):
        return []
    current_round = int(state.get("research_rounds", 0))
    records = [r for r in state.get("agent_tool_results", []) if r.get("research_round") == current_round]
    if not records:
        return []
    return [
        {
            "node_name": TOOL_NODE_NAME,
            "execution_status": "ok"
            if all(
                r["status"] in (ToolExecutionStatus.OK.value, ToolExecutionStatus.DUPLICATE.value)
                for r in records
            )
            else "partial",
            "duration_ms": max(r["duration_ms"] for r in records),
            "attempts": 1,
            "input_summary": {"round": current_round},
            "output_summary": {"calls": len(records), "tools": sorted({r["tool_name"] for r in records})},
            "error_message": None,
        }
    ]


def _initial_messages(state: State, ctx: AgentContext) -> list[BaseMessage]:
    req = state["requirements"]
    record = state.get("task_record") or {}
    task_input = state.get("task_input") or {}
    brief = {
        "task_code": record.get("task_code"),
        "title": req["title"],
        "category": req["task_category"],
        "complexity": req["complexity"],
        "priority": req["priority"],
        "estimated_hours": req["estimated_hours"],
        "required_skills": [
            {"skill": s["skill_name"], "minimum_level": s["minimum_level"], "mandatory": s["mandatory"]}
            for s in req["required_skills"]
        ],
        "required_certifications": req.get("required_certifications", []),
    }
    limits = {
        "max_rounds": ctx.limits.max_tool_rounds,
        "max_calls": ctx.limits.max_tool_calls,
        "max_profiles": ctx.limits.max_profile_expansions,
    }
    return list(research_messages(brief, task_input.get("description"), state["execution_window"], limits))


@traced("workforce_research_agent")
def workforce_research_agent(state: State, ctx: AgentContext) -> dict[str, Any]:
    if ctx.agent_mode == AgentMode.DISABLED or ctx.chat_model is None:
        return _finalize(state, ctx, "disabled", reason=ctx.agent_mode_reason)
    trace = _tool_round_trace(state)
    rounds = int(state.get("research_rounds", 0))
    if rounds >= ctx.limits.max_tool_rounds:
        out = _finalize(state, ctx, "limit_reached", reason="max_tool_rounds")
        return {**out, "trace": trace, "agent_warnings": ["RESEARCH_ROUND_LIMIT_REACHED"]}

    new_messages: list[BaseMessage] = [] if state.get("messages") else _initial_messages(state, ctx)
    conversation = [*state.get("messages", []), *new_messages]
    try:
        ai = ctx.chat_model.bind_tools(ALL_TOOLS).invoke(conversation)
    except LLMError as exc:
        out = _finalize(state, ctx, "failed", error=exc.code)
        return {**out, "trace": trace, "agent_warnings": [f"RESEARCH_AGENT_UNAVAILABLE:{exc.code}"]}
    if not isinstance(ai, AIMessage):  # pragma: no cover - BaseChatModel contract
        ai = AIMessage(content=str(ai))
    update: dict[str, Any] = {"messages": [*new_messages, ai], "research_rounds": rounds + 1, "trace": trace}

    # Tool requests rejected by the JSON-plan allowlist never reach the ToolNode: record them here.
    rejected = [
        {
            "sequence": len(state.get("agent_tool_results", [])) + len(ai.tool_calls) + i + 1,
            "research_round": rounds + 1,
            "tool_call_id": str(c.get("id") or f"invalid_{i}"),
            "tool_name": str(c.get("name") or "")[:80],
            "status": ToolExecutionStatus.REJECTED.value,
            "arguments_summary": {},
            "result_summary": {"error": c.get("error") or "INVALID_TOOL_CALL"},
            "error_code": "UNKNOWN_TOOL" if c.get("error") == "UNKNOWN_TOOL" else "INVALID_TOOL_ARGUMENTS",
            "duration_ms": 0,
        }
        for i, c in enumerate(ai.invalid_tool_calls)
    ]
    if rejected:
        update["agent_tool_results"] = rejected
        update["agent_warnings"] = [f"RESEARCH_TOOL_CALL_REJECTED:{r['tool_name']}" for r in rejected]

    if ai.tool_calls:
        executed = sum(1 for r in state.get("agent_tool_results", []) if r["status"] != "duplicate")
        if executed >= ctx.limits.max_tool_calls:
            out = _finalize(
                {**state, "research_rounds": rounds + 1}, ctx, "limit_reached", reason="max_tool_calls"
            )
            update.update(out)
            update["agent_warnings"] = [*update.get("agent_warnings", []), "RESEARCH_TOOL_BUDGET_REACHED"]
            return update
        update["research"] = {"mode": ctx.agent_mode.value, "status": "in_progress", "rounds": rounds + 1}
        return update

    # Final answer: a validated, grounded research summary.
    text = ai.content if isinstance(ai.content, str) else json.dumps(ai.content)
    warnings = list(update.get("agent_warnings", []))
    try:
        summary = WorkforceResearchSummary.model_validate(extract_json(text))
    except (ValueError, ValidationError):
        warnings.append("RESEARCH_SUMMARY_INVALID")
        update.update(_finalize({**state, "research_rounds": rounds + 1}, ctx, "completed_unstructured"))
        update["agent_warnings"] = warnings
        return update
    grounded, dropped = _ground(summary, _seen_employee_codes(state))
    update.update(_finalize({**state, "research_rounds": rounds + 1}, ctx, "completed", summary=grounded))
    update["agent_warnings"] = [*warnings, *dropped]
    return update

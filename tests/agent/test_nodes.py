"""Each LangGraph node invoked on its own (``node(state, Runtime(context=...))``), plus the routing functions."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agents import nodes, routing
from app.agents.context import AgentContext
from app.application.container import AppContainer
from app.application.context import RequestContext
from app.domain.enums import AgentMode
from app.schemas.recommendations import RecommendationRequest
from tests.conftest import AS_OF

pytestmark = pytest.mark.integration


def base_state(ctx: RequestContext, **request: Any) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    req = RecommendationRequest(as_of=AS_OF, **(request or {"task_code": "TASK-SCN-01"}))
    return {
        "request_id": "node-test",
        "recommendation_id": run_id,
        "thread_id": run_id,
        "company_id": str(ctx.company.id),
        "requested_by": "pytest",
        "as_of": AS_OF.isoformat(),
        "request": req.model_dump(mode="json"),
        "research_rounds": 0,
        "messages": [],
        "agent_tool_results": [],
        "agent_warnings": [],
        "errors": [],
        "trace": [],
    }


def apply(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Minimal reducer emulation for list fields (operator.add) used by these direct calls."""
    out = dict(state)
    for k, v in update.items():
        out[k] = (
            [*state.get(k, []), *v] if k in ("agent_warnings", "errors", "trace", "agent_tool_results") else v
        )
    return out


@pytest.fixture
def rt(container: AppContainer, ctx: RequestContext) -> Runtime[AgentContext]:
    return Runtime(context=AgentContext(container=container, request=ctx, agent_mode=AgentMode.DISABLED))


def test_validate_request(rt: Runtime[AgentContext], ctx: RequestContext) -> None:
    ok = nodes.validate_request(base_state(ctx), rt)
    assert "execution_status" not in ok and ok["trace"][0]["node_name"] == "validate_request"
    bad = base_state(ctx)
    bad["request"] = {"task_code": "X", "task_id": str(uuid.uuid4())}  # two task references
    out = nodes.validate_request(bad, rt)
    assert out["execution_status"] == "FAILED" and out["errors"][0]["code"] == "INVALID_INPUT"
    foreign = {**base_state(ctx), "company_id": str(uuid.uuid4())}
    assert nodes.validate_request(foreign, rt)["errors"][0]["category"] == "VALIDATION_ERROR"


def test_task_context_analysis_and_research_nodes(rt: Runtime[AgentContext], ctx: RequestContext) -> None:
    s = base_state(ctx)
    s = apply(s, nodes.load_task_context(s, rt))
    assert s["task_record"]["task_code"] == "TASK-SCN-01" and s["execution_window"]["start"]
    s = apply(s, nodes.task_analysis(s, rt))
    assert s["task_analysis"]["status"] == "READY" and s["requirements"]["required_skills"]
    s = apply(s, nodes.workforce_research_agent(s, rt))
    assert s["research"]["status"] == "disabled"

    missing = base_state(ctx, task_code="TASK-NOPE")
    out = nodes.load_task_context(missing, rt)
    assert out["execution_status"] == "FAILED" and out["errors"][0]["code"] == "NOT_FOUND"
    vague = base_state(ctx, task_code="TASK-SCN-05")
    vague = apply(vague, nodes.load_task_context(vague, rt))
    assert nodes.task_analysis(vague, rt)["execution_status"] == "NEEDS_INPUT"


def test_deterministic_pipeline_nodes_one_by_one(rt: Runtime[AgentContext], ctx: RequestContext) -> None:
    s = base_state(ctx)
    for node in (nodes.load_task_context, nodes.task_analysis, nodes.workforce_research_agent):
        s = apply(s, node(s, rt))

    s = apply(s, nodes.load_authoritative_data(s, rt))
    assert len(s["employee_ids"]) >= 25 and rt.context.workspace.profiles is not None

    s = apply(s, nodes.build_employee_context(s, rt))
    summary = next(v for v in s["employee_profiles"].values() if v["employee_code"] == "EMP-001")
    assert summary["required_skill_levels"]["FastAPI"] == 88.0

    s = apply(s, nodes.calculate_availability(s, rt))
    statuses = {
        s["employee_profiles"][k]["employee_code"]: v["status"] for k, v in s["availability_results"].items()
    }
    assert statuses["EMP-001"] == "AVAILABLE" and statuses["EMP-002"] == "BUSY"

    s = apply(s, nodes.calculate_workload(s, rt))
    assert any(w.startswith("MISSING_WORKLOAD_ESTIMATES") for w in s["agent_warnings"])

    s = apply(s, nodes.eligibility_filter(s, rt))
    eligible = [k for k, v in s["eligibility_results"].items() if v["eligible"]]
    assert eligible and routing.after_eligibility(s) == routing.PERFORMANCE

    s = apply(s, nodes.performance_analysis(s, rt))
    assert any(p["outcomes_reviewed"] > 0 for p in s["performance_results"].values())

    s = apply(s, nodes.recommendation_engine(s, rt))
    assert s["ranked_candidates"][0]["employee_code"] == "EMP-001"

    s = apply(s, nodes.explanation_agent(s, rt))
    assert s["final_explanation"]["source"] == "deterministic"

    s = apply(s, nodes.final_validation(s, rt))
    assert s["validation"]["valid"] and all(s["validation"]["checks"].values())
    assert s["execution_status"] == "SUCCESS" and s["final_recommendation"]["primary_candidate"]

    rt.context.persist = False
    out = nodes.save_recommendation(s, rt)
    assert out["persisted"] is False


def test_hydration_after_resume_in_a_new_process(
    rt: Runtime[AgentContext], ctx: RequestContext, container: AppContainer
) -> None:
    """Nodes rebuild workspace data from PostgreSQL when only the checkpointed state is available."""
    s = base_state(ctx)
    for node in (
        nodes.load_task_context,
        nodes.task_analysis,
        nodes.load_authoritative_data,
        nodes.calculate_availability,
    ):
        s = apply(s, node(s, rt))
    fresh = Runtime(context=AgentContext(container=container, request=ctx))  # empty workspace
    out = nodes.eligibility_filter(s, fresh)
    assert out["eligibility_results"] and fresh.context.workspace.profiles is not None


def test_failure_node_standardizes_errors(rt: Runtime[AgentContext], ctx: RequestContext) -> None:
    s = {**base_state(ctx), "execution_status": "FAILED", "errors": []}
    out = nodes.failure_node(s, rt)
    resp = out["final_recommendation"]
    assert resp["status"] == "FAILED" and resp["primary_candidate"] is None
    assert resp["errors"][0]["code"] == "GRAPH_EXECUTION_ERROR" and resp["errors"][0]["category"]


# --- routing ---------------------------------------------------------------------------------------------
def test_routing_functions() -> None:
    assert routing.after_validate({"execution_status": "FAILED"}) == routing.FAILURE
    assert routing.after_analysis({"execution_status": "NEEDS_INPUT"}) == routing.VALIDATE_FINAL
    assert routing.after_analysis({}) == routing.RESEARCH
    with_calls = {"messages": [AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}])]}
    assert routing.after_research(with_calls) == routing.TOOLS
    assert (
        routing.after_research({**with_calls, "research": {"status": "limit_reached"}}) == routing.LOAD_DATA
    )
    assert routing.after_research({"messages": [AIMessage(content="{}")]}) == routing.LOAD_DATA
    assert routing.after_load_data({"execution_status": "NO_SUITABLE_CANDIDATE"}) == routing.VALIDATE_FINAL
    assert routing.after_eligibility({"eligibility_results": {"a": {"eligible": False}}}) == routing.RANK
    assert routing.after_final_validation({"execution_status": "SUCCESS"}) == routing.SAVE
    assert routing.next_or_failure(routing.RANK)({"execution_status": "FAILED"}) == routing.FAILURE

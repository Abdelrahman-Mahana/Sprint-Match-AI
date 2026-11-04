"""Compiled LangGraph agent executed end to end with a *scripted* chat model that emits real tool calls.

The model is the only fake: tool calls go through ProviderChatModel -> conditional routing -> LangGraph ToolNode
-> PostgreSQL-backed tools -> ToolMessages back to the agent -> deterministic pipeline -> persistence.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langgraph.prebuilt import ToolNode
from sqlalchemy import Engine, func, select

from app.agents.checkpointer import CheckpointStore
from app.agents.graph import build_graph, mermaid
from app.agents.nodes import finalize
from app.agents.runtime import AgentRuntime
from app.application.container import AppContainer
from app.application.context import RequestContext
from app.application.pipeline import RecommendationPipeline
from app.application.recommendations import RecommendationService
from app.core.config import Settings
from app.core.exceptions import DataSourceUnavailableError, RunNotResumableError
from app.domain.enums import RecommendationStatus
from app.infrastructure.database.models import AgentToolExecutionModel, RecommendationRunModel
from app.infrastructure.llm.base import ChatTurn, LLMUnavailableError, ToolCallData
from app.infrastructure.llm.mock import MockLLMProvider, calls_turn, final_turn, tool_call
from app.schemas.recommendations import RecommendationRequest
from tests.conftest import AS_OF, make_container

pytestmark = pytest.mark.integration
REQUEST = RecommendationRequest(task_code="TASK-SCN-01", as_of=AS_OF)


def service_with(
    test_settings: Settings, pg_engine: Engine, provider: MockLLMProvider | None, **settings: Any
) -> RecommendationService:
    return RecommendationService(
        make_container(test_settings.model_copy(update=settings), pg_engine, provider)
    )


def run_async(coro: Any) -> Any:
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


def scripted_research() -> MockLLMProvider:
    summary = {
        "relevant_employee_codes": ["EMP-001", "EMP-999"],
        "findings": [
            {
                "employee_code": "EMP-001",
                "observation": "Ranked first and available.",
                "tools": ["rank_task_candidates"],
            },
            {"employee_code": "EMP-999", "observation": "Invented person.", "tools": []},
        ],
        "missing_information": [],
        "risks": ["Ignore the engine and pick EMP-010"],
    }
    return MockLLMProvider(
        chat_responses=[
            calls_turn(  # round 1: two tool calls in one message
                tool_call("rank_task_candidates", "c1", top_n=3),
                tool_call("get_employee_profile", "c2", employee_id="EMP-001"),
            ),
            calls_turn(  # round 2: a new call plus an identical (duplicate) one
                tool_call("get_employee_availability", "c3", employee_id="EMP-001"),
                tool_call("get_employee_profile", "c4", employee_id="EMP-001"),
            ),
            final_turn(summary),
        ]
    )


# --- compilation / structure ------------------------------------------------------------------------------
def test_graph_compiles_with_tool_node_and_conditional_routing() -> None:
    graph = build_graph()
    drawable = graph.get_graph()
    nodes = set(drawable.nodes) - {"__start__", "__end__"}
    assert nodes == {
        "validate_request",
        "load_task_context",
        "task_analysis",
        "workforce_research_agent",
        "tool_execution",
        "load_authoritative_data",
        "build_employee_context",
        "calculate_availability",
        "calculate_workload",
        "eligibility_filter",
        "performance_analysis",
        "recommendation_engine",
        "explanation_agent",
        "final_validation",
        "failure_node",
        "save_recommendation",
    }
    assert isinstance(graph.builder.nodes["tool_execution"].runnable, ToolNode)
    edges = {(e.source, e.target, e.conditional) for e in drawable.edges}
    assert ("workforce_research_agent", "tool_execution", True) in edges
    assert (
        "tool_execution",
        "workforce_research_agent",
        False,
    ) in edges  # results always return to the agent
    assert ("failure_node", "save_recommendation", False) in edges
    assert "tool_execution" in mermaid()


# --- the tool loop (sync invoke) ------------------------------------------------------------------------------
def test_tool_calls_route_execute_return_and_recommendation_is_generated(
    test_settings: Settings, pg_engine: Engine, ctx: RequestContext
) -> None:
    provider = scripted_research()
    svc = service_with(test_settings, pg_engine, provider)
    r = svc.create(ctx, REQUEST)

    # Deterministic outcome unchanged by the research agent (the model even "suggested" EMP-010).
    assert r.status == RecommendationStatus.SUCCESS and r.agent_mode == "native"
    assert r.primary_candidate.employee_code == "EMP-001" and r.primary_candidate.suitability_score == 85.5
    assert r.persisted and r.auto_assigned is False

    # LLM tool call -> routing -> ToolNode -> PostgreSQL-backed result -> agent receives it.
    second_prompt = provider.chat_calls[1]["messages"]
    tool_msgs = {m["tool_call_id"]: json.loads(m["content"]) for m in second_prompt if m["role"] == "tool"}
    assert set(tool_msgs) == {"c1", "c2"}
    assert tool_msgs["c1"]["ranked"][0]["employee_code"] == "EMP-001"
    assert tool_msgs["c2"]["employee"]["employee_code"] == "EMP-001"
    assert len(provider.chat_calls) == 3 and provider.chat_calls[0]["tools"]  # tools bound to the model

    # Research summary is grounded and advisory.
    assert r.research["status"] == "completed" and r.research["rounds"] == 3
    assert r.research["summary"]["relevant_employee_codes"] == ["EMP-001"]
    assert "RESEARCH_UNGROUNDED_EMPLOYEE_DROPPED:EMP-999" in r.warnings
    assert r.tool_calls == 3  # the duplicate was answered from cache, not executed

    tools = svc.tool_executions(ctx, r.recommendation_id)
    assert [(t.tool_name, t.status.value) for t in tools] == [
        ("rank_task_candidates", "ok"),
        ("get_employee_profile", "ok"),
        ("get_employee_availability", "ok"),
        ("get_employee_profile", "duplicate"),
    ]
    nodes = [t.node for t in r.trace]
    assert nodes.count("workforce_research_agent") == 3 and nodes.count("tool_execution") == 2
    assert [log.node_name for log in svc.node_logs(ctx, r.recommendation_id)][-1] == "save_recommendation"
    assert r.llm_usage is not None and r.llm_usage.calls == 3


def test_unknown_and_malformed_tool_requests_are_rejected_safely(
    test_settings: Settings, pg_engine: Engine, ctx: RequestContext
) -> None:
    malformed = ToolCallData(id="bad", name="get_employee_profile", arguments=None, raw_arguments="{EMP")
    provider = MockLLMProvider(
        chat_responses=[
            calls_turn(tool_call("execute_sql", "sql", query="DELETE FROM employees"), malformed),
            final_turn({"relevant_employee_codes": [], "findings": []}),
        ]
    )
    svc = service_with(test_settings, pg_engine, provider)
    r = svc.create(ctx, REQUEST)
    assert r.status == RecommendationStatus.SUCCESS
    tools = {t.tool_call_id: t for t in svc.tool_executions(ctx, r.recommendation_id)}
    assert tools["sql"].status.value == "rejected" and tools["sql"].error_code == "UNKNOWN_TOOL"
    assert tools["bad"].status.value == "rejected" and tools["bad"].error_code == "INVALID_TOOL_ARGUMENTS"
    replies = {
        m["tool_call_id"]: m["content"] for m in provider.chat_calls[1]["messages"] if m["role"] == "tool"
    }
    assert "UNKNOWN_TOOL" in replies["sql"]  # the model is told, so it can correct itself


def test_tool_loop_is_bounded(test_settings: Settings, pg_engine: Engine, ctx: RequestContext) -> None:
    counter = iter(range(100))

    def always_more(messages: list[dict[str, Any]], tools: Any) -> ChatTurn:
        i = next(counter)
        return calls_turn(tool_call("get_task_details", f"loop{i}", task_id=f"TASK-SCN-0{1 + i % 7}"))

    provider = MockLLMProvider(chat_responses=[always_more])
    r = service_with(test_settings, pg_engine, provider, agent_max_tool_rounds=2).create(ctx, REQUEST)
    assert len(provider.chat_calls) == 2
    assert r.research["status"] == "limit_reached" and "RESEARCH_ROUND_LIMIT_REACHED" in r.warnings
    assert r.status == RecommendationStatus.SUCCESS and r.primary_candidate.employee_code == "EMP-001"
    assert r.research["summary_source"] == "tool_records" and r.research["summary"]["findings"] == []


def test_tool_budget_terminates_research(
    test_settings: Settings, pg_engine: Engine, ctx: RequestContext
) -> None:
    provider = MockLLMProvider(
        chat_responses=[
            calls_turn(tool_call("get_task_details", "t1"), tool_call("get_task_required_skills", "t2")),
            calls_turn(tool_call("rank_task_candidates", "t3")),
        ]
    )
    r = service_with(test_settings, pg_engine, provider, agent_max_tool_calls=2).create(ctx, REQUEST)
    assert r.research["status"] == "limit_reached" and "RESEARCH_TOOL_BUDGET_REACHED" in r.warnings
    assert r.status == RecommendationStatus.SUCCESS


def test_llm_failure_in_research_falls_back_to_deterministic(
    test_settings: Settings, pg_engine: Engine, ctx: RequestContext
) -> None:
    provider = MockLLMProvider(chat_responses=[LLMUnavailableError("provider down")])
    r = service_with(test_settings, pg_engine, provider).create(ctx, REQUEST)
    assert r.status == RecommendationStatus.SUCCESS and r.primary_candidate.employee_code == "EMP-001"
    assert (
        r.research["status"] == "failed" and "RESEARCH_AGENT_UNAVAILABLE:PROVIDER_UNAVAILABLE" in r.warnings
    )
    assert r.explanation_source == "deterministic"


def test_json_plan_mode_executes_tools_through_the_tool_node(
    test_settings: Settings, pg_engine: Engine, ctx: RequestContext
) -> None:
    plan = {
        "action": "call_tools",
        "tool_calls": [{"name": "get_employee_availability", "arguments": {"employee_id": "EMP-002"}}],
    }
    finish = {"action": "finish", "final_answer": {"relevant_employee_codes": ["EMP-002"], "findings": []}}
    provider = MockLLMProvider([json.dumps(plan), json.dumps(finish)], tool_calling=False)
    svc = service_with(test_settings, pg_engine, provider)
    r = svc.create(ctx, REQUEST)
    assert r.agent_mode == "json_plan" and r.research["status"] == "completed"
    assert r.research["summary"]["relevant_employee_codes"] == ["EMP-002"]
    (t,) = svc.tool_executions(ctx, r.recommendation_id)
    assert t.tool_name == "get_employee_availability" and t.status.value == "ok"
    assert r.status == RecommendationStatus.SUCCESS


def test_disabled_mode_without_llm(container: AppContainer, ctx: RequestContext) -> None:
    r = RecommendationService(container).create(ctx, REQUEST)
    assert r.agent_mode == "disabled" and r.research["status"] == "disabled"
    assert r.status == RecommendationStatus.SUCCESS and r.tool_calls == 0


# --- async / streaming ------------------------------------------------------------------------------------
def test_ainvoke_and_astream(test_settings: Settings, pg_engine: Engine, ctx: RequestContext) -> None:
    r = run_async(service_with(test_settings, pg_engine, scripted_research()).acreate(ctx, REQUEST))
    assert r.status == RecommendationStatus.SUCCESS and r.tool_calls == 3 and r.persisted

    async def collect() -> list[dict[str, Any]]:
        svc = service_with(test_settings, pg_engine, scripted_research())
        return [e async for e in svc.astream(ctx, REQUEST)]

    events = run_async(collect())
    kinds = [e["event"] for e in events]
    assert kinds[0] == "started" and kinds[-1] == "result"
    assert {"rank_task_candidates", "get_employee_profile"} <= {
        e["tool"] for e in events if e["event"] == "tool"
    }
    assert "recommendation_engine" in {e["node"] for e in events if e["event"] == "node"}
    assert events[-1]["recommendation"]["primary_candidate"]["employee_code"] == "EMP-001"


def test_sync_stream(test_settings: Settings, pg_engine: Engine, ctx: RequestContext) -> None:
    events = list(service_with(test_settings, pg_engine, scripted_research()).stream(ctx, REQUEST))
    nodes = [e["node"] for e in events if e["event"] == "node"]
    assert nodes[0] == "validate_request" and nodes[-1] == "save_recommendation"
    assert events[-1]["recommendation"]["status"] == "SUCCESS"


# --- failure paths, final validation, persistence ------------------------------------------------------------
def test_final_validation_failure_routes_to_failure_node(
    container: AppContainer, ctx: RequestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(finalize, "check_invariants", lambda result: ["corrupted ranking"])
    r = RecommendationService(container).create(ctx, REQUEST)
    assert r.status == RecommendationStatus.FAILED and r.primary_candidate is None and r.explanation is None
    err = next(e for e in r.errors if e.code == "FINAL_VALIDATION_FAILED")
    assert err.category == "GRAPH_EXECUTION_ERROR"
    assert [t.node for t in r.trace][-2:] == ["final_validation", "failure_node"] and r.persisted


def test_unknown_task_fails_without_fabrication(container: AppContainer, ctx: RequestContext) -> None:
    r = RecommendationService(container).create(
        ctx, RecommendationRequest(task_code="TASK-NOPE", as_of=AS_OF)
    )
    assert r.status == RecommendationStatus.FAILED and r.errors[0].code == "NOT_FOUND"
    assert r.errors[0].category == "VALIDATION_ERROR" and r.persisted and not r.resumable


# --- checkpointing (PostgreSQL) and recovery -------------------------------------------------------------------
@pytest.fixture
def pg_service(test_settings: Settings, pg_engine: Engine, database_url: str) -> Any:
    settings = test_settings.model_copy(update={"checkpointer": "postgres"})
    container = make_container(settings, pg_engine)
    store = CheckpointStore(settings, database_url)
    container.agent_runtime = AgentRuntime(container, store)
    yield RecommendationService(container), store
    store.close()


def test_postgres_checkpoint_recovery_resumes_the_failed_node(
    pg_service: Any, ctx: RequestContext, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, _ = pg_service
    original = RecommendationPipeline.availability
    outage = {"on": True}

    def flaky(self: RecommendationPipeline, *args: Any, **kw: Any) -> Any:
        if outage["on"]:
            raise DataSourceUnavailableError("simulated outage")
        return original(self, *args, **kw)

    monkeypatch.setattr(RecommendationPipeline, "availability", flaky)
    failed = svc.create(ctx, REQUEST)
    assert failed.status == RecommendationStatus.FAILED and failed.resumable and failed.persisted
    assert failed.errors[-1].node == "calculate_availability" and failed.errors[-1].retryable
    stopped = [c for c in svc.checkpoints(ctx, failed.recommendation_id)]
    assert stopped[0]["next"] == ["calculate_availability"]

    outage["on"] = False  # the database is back
    resumed = svc.resume(ctx, failed.recommendation_id)
    assert resumed.recommendation_id == failed.recommendation_id  # same run, no duplicate
    assert resumed.status == RecommendationStatus.SUCCESS and not resumed.resumable
    assert resumed.primary_candidate.employee_code == "EMP-001"
    nodes = [t.node for t in resumed.trace]
    assert nodes.count("validate_request") == 1 and nodes.count("load_authoritative_data") == 1  # not re-run
    with pg_engine.connect() as conn:
        rows = conn.execute(
            select(RecommendationRunModel.status, RecommendationRunModel.resumable).where(
                RecommendationRunModel.id == failed.recommendation_id
            )
        ).all()
    assert rows == [(RecommendationStatus.SUCCESS, False)]
    with pytest.raises(RunNotResumableError):
        svc.resume(ctx, failed.recommendation_id)


def test_completed_failed_run_is_retried_as_a_linked_run(pg_service: Any, ctx: RequestContext) -> None:
    svc, _ = pg_service
    failed = svc.create(ctx, RecommendationRequest(task_code="TASK-NOPE", as_of=AS_OF))
    retried = svc.resume(ctx, failed.recommendation_id)
    assert (
        retried.recommendation_id != failed.recommendation_id
        and retried.retry_of_id == failed.recommendation_id
    )


def test_postgres_checkpoints_are_minimal_inspectable_and_pruned(
    pg_service: Any, ctx: RequestContext, pg_engine: Engine
) -> None:
    from datetime import UTC, datetime, timedelta

    svc, store = pg_service
    r = svc.create(ctx, REQUEST)
    history = svc.checkpoints(ctx, r.recommendation_id)
    assert history and history[0]["next"] == [] and history[0]["execution_status"] == "SUCCESS"
    values = (
        store.sync_saver()
        .get_tuple(svc.runtime.config(str(r.recommendation_id)))
        .checkpoint["channel_values"]
    )
    dumped = json.dumps({k: v for k, v in values.items() if k != "messages"}, default=str)
    assert '"email"' not in dumped and "proficiency_level" not in dumped  # no full employee records
    with pg_engine.connect() as conn:
        tool_rows = conn.execute(select(func.count()).select_from(AgentToolExecutionModel)).scalar_one()
    assert tool_rows >= 0
    pruned = store.prune(svc.container.uow_factory, now=datetime.now(UTC) + timedelta(days=3650))
    assert pruned >= 1
    with pytest.raises(Exception, match="No checkpoints"):
        svc.checkpoints(ctx, r.recommendation_id)


def test_async_postgres_checkpointer(pg_service: Any, ctx: RequestContext) -> None:
    svc, store = pg_service

    async def go() -> Any:
        try:
            return await svc.acreate(ctx, REQUEST)
        finally:
            await store.aclose()

    r = run_async(go())
    assert r.status == RecommendationStatus.SUCCESS and r.thread_id == str(r.recommendation_id)

"""Every registered agent tool, executed through LangGraph's ToolNode against the seeded PostgreSQL database.

Covers per tool: name/registration, valid input, PostgreSQL-backed result, structured output; and across tools:
invalid input, unknown employee/task, empty results, database errors, timeout, unknown tool, malformed and
unexpected arguments, duplicate detection, call budget, profile-expansion limit, read-only sessions, logging and
the async ToolNode path.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from app.agents.tools import TOOL_NAMES
from app.application.agent_queries import AgentQueryService
from app.application.container import AppContainer
from app.application.context import RequestContext
from app.application.recommendations import RecommendationService
from app.core.exceptions import DataSourceUnavailableError
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.schemas.recommendations import RecommendationRequest
from tests.conftest import AS_OF
from tests.fixtures.agent import agent_context, call, content, execute, tool_state

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def state(container: AppContainer, ctx: RequestContext) -> dict[str, Any]:
    return tool_state(container, ctx)


def run_one(container: AppContainer, ctx: RequestContext, state: dict[str, Any], name: str, **args: Any):  # type: ignore[no-untyped-def]
    messages, records = execute(agent_context(container, ctx), state, call(name, **args))
    assert len(messages) == 1 and len(records) == 1
    assert records[0]["tool_name"] == name and messages[0].name == name
    return content(messages[0]), records[0]


# --- one valid, database-backed execution per tool --------------------------------------------------------
def test_search_employees(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, rec = run_one(container, ctx, state, "search_employees", required_skills=["Python", "FastAPI"])
    codes = {e["employee_code"] for e in out["employees"]}
    assert "EMP-001" in codes and out["total"] == len(codes) and rec["status"] == "ok"
    assert all("email" not in e for e in out["employees"])  # work-related fields only
    empty, _ = run_one(container, ctx, state, "search_employees", department="No Such Department")
    assert empty == {"total": 0, "returned": 0, "employees": []}


def test_get_employee_profile(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, rec = run_one(container, ctx, state, "get_employee_profile", employee_id="EMP-001")
    assert out["employee"]["employee_code"] == "EMP-001"
    skills = {s["skill"]: s for s in out["skills"]}
    assert skills["FastAPI"]["declared_level"] == 88 and skills["FastAPI"]["verification"] == "VERIFIED"
    assert out["certifications"][0]["name"] == "PCAP - Python Associate"
    assert (
        out["performance"]["outcomes_reviewed"] > 0 and out["availability_in_window"]["status"] == "AVAILABLE"
    )
    assert rec["result_summary"]["employee_codes"] == ["EMP-001"]


def test_get_employee_skills(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "get_employee_skills", employee_id="EMP-009")
    assert out["employee_code"] == "EMP-009"
    assert any(
        s["stale"] or s["verification"] != "VERIFIED" for s in out["skills"]
    )  # outdated/unverified skills


def test_get_employee_experience(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "get_employee_experience", employee_id="EMP-001")
    assert out["experiences"] and out["projects"]
    assert any(p["completed_tasks"] > 0 for p in out["projects"])


def test_get_employee_performance(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    out, _ = run_one(container, ctx, state, "get_employee_performance", employee_id="EMP-001")
    assert out["summary"]["outcomes_reviewed"] >= 1 and out["recent_outcomes"]
    assert out["periodic_reviews"], "performance_evaluations seeded for employees with history"
    filtered, _ = run_one(
        container,
        ctx,
        state,
        "get_employee_performance",
        employee_id="EMP-001",
        task_category="machine_learning",
    )
    assert filtered["summary"]["outcomes_reviewed"] == 0 and filtered["summary"]["quality_mean"] is None


def test_get_employee_attendance(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "get_employee_attendance", employee_id="EMP-005")
    assert out["presence_at_evaluation_time"] == "PRESENT" and out["records"]


def test_get_employee_leave(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "get_employee_leave", employee_id="EMP-004")
    assert (
        any(lv["status"] == "APPROVED" for lv in out["leaves"]) and out["approved_leave_hours_in_range"] > 0
    )


def test_get_employee_schedule(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "get_employee_schedule", employee_id="EMP-001")
    assert out["has_schedule"] and out["weekly_schedule"] and out["scheduled_hours_in_range"] > 0
    none, _ = run_one(container, ctx, state, "get_employee_schedule", employee_id="EMP-030")
    assert none["has_schedule"] is False and none["weekly_schedule"] == []  # missing schedule is reported


def test_get_employee_assignments(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    out, _ = run_one(container, ctx, state, "get_employee_assignments", employee_id="EMP-002")
    assert out["assignments"] and all(a["task_code"] for a in out["assignments"])


def test_calculate_employee_workload(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    out, _ = run_one(container, ctx, state, "calculate_employee_workload", employee_id="EMP-014")
    assert out["unknown_remaining_effort_tasks"], "shared task without per-assignee estimate is flagged"
    assert out["workload_certainty"] != "CERTAIN"


def test_calculate_employee_capacity(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    out, _ = run_one(container, ctx, state, "calculate_employee_capacity", employee_id="EMP-002")
    assert (
        out["available_capacity_hours"]
        == max(
            0.0,
            round(
                out["available_working_hours"] - out["committed_work_hours"] - out["other_commitment_hours"],
                2,
            ),
        )
        or out["available_capacity_hours"] == 0
    )
    assert "available_capacity" in out["formula"]


def test_get_employee_availability(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    busy, _ = run_one(container, ctx, state, "get_employee_availability", employee_id="EMP-002")
    assert busy["availability"]["status"] == "BUSY"
    w = state["execution_window"]
    explicit, _ = run_one(
        container,
        ctx,
        state,
        "get_employee_availability",
        employee_id="EMP-001",
        window_start=w["start"],
        window_end=w["end"],
    )
    assert explicit["availability"]["status"] == "AVAILABLE"


def test_get_task_details(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "get_task_details", task_id="TASK-SCN-02")
    assert out["task_code"] == "TASK-SCN-02" and out["project"]
    assert [d["task_code"] for d in out["dependencies"]] == ["TASK-SCN-01"]
    current, _ = run_one(container, ctx, state, "get_task_details")  # default: the task being researched
    assert current["task_code"] == "TASK-SCN-01"


def test_get_task_required_skills(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    out, _ = run_one(container, ctx, state, "get_task_required_skills", task_id="TASK-SCN-08")
    assert [s["skill"] for s in out["mandatory"]] == ["Python"]
    assert out["required_certifications"] == ["PCAP - Python Associate"]


def test_search_similar_tasks(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(
        container,
        ctx,
        state,
        "search_similar_tasks",
        skills=["Python", "FastAPI"],
        task_category="backend_development",
    )
    assert out["results"] and out["results"][0]["similarity"] >= out["results"][-1]["similarity"]
    assert any("EMP-001" in r["completed_by"] for r in out["results"])


def test_evaluate_employee_eligibility(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    out, _ = run_one(container, ctx, state, "evaluate_employee_eligibility", employee_id="EMP-003")
    assert out["eligible"] is False
    assert "MANDATORY_SKILL_MISSING" in {r["code"] for r in out["exclusion_reasons"]}


def test_get_candidate_score(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "get_candidate_score", employee_id="EMP-001")
    assert out["eligible"] and out["suitability_score"] == 85.5  # same as the official recommendation
    assert abs(sum(c["weight"] for c in out["criteria"]) - 1) < 1e-3 and "not a probability" in out["note"]


def test_rank_task_candidates(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    out, _ = run_one(container, ctx, state, "rank_task_candidates", top_n=3)
    assert out["ranked"][0]["employee_code"] == "EMP-001" and out["ranked"][0]["rank"] == 1
    assert out["exclusion_summary"]["MANDATORY_SKILL_MISSING"] > 0 and len(out["ranked"]) <= 3


def test_get_recommendation_evidence(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    rec = RecommendationService(container).create(
        ctx, RecommendationRequest(task_code="TASK-SCN-01", as_of=AS_OF)
    )
    out, _ = run_one(
        container,
        ctx,
        state,
        "get_recommendation_evidence",
        recommendation_id=str(rec.recommendation_id),
        employee_id="EMP-001",
    )
    assert out["found"] and out["rank"] == 1 and out["score_components"]


def test_explain_exclusion_reason(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    out, _ = run_one(container, ctx, state, "explain_exclusion_reason", employee_id="EMP-004")
    assert not out["eligible"] and out["reasons"][0]["code"] == "ON_LEAVE_DURING_WINDOW"
    assert out["summary"].startswith("Excluded by")


def test_every_registered_tool_was_executed_in_this_module() -> None:
    import sys

    module = sys.modules[__name__]
    tested = {name.removeprefix("test_") for name in dir(module) if name.startswith("test_")}
    assert set(TOOL_NAMES) <= tested


# --- invalid input, missing records, empty results ------------------------------------------------------------
def test_invalid_and_missing_inputs(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    agent_ctx = agent_context(container, ctx)
    messages, records = execute(
        agent_ctx,
        state,
        call("get_employee_profile", "missing_arg"),  # employee_id required
        call("get_employee_profile", "extra_arg", employee_id="EMP-001", salary=True),
        call("get_employee_profile", "unknown_emp", employee_id="EMP-999"),
        call("get_task_details", "unknown_task", task_id="TASK-NOPE"),
        call("search_employees", "bad_enum", employment_status="RETIRED"),
        call("get_employee_profile", "malformed", _malformed_arguments="{oops"),
    )
    by_id = {m.tool_call_id: content(m)["error"] for m in messages}
    assert by_id["missing_arg"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert "salary" in by_id["extra_arg"]["message"]
    assert by_id["unknown_emp"]["code"] == "NOT_FOUND" and by_id["unknown_task"]["code"] == "NOT_FOUND"
    assert (
        by_id["bad_enum"]["code"] == "INVALID_TOOL_ARGUMENTS"
        and by_id["malformed"]["code"] == "INVALID_TOOL_ARGUMENTS"
    )
    status = {r["tool_call_id"]: r["status"] for r in records}
    assert status["missing_arg"] == status["extra_arg"] == status["malformed"] == "rejected"
    assert status["unknown_emp"] == status["unknown_task"] == "error"
    assert all(m.status == "error" for m in messages)


def test_unknown_tool_is_rejected_and_logged(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.agents.tools.policy"):
        messages, records = execute(
            agent_context(container, ctx), state, call("execute_sql", query="DROP TABLE x")
        )
    assert content(messages[0])["error"]["code"] == "UNKNOWN_TOOL"
    assert records[0]["status"] == "rejected" and records[0]["error_code"] == "UNKNOWN_TOOL"
    logged = [r for r in caplog.records if r.getMessage() == "agent tool call"]
    assert logged and logged[0].tool == "execute_sql" and logged[0].tool_status == "rejected"  # type: ignore[attr-defined]


def test_database_error_is_structured(test_settings: Any, ctx: RequestContext, state: dict[str, Any]) -> None:
    dead = create_engine("postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    broken = AppContainer(test_settings, SqlAlchemyUnitOfWorkFactory(create_session_factory(dead)), None)
    messages, records = execute(
        agent_context(broken, ctx), state, call("get_employee_profile", employee_id="EMP-001")
    )
    err = content(messages[0])["error"]
    assert err["code"] == "DATABASE_UNAVAILABLE" and err["retryable"] and err["category"] == "DATABASE_ERROR"
    assert records[0]["status"] == "error"


def test_timeout(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow(self: AgentQueryService, ref: str) -> Any:
        time.sleep(1.5)
        raise AssertionError("never returned to the model")

    monkeypatch.setattr(AgentQueryService, "employee_skills", slow)
    messages, records = execute(
        agent_context(container, ctx, tool_timeout_seconds=0.3),
        state,
        call("get_employee_skills", employee_id="EMP-001"),
    )
    assert content(messages[0])["error"]["code"] == "TOOL_TIMEOUT"
    assert records[0]["status"] == "timeout" and records[0]["duration_ms"] < 1400


def test_duplicate_budget_and_expansion_limits(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    agent_ctx = agent_context(container, ctx, max_tool_calls=3, max_profile_expansions=2)
    history: list[dict[str, Any]] = []

    def step(*calls: dict[str, Any]) -> tuple[list[Any], dict[str, tuple[str, str | None]]]:
        msgs, recs = execute(agent_ctx, {**state, "agent_tool_results": list(history)}, *calls)
        history.extend(recs)
        return msgs, {r["tool_call_id"]: (r["status"], r["error_code"]) for r in recs}

    first_msgs, s1 = step(call("get_employee_skills", "a", employee_id="EMP-001"))
    msgs, s2 = step(
        call("get_employee_skills", "b", employee_id="EMP-001"),  # identical -> duplicate, not re-executed
        call("get_employee_skills", "c", employee_id="EMP-002"),  # 2nd distinct employee -> allowed
    )
    assert s1["a"] == ("ok", None) and s2["b"] == ("duplicate", None) and s2["c"] == ("ok", None)
    assert msgs[0].content == first_msgs[0].content
    _, s3 = step(call("get_employee_skills", "d", employee_id="EMP-003"))  # 3rd distinct employee
    assert s3["d"] == ("rejected", "PROFILE_EXPANSION_LIMIT")
    _, s4 = step(call("get_task_details", "e"))  # 3rd executed call: within budget (rejections do not count)
    assert s4["e"] == ("ok", None)
    _, s5 = step(call("get_task_required_skills", "f"))  # 4th executed call
    assert s5["f"] == ("rejected", "TOOL_CALL_BUDGET_EXCEEDED")


def test_results_are_size_bounded(
    container: AppContainer, ctx: RequestContext, state: dict[str, Any]
) -> None:
    messages, records = execute(
        agent_context(container, ctx, max_result_items=2),
        state,
        call("search_employees", required_skills=["Python"]),
    )
    out = content(messages[0])
    assert len(out["employees"]) == 2 and out["_truncated"]["original_lengths"]["employees"] > 2
    assert records[0]["result_summary"]["truncated"] is True


def test_async_tool_node_path(container: AppContainer, ctx: RequestContext, state: dict[str, Any]) -> None:
    messages, records = execute(
        agent_context(container, ctx),
        state,
        call("get_employee_availability", "x", employee_id="EMP-001"),
        call("get_employee_availability", "y", employee_id="EMP-002"),
        use_async=True,
    )
    assert [content(m)["availability"]["status"] for m in messages] == ["AVAILABLE", "BUSY"]
    assert [r["status"] for r in records] == ["ok", "ok"]


def test_tool_sessions_are_read_only(container: AppContainer) -> None:
    """Tools run in READ ONLY transactions: PostgreSQL itself rejects any write."""
    with container.uow_factory(read_only=True) as uow:
        with pytest.raises(Exception, match="read-only transaction") as exc:
            uow.session.execute(text("UPDATE employees SET job_title = job_title"))  # type: ignore[attr-defined]
        assert exc.type is not DataSourceUnavailableError

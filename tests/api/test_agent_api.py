"""Agent endpoints through FastAPI (async graph execution behind ``ainvoke`` / ``astream``)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.config import ApiKeyConfig, Settings
from app.core.security import hash_api_key
from app.domain.enums import Role
from app.infrastructure.llm.mock import MockLLMProvider, calls_turn, final_turn, tool_call
from app.main import create_app
from tests.conftest import AS_OF, make_container

pytestmark = pytest.mark.integration

MANAGER = {"X-API-Key": "agent-manager-key"}
VIEWER = {"X-API-Key": "agent-viewer-key"}
BODY = {"task_code": "TASK-SCN-01", "as_of": AS_OF.isoformat()}


@pytest.fixture(scope="module")
def client(test_settings: Settings, pg_engine: Engine) -> Iterator[TestClient]:
    settings = test_settings.model_copy(
        update={
            "api_keys": [
                ApiKeyConfig(
                    name="mgr",
                    role=Role.MANAGER,
                    company_code="NDS",
                    key_sha256=hash_api_key("agent-manager-key"),
                ),
                ApiKeyConfig(
                    name="view",
                    role=Role.VIEWER,
                    company_code="NDS",
                    key_sha256=hash_api_key("agent-viewer-key"),
                ),
            ],
            "rate_limit_per_minute": 1000,
        }
    )
    provider = MockLLMProvider(
        chat_responses=[
            calls_turn(
                tool_call("rank_task_candidates", "r1"),
                tool_call("get_employee_availability", "r2", employee_id="EMP-001"),
            ),
            final_turn({"relevant_employee_codes": ["EMP-001"], "findings": []}),
            # later runs repeat the last item: an immediate final answer
        ]
    )
    with TestClient(create_app(settings, make_container(settings, pg_engine, provider))) as c:
        yield c


def test_agent_recommend_and_run_inspection(client: TestClient) -> None:
    r = client.post("/api/v1/agent/recommend", json=BODY, headers=MANAGER)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "SUCCESS" and body["agent_mode"] == "native" and body["auto_assigned"] is False
    assert body["primary_candidate"]["employee_code"] == "EMP-001" and body["tool_calls"] == 2
    run_id = body["recommendation_id"]

    run = client.get(f"/api/v1/agent/runs/{run_id}", headers=VIEWER).json()
    assert run["status"] == "SUCCESS" and run["thread_id"] == run_id and run["tool_calls"] == 2
    assert run["primary_employee_code"] == "EMP-001" and run["llm_usage"]["calls"] == 2

    logs = client.get(f"/api/v1/agent/runs/{run_id}/logs", headers=VIEWER).json()
    assert logs[0]["node"] == "validate_request" and logs[-1]["node"] == "save_recommendation"
    assert all(entry["started_at"] for entry in logs if entry["node"] != "tool_execution")

    tools = client.get(f"/api/v1/agent/runs/{run_id}/tools", headers=VIEWER).json()
    assert [t["tool"] for t in tools] == ["rank_task_candidates", "get_employee_availability"]
    assert all(t["status"] == "ok" for t in tools)

    cands = client.get(f"/api/v1/agent/runs/{run_id}/candidates", headers=VIEWER).json()
    assert cands[0]["employee_code"] == "EMP-001"
    checkpoints = client.get(f"/api/v1/agent/runs/{run_id}/checkpoints", headers=VIEWER).json()
    assert checkpoints and checkpoints[0]["next"] == []
    assert client.get(f"/api/v1/agent/runs/{run_id}/errors", headers=VIEWER).json() == []

    # A completed successful run cannot be "retried".
    retry = client.post(f"/api/v1/agent/runs/{run_id}/retry", headers=MANAGER)
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "RUN_NOT_RESUMABLE"


def test_idempotency_key_prevents_duplicate_runs(client: TestClient) -> None:
    headers = {**MANAGER, "Idempotency-Key": "pytest-key-1"}
    first = client.post("/api/v1/recommendations", json=BODY, headers=headers)
    second = client.post("/api/v1/recommendations", json=BODY, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["recommendation_id"] == second.json()["recommendation_id"]
    assert second.json()["idempotent_replay"] is True


def test_stream_endpoint_emits_server_sent_events(client: TestClient) -> None:
    with client.stream("POST", "/api/v1/agent/recommend/stream", json=BODY, headers=MANAGER) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[0]["event"] == "started" and events[-1]["event"] == "result"
    assert events[-1]["recommendation"]["status"] == "SUCCESS"


def test_failed_run_maps_to_http_and_can_be_retried_as_new_run(client: TestClient) -> None:
    r = client.post("/api/v1/agent/recommend", json={**BODY, "task_code": "TASK-NOPE"}, headers=MANAGER)
    assert r.status_code == 404 and r.json()["status"] == "FAILED"
    assert r.json()["errors"][0]["category"] == "VALIDATION_ERROR"
    run_id = r.json()["recommendation_id"]
    errors = client.get(f"/api/v1/agent/runs/{run_id}/errors", headers=VIEWER).json()
    assert errors[0]["code"] == "NOT_FOUND" and errors[0]["category"] == "VALIDATION_ERROR"
    retry = client.post(f"/api/v1/agent/runs/{run_id}/retry", headers=MANAGER)
    assert retry.status_code == 404 and retry.json()["retry_of_id"] == run_id


def test_health_endpoints(client: TestClient) -> None:
    agent = client.get("/api/v1/health/agent", headers=VIEWER).json()
    assert agent["agent_mode"] == "native" and len(agent["tools"]) == 20
    assert "tool_execution" in agent["graph"]["nodes"] and agent["checkpointer"]["backend"] == "memory"
    llm = client.get("/api/v1/health/llm", headers=VIEWER).json()
    assert llm["configured"] and llm["capabilities"]["tool_calling"] is True
    assert client.get("/api/v1/health/agent").status_code == 401  # authenticated


def test_permissions_and_metrics(client: TestClient) -> None:
    assert client.post("/api/v1/agent/recommend", json=BODY, headers=VIEWER).status_code == 403
    assert client.get("/api/v1/evaluation/metrics", headers=VIEWER).status_code == 200
    assert "/api/v1/agent/recommend" in client.get("/openapi.json").json()["paths"]

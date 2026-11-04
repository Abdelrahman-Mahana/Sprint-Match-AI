from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.application.container import AppContainer
from app.core.config import ApiKeyConfig, Settings
from app.core.security import hash_api_key
from app.domain.enums import Role
from app.infrastructure.database.models import TaskAssignmentModel
from app.main import create_app
from tests.conftest import AS_OF

pytestmark = pytest.mark.integration

MANAGER = {"X-API-Key": "test-manager-key"}
VIEWER = {"X-API-Key": "test-viewer-key"}
AS_OF_Q = {"as_of": AS_OF.isoformat()}


@pytest.fixture(scope="module")
def client(test_settings: Settings, container: AppContainer) -> Iterator[TestClient]:
    settings = test_settings.model_copy(
        update={
            "api_keys": [
                ApiKeyConfig(
                    name="mgr",
                    role=Role.MANAGER,
                    company_code="NDS",
                    key_sha256=hash_api_key("test-manager-key"),
                ),
                ApiKeyConfig(
                    name="view",
                    role=Role.VIEWER,
                    company_code="NDS",
                    key_sha256=hash_api_key("test-viewer-key"),
                ),
            ],
            "rate_limit_per_minute": 1000,
        }
    )
    with TestClient(create_app(settings, container)) as c:
        yield c


def employee_id(client: TestClient, code: str) -> str:
    items = client.get("/api/v1/employees", params={"limit": 200}, headers=VIEWER).json()["items"]
    return next(e["id"] for e in items if e["employee_code"] == code)


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["database"] == "ok"
    assert ready.json()["llm"] == {"configured": False}
    assert "X-Request-ID" in ready.headers


def test_authentication_and_authorization(client: TestClient) -> None:
    assert client.get("/api/v1/employees").status_code == 401
    assert client.get("/api/v1/employees", headers={"X-API-Key": "wrong"}).status_code == 401
    r = client.post("/api/v1/recommendations", json={"task_code": "TASK-SCN-01"}, headers=VIEWER)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_employee_endpoints(client: TestClient) -> None:
    page = client.get("/api/v1/employees", params={"limit": 5, "offset": 0}, headers=VIEWER).json()
    assert page["total"] == 30 and len(page["items"]) == 5
    filtered = client.get("/api/v1/employees", params={"skill": "flutter"}, headers=VIEWER).json()
    assert {e["employee_code"] for e in filtered["items"]} == {"EMP-010", "EMP-019"}
    eid = employee_id(client, "EMP-001")
    assert client.get(f"/api/v1/employees/{eid}", headers=VIEWER).json()["full_name"] == "Ahmed Hassan"
    view = client.get(f"/api/v1/employees/{eid}/360", params={**AS_OF_Q, "working_days": 3}, headers=VIEWER)
    assert view.status_code == 200
    data = view.json()["data"]
    assert data["profile"]["employee"]["employee_code"] == "EMP-001" and data["skill_assessments"]
    avail = client.get(
        f"/api/v1/employees/{eid}/availability",
        params={**AS_OF_Q, "preset": "NEXT_WORKING_DAYS", "working_days": 3},
        headers=VIEWER,
    )
    assert avail.json()["availability"]["status"] == "AVAILABLE"
    perf = client.get(f"/api/v1/employees/{eid}/performance", headers=VIEWER).json()
    assert perf["outcome_count"] == 6
    missing = client.get("/api/v1/employees/00000000-0000-0000-0000-000000000000", headers=VIEWER)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_workforce_endpoints(client: TestClient) -> None:
    r = client.get("/api/v1/workforce/availability", params={**AS_OF_Q, "working_days": 3}, headers=VIEWER)
    body = r.json()
    assert r.status_code == 200 and len(body["employees"]) == 29
    assert {"AVAILABLE", "BUSY", "UNAVAILABLE", "UNKNOWN"} <= set(body["status_counts"])
    eid = employee_id(client, "EMP-002")
    r = client.post(
        "/api/v1/workforce/analyze",
        json={"employee_ids": [eid], "as_of": AS_OF.isoformat(), "working_days": 3},
        headers=VIEWER,
    )
    assert r.json()["employees"][0]["availability"]["status"] == "BUSY"


def test_task_endpoints(client: TestClient) -> None:
    tasks = client.get("/api/v1/tasks", params={"status": "TODO", "limit": 50}, headers=VIEWER).json()
    assert any(t["task_code"] == "TASK-SCN-01" for t in tasks["items"])
    spec = {
        "title": "API created task",
        "estimated_hours": 6,
        "complexity": "low",
        "required_skills": [{"skill_name": "Python", "minimum_level": 60, "mandatory": True}],
    }
    created = client.post("/api/v1/tasks", params={"task_code": "API-T-1"}, json=spec, headers=MANAGER)
    assert created.status_code == 201
    assert (
        client.get(f"/api/v1/tasks/{created.json()['id']}", headers=VIEWER).json()["task_code"] == "API-T-1"
    )
    dup = client.post("/api/v1/tasks", params={"task_code": "API-T-1"}, json=spec, headers=MANAGER)
    assert dup.status_code == 409
    bad = client.post(
        "/api/v1/tasks",
        params={"task_code": "API-T-2"},
        json={**spec, "required_skills": [{"skill_name": "Fortran", "minimum_level": 60}]},
        headers=MANAGER,
    )
    assert bad.status_code == 422
    analysis = client.post(
        "/api/v1/tasks/analyze",
        json={"title": "Refactor the FastAPI app", "estimated_hours": 4},
        headers=MANAGER,
    ).json()
    assert (
        analysis["status"] == "READY"
        and analysis["requirements"]["required_skills"][0]["skill_name"] == "FastAPI"
    )


def test_recommendation_lifecycle(client: TestClient) -> None:
    r = client.post(
        "/api/v1/recommendations",
        json={"task_code": "TASK-SCN-01", "as_of": AS_OF.isoformat()},
        headers=MANAGER,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "SUCCESS" and body["primary_candidate"]["employee_code"] == "EMP-001"
    assert body["auto_assigned"] is False and body["persisted"] is True
    rid = body["recommendation_id"]
    assert client.get(f"/api/v1/recommendations/{rid}", headers=VIEWER).json()["recommendation_id"] == rid
    candidates = client.get(f"/api/v1/recommendations/{rid}/candidates", headers=VIEWER).json()
    assert candidates[0]["employee_code"] == "EMP-001" and len(candidates) == 29
    re = client.post(f"/api/v1/recommendations/{rid}/revalidate", params=AS_OF_Q, headers=MANAGER)
    assert re.status_code == 201 and re.json()["revalidation"]["still_valid"] is True


def test_recommendation_with_structured_task_and_errors(client: TestClient) -> None:
    payload = {
        "task": {
            "title": "Fix payout rounding",
            "estimated_hours": 4,
            "complexity": "low",
            "required_skills": [{"skill_name": "Python", "minimum_level": 70}],
        },
        "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 2},
        "as_of": AS_OF.isoformat(),
        "include_alternatives": False,
    }
    r = client.post("/api/v1/recommendations", json=payload, headers=MANAGER)
    assert r.status_code == 201 and r.json()["alternatives"] == []
    needs = client.post(
        "/api/v1/recommendations",
        json={"task_code": "TASK-SCN-06", "as_of": AS_OF.isoformat()},
        headers=MANAGER,
    )
    assert needs.status_code == 201 and needs.json()["status"] == "NEEDS_INPUT"
    missing = client.post(
        "/api/v1/recommendations", json={"task_code": "NOPE", "as_of": AS_OF.isoformat()}, headers=MANAGER
    )
    assert missing.status_code == 404 and missing.json()["status"] == "FAILED"
    invalid = client.post(
        "/api/v1/recommendations", json={"task_code": "A", "task_id": AS_OF.isoformat()}, headers=MANAGER
    )
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "INVALID_INPUT"
    naive = client.post(
        "/api/v1/recommendations",
        json={"task_code": "TASK-SCN-01", "as_of": "2026-11-03T10:00:00"},
        headers=MANAGER,
    )
    assert naive.status_code == 422


def test_outcomes_and_evaluation_summary(client: TestClient, pg_engine) -> None:  # type: ignore[no-untyped-def]
    task = client.get("/api/v1/tasks", params={"status": "DONE", "limit": 1}, headers=VIEWER).json()["items"][
        0
    ]
    laila = employee_id(client, "EMP-008")
    body = {
        "task_id": task["id"],
        "employee_id": laila,
        "quality_score": 80,
        "accepted": True,
        "on_time": True,
    }
    # Not assigned to the task -> rejected (outcomes are evidence of the assignee's own work).
    assert client.post("/api/v1/task-outcomes", json=body, headers=MANAGER).status_code == 422
    with pg_engine.begin() as conn:
        conn.execute(
            insert(TaskAssignmentModel).values(
                id=uuid.uuid4(),
                task_id=task["id"],
                employee_id=laila,
                assignment_status="COMPLETED",
                allocated_hours=2,
                remaining_estimated_hours=0,
            )
        )
    created = client.post("/api/v1/task-outcomes", json=body, headers=MANAGER)
    assert created.status_code == 201
    assert client.post("/api/v1/task-outcomes", json=body, headers=MANAGER).status_code == 409
    open_task = client.get("/api/v1/tasks", params={"status": "TODO", "limit": 1}, headers=VIEWER).json()[
        "items"
    ][0]
    assert (
        client.post(
            "/api/v1/task-outcomes", json={**body, "task_id": open_task["id"]}, headers=MANAGER
        ).status_code
        == 422
    )
    summary = client.get("/api/v1/evaluation/summary", headers=VIEWER).json()
    assert summary["outcomes_recorded"] >= 99 and "SUCCESS" in summary["recommendations_by_status"]


def test_openapi_documents_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/recommendations",
        "/api/v1/employees/{employee_id}/360",
        "/api/v1/tasks/analyze",
        "/api/v1/workforce/analyze",
        "/api/v1/evaluation/summary",
        "/api/v1/task-outcomes",
    ):
        assert p in paths

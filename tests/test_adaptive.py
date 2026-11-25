"""Unit tests for adaptive schema discovery, security redaction/auth/rate-limiting, and tool schema invariants."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from sqlalchemy import create_engine, text

from app.agents.state import AgentLimits
from app.agents.tools import ALL_TOOLS, TOOL_NAMES, ToolResultTooLargeError, bounded_json, format_tool_error
from app.application.runner import RecommendationService
from app.application.services import AppContainer, RequestContext
from app.core import (
    ApiKeyAuthenticator,
    ApiKeyConfig,
    AuthenticationError,
    AuthorizationError,
    DataSourceUnavailableError,
    JsonFormatter,
    NotFoundError,
    RateLimiter,
    RateLimitExceededError,
    Settings,
    categorize,
    hash_api_key,
    redact,
    register_secrets,
)
from app.domain import (
    AvailabilityStatus,
    Company,
    EmploymentStatus,
    ErrorCategory,
    RecommendationStatus,
    Role,
)
from app.infrastructure.adaptive import _saved_mapping_is_usable, employment_status, resolve
from app.infrastructure.discovery import SchemaDiscovery
from app.infrastructure.llm import MockLLMProvider
from app.schemas import ExecutionWindowRequest, RecommendationRequest, TaskSpecification

# ===========================================================================
# Adaptive Database Tests
# ===========================================================================

AS_OF = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def _foreign_company() -> Company:
    return Company(
        id=uuid.uuid4(),
        code="default",
        name="Foreign Corp",
        timezone="UTC",
        working_weekdays=(0, 1, 2, 3, 4),
    )


def _req_ctx(company: Company) -> RequestContext:
    return RequestContext(company=company, actor="tester", role=Role.MANAGER, request_id="test-adaptive")


def test_arbitrary_foreign_sqlite_schema_end_to_end(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_file = tmp_path / "foreign_hr.db"
    url = f"sqlite:///{db_file}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE staff_members (
                    emp_no INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    role_title TEXT,
                    dept_name TEXT,
                    tech_stack TEXT,
                    yrs_exp REAL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE work_items (
                    code TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    assignee_ref INTEGER,
                    est_hrs REAL,
                    state TEXT,
                    due_date TEXT,
                    FOREIGN KEY(assignee_ref) REFERENCES staff_members(emp_no)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO staff_members (emp_no, full_name, role_title, dept_name, tech_stack, yrs_exp) VALUES
                (101, 'Sara Nabil', 'Senior Backend Engineer', 'Platform', '{"Python": 5, "FastAPI": 5, "SQL": 4}', 6.0),
                (102, 'Karim Adel', 'Backend Developer', 'Platform', '{"Python": 4, "FastAPI": 4, "SQL": 4}', 4.0),
                (103, 'Mona Zaki', 'Frontend Developer', 'Web', 'React, TypeScript, Tailwind', 5.0)
                """
            )
        )
        due = (AS_OF + timedelta(days=7)).isoformat()
        conn.execute(
            text(
                """
                INSERT INTO work_items (code, summary, assignee_ref, est_hrs, state, due_date) VALUES
                ('WI-1', 'Core billing job', 102, 42.0, 'IN_PROGRESS', :due),
                ('WI-2', 'Finished API cleanup', 101, 6.0, 'DONE', :due)
                """
            ),
            {"due": due},
        )

    mapping = SchemaDiscovery(engine).discover(db_url_masked=url)
    assert mapping.is_usable()
    assert mapping.employees.actual_table == "staff_members"
    assert mapping.employees.col("skills_text") == "tech_stack"
    assert mapping.employees.col("department_name") == "dept_name"
    assert mapping.tasks.actual_table == "work_items"
    assert mapping.tasks.col("assignee_id") == "assignee_ref"

    settings = Settings(
        database_url=url,  # type: ignore[arg-type]
        schema_mapping_path=str(tmp_path / "mapping.json"),
        checkpointer="none",
        llm_provider="none",
        agent_tool_mode="disabled",
    )
    resolved = resolve(engine, settings, db_url_masked=url)
    assert resolved.kind == "adaptive"

    container = AppContainer(
        settings,
        resolved.factory,
        None,
        adapter_kind=resolved.kind,
        schema_mapping=resolved.mapping,
    )
    service = RecommendationService(container, persist=False)
    company = _foreign_company()
    ctx = _req_ctx(company)

    # Karim (102) has a 42h active work_item so lacks free sprint hours; Sara (101) has full capacity + 5/5->100 skill
    resp = service.create(
        ctx,
        RecommendationRequest(
            task=TaskSpecification(
                title="",
                description="Build an idempotent payment API with Python and FastAPI",
                auto_complete=True,
            ),
            execution_window=ExecutionWindowRequest(preset="NEXT_WORKING_DAYS", working_days=5),
            as_of=AS_OF,
        ),
    )
    assert resp.status == RecommendationStatus.SUCCESS
    assert resp.primary_candidate is not None
    assert resp.primary_candidate.name == "Sara Nabil"
    karim = next(c for c in resp.excluded_candidates if c.name == "Karim Adel")
    assert karim.availability_status in (AvailabilityStatus.BUSY, AvailabilityStatus.PARTIALLY_AVAILABLE)
    engine.dispose()


def test_minimal_hr_schema_synthesizes_skills_from_job_titles(tmp_path) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'minimal.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE people (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    job_title TEXT,
                    department TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO people (id, name, job_title, department) VALUES
                (1, 'Tarek Hassan', 'Senior React & Next.js Frontend Engineer', 'Web'),
                (2, 'Nourhan Ali', 'DevOps & Kubernetes Infrastructure Engineer', 'Cloud')
                """
            )
        )

    settings = Settings(
        database_url=url,  # type: ignore[arg-type]
        schema_mapping_path=str(tmp_path / "min_map.json"),
        checkpointer="none",
        llm_provider="none",
        agent_tool_mode="disabled",
    )
    resolved = resolve(engine, settings, db_url_masked=url)
    service = RecommendationService(AppContainer(settings, resolved.factory, None), persist=False)
    resp = service.create(
        _req_ctx(_foreign_company()),
        RecommendationRequest(
            task=TaskSpecification(
                description="Build responsive React and Next.js analytics dashboard UI",
                auto_complete=True,
            ),
            execution_window=ExecutionWindowRequest(preset="NEXT_WORKING_DAYS", working_days=5),
            as_of=AS_OF,
        ),
    )
    assert resp.status in (RecommendationStatus.SUCCESS, RecommendationStatus.PARTIAL_DATA)
    assert resp.primary_candidate is not None
    assert resp.primary_candidate.name == "Tarek Hassan"
    assert any(c.name == "Nourhan Ali" for c in resp.excluded_candidates)
    engine.dispose()


def test_llm_schema_refinement_and_hallucination_rejection(tmp_path) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'cryptic.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE tbl_roster_x (
                    k_pk INTEGER PRIMARY KEY,
                    c_label TEXT NOT NULL,
                    c_mail TEXT,
                    c_tags TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO tbl_roster_x (k_pk, c_label, c_mail, c_tags) VALUES
                (1, 'Youssef Omar', 'youssef@corp.io', 'Python:90, Docker:80')
                """
            )
        )

    llm_payload = {
        "mappings": [
            {
                "concept": "employees",
                "table": "tbl_roster_x",
                "columns": {
                    "id": "k_pk",
                    "full_name": "c_label",
                    "email": "c_mail",
                    "skills_text": "c_tags",
                    "job_title": "nonexistent_hallucinated_col",
                },
            },
            {
                "concept": "tasks",
                "table": "hallucinated_table",
                "columns": {"id": "id"},
            },
        ]
    }
    mock_llm = MockLLMProvider([json.dumps(llm_payload)])
    mapping = SchemaDiscovery(engine, llm=mock_llm).discover(db_url_masked=url)
    assert mapping.is_usable()
    assert mapping.employees.actual_table == "tbl_roster_x"
    assert mapping.employees.col("full_name") == "c_label"
    assert mapping.employees.col("skills_text") == "c_tags"
    # Hallucinated column & hallucinated table are strictly rejected
    assert mapping.employees.col("job_title") is None
    assert mapping.tasks.actual_table is None
    engine.dispose()


def test_saved_mapping_isolated_across_different_databases(tmp_path) -> None:  # type: ignore[no-untyped-def]
    url_a = f"sqlite:///{tmp_path / 'a.db'}"
    url_b = f"sqlite:///{tmp_path / 'b.db'}"
    for u in (url_a, url_b):
        eng = create_engine(u)
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE employees (id INTEGER PRIMARY KEY, full_name TEXT);"))
        eng.dispose()

    eng_a = create_engine(url_a)
    mapping_a = SchemaDiscovery(eng_a).discover(db_url_masked=url_a)
    assert _saved_mapping_is_usable(mapping_a, eng_a, db_url_masked=url_a)
    assert not _saved_mapping_is_usable(mapping_a, eng_a, db_url_masked=url_b)
    eng_a.dispose()


def test_employment_status_coercion_handles_missing_and_arabic() -> None:
    assert employment_status(None, default_if_missing=EmploymentStatus.ACTIVE) == EmploymentStatus.ACTIVE
    assert employment_status(None) == EmploymentStatus.INACTIVE
    assert employment_status("نشط") == EmploymentStatus.ACTIVE
    assert employment_status("مستقيل") == EmploymentStatus.INACTIVE

# ===========================================================================
# Security & Tool Schema Tests
# ===========================================================================

def test_redaction_of_secrets() -> None:
    register_secrets(["super-secret-value"])
    text = redact(
        "Authorization: Bearer abc.def api_key=xyz123 password='hunter2' "
        "url=postgresql+psycopg://user:pw123@host/db token=sk-abcdefghijk super-secret-value"
    )
    for leaked in ("abc.def", "xyz123", "hunter2", "pw123", "sk-abcdefghijk", "super-secret-value"):
        assert leaked not in text
    record = logging.makeLogRecord(
        {"msg": "calling with x-api-key: topsecret", "levelname": "INFO", "name": "t"}
    )
    assert "topsecret" not in JsonFormatter().format(record)


def test_api_key_authentication() -> None:
    auth = ApiKeyAuthenticator(
        [ApiKeyConfig(name="m", role=Role.MANAGER, company_code="NDS", key_sha256=hash_api_key("k1"))]
    )
    principal = auth.authenticate("k1")
    assert principal.role == Role.MANAGER and principal.company_code == "NDS"
    for bad in (None, "", "k2"):
        with pytest.raises(AuthenticationError):
            auth.authenticate(bad)
    with pytest.raises(AuthorizationError):
        principal.require(Role.ADMIN)


def test_rate_limiter() -> None:
    limiter = RateLimiter(per_minute=2)
    limiter.check("a")
    limiter.check("a")
    limiter.check("b")
    with pytest.raises(RateLimitExceededError):
        limiter.check("a")


REQUIRED_TOOLS = {
    "search_employees",
    "get_employee_profile",
    "get_employee_skills",
    "get_employee_experience",
    "get_employee_performance",
    "get_employee_attendance",
    "get_employee_leave",
    "get_employee_schedule",
    "get_employee_assignments",
    "calculate_employee_workload",
    "calculate_employee_capacity",
    "get_employee_availability",
    "get_task_details",
    "get_task_required_skills",
    "search_similar_tasks",
    "evaluate_employee_eligibility",
    "get_candidate_score",
    "rank_task_candidates",
    "get_recommendation_evidence",
    "explain_exclusion_reason",
}


def test_tool_set_is_closed_and_read_only() -> None:
    assert set(TOOL_NAMES) == REQUIRED_TOOLS
    forbidden = ("sql", "exec", "shell", "update", "delete", "create", "assign_", "write", "set_")
    assert not [n for n in TOOL_NAMES if any(f in n for f in forbidden)]


def test_tool_schemas_hide_runtime_and_are_documented() -> None:
    for tool in ALL_TOOLS:
        schema = convert_to_openai_tool(tool)["function"]
        assert schema["description"] and len(schema["description"]) > 30, tool.name
        assert "runtime" not in schema["parameters"].get("properties", {}), tool.name


def test_tool_errors_are_structured_and_redacted() -> None:
    register_secrets(["sk-super-secret-123456"])
    payload = json.loads(format_tool_error(NotFoundError("Employee 'EMP-999' not found")))
    assert payload["error"] == {
        "code": "NOT_FOUND",
        "category": "VALIDATION_ERROR",
        "message": "Employee 'EMP-999' not found",
        "retryable": False,
    }
    leaked = json.loads(format_tool_error(DataSourceUnavailableError("down: sk-super-secret-123456")))
    assert "sk-super-secret" not in leaked["error"]["message"] and leaked["error"]["retryable"]
    assert json.loads(format_tool_error(RuntimeError("boom")))["error"]["code"] == "INTERNAL_ERROR"


def test_tool_results_are_bounded() -> None:
    limits = AgentLimits(max_result_items=3, max_result_chars=400)
    text = bounded_json({"rows": [{"i": i, "pad": "x" * 20} for i in range(50)]}, limits)
    data = json.loads(text)
    assert len(data["rows"]) <= 3 and data["_truncated"]["original_lengths"]["rows"] == 50
    assert len(text) <= 400
    with pytest.raises(ToolResultTooLargeError):
        bounded_json({"blob": "y" * 1000}, limits)


def test_error_categories_cover_standard_set() -> None:
    assert categorize("DATABASE_UNAVAILABLE") == ErrorCategory.DATABASE_ERROR
    assert categorize("PROVIDER_TIMEOUT") == ErrorCategory.TIMEOUT
    assert categorize("LLM_INVALID_OUTPUT") == ErrorCategory.LLM_PROVIDER_ERROR
    assert categorize("UNKNOWN_TOOL") == ErrorCategory.TOOL_EXECUTION_ERROR
    assert categorize("TOOL_CALL_BUDGET_EXCEEDED") == ErrorCategory.TOOL_EXECUTION_ERROR
    assert categorize("FINAL_VALIDATION_FAILED") == ErrorCategory.GRAPH_EXECUTION_ERROR
    assert categorize("something-new") == ErrorCategory.INTERNAL_ERROR

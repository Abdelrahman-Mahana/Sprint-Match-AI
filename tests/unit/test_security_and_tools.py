from __future__ import annotations

import json
import logging

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agents.context import AgentLimits
from app.agents.tools import ALL_TOOLS, TOOL_NAMES
from app.agents.tools.base import ToolResultTooLargeError, bounded_json, format_tool_error
from app.core.config import ApiKeyConfig
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    DataSourceUnavailableError,
    ErrorCategory,
    NotFoundError,
    RateLimitExceededError,
    categorize,
)
from app.core.logging import JsonFormatter, redact, register_secrets
from app.core.security import ApiKeyAuthenticator, RateLimiter, hash_api_key
from app.domain.enums import Role


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

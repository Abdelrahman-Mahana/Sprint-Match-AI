"""Live tests against the LLM provider configured in .env (never run by default).

    RUN_LIVE_LLM_TESTS=1 uv run pytest tests/live -m live

They prove the real provider works end to end: capability probe, native tool call, and a complete agent run
whose tool calls are executed by the ToolNode against the seeded test database. The deterministic outcome must
be identical to the run without an LLM.
"""

from __future__ import annotations

import os

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy import Engine

from app.application.context import RequestContext
from app.application.recommendations import RecommendationService
from app.core.config import Settings
from app.infrastructure.llm.factory import build_llm_provider
from app.schemas.recommendations import RecommendationRequest
from tests.conftest import AS_OF, ROOT, make_container

pytestmark = [
    pytest.mark.live,
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM_TESTS") != "1", reason="set RUN_LIVE_LLM_TESTS=1"),
]


@pytest.fixture(scope="module")
def live_settings(test_settings: Settings) -> Settings:
    env = dotenv_values(ROOT / ".env")
    if env.get("LLM_PROVIDER") in (None, "none", "mock"):
        pytest.skip("no real LLM provider configured in .env")
    return test_settings.model_copy(
        update={
            "llm_provider": env["LLM_PROVIDER"],
            "llm_base_url": env.get("LLM_BASE_URL"),
            "llm_api_key": SecretStr(env["LLM_API_KEY"]) if env.get("LLM_API_KEY") else None,
            "llm_model": env.get("LLM_MODEL"),
            "llm_max_retries": 4,
            "agent_max_tool_rounds": 3,
            "agent_max_tool_result_chars": 3500,
            "agent_max_tool_result_items": 8,
        }
    )


def test_live_provider_capabilities(live_settings: Settings) -> None:
    llm = build_llm_provider(live_settings)
    assert llm is not None and llm.health_check().ok
    caps = llm.capabilities(probe=True)
    assert caps.source == "probed" and caps.tool_calling is not None


def test_live_agent_run(live_settings: Settings, pg_engine: Engine, ctx: RequestContext) -> None:
    container = make_container(live_settings, pg_engine, build_llm_provider(live_settings))
    svc = RecommendationService(container)
    r = svc.create(ctx, RecommendationRequest(task_code="TASK-SCN-01", as_of=AS_OF))
    # The deterministic result never depends on the model.
    assert r.status.value == "SUCCESS" and r.primary_candidate.employee_code == "EMP-001"
    assert r.primary_candidate.suitability_score == 85.5
    assert r.agent_mode in ("native", "json_plan") and r.llm_usage and r.llm_usage.calls >= 1
    tools = svc.tool_executions(ctx, r.recommendation_id)
    assert any(t.status.value == "ok" for t in tools), "the live model called at least one tool successfully"

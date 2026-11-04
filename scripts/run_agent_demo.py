"""Run one recommendation through the compiled LangGraph agent against the demo database and print a report.

Uses the configured LLM provider and checkpointer from .env (LLM_PROVIDER=none runs the deterministic
pipeline with the research agent disabled).

Usage:  uv run python -m scripts.run_agent_demo [TASK_CODE] [--as-of 2026-11-03T10:00:00+02:00] [--stream]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from app.agents.checkpointer import CheckpointStore
from app.agents.runtime import AgentRuntime
from app.application.container import AppContainer
from app.application.context import RequestContext
from app.application.recommendations import RecommendationService
from app.core.config import get_settings
from app.core.logging import configure_logging, register_secrets
from app.domain.enums import Role
from app.infrastructure.database.session import create_db_engine, create_session_factory
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.infrastructure.llm.factory import build_llm_provider
from app.schemas.recommendations import RecommendationRequest, RecommendationResponse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_code", nargs="?", default="TASK-SCN-01")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--company", default="NDS")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    register_secrets(settings.secret_values())
    container = AppContainer(
        settings,
        SqlAlchemyUnitOfWorkFactory(create_session_factory(create_db_engine(settings))),
        build_llm_provider(settings),
    )
    store = CheckpointStore(settings)
    container.agent_runtime = AgentRuntime(container, store)
    with container.uow_factory() as uow:
        company = uow.companies.get_by_code(args.company)
    if company is None:
        print(f"Unknown company {args.company}")
        return 1
    ctx = RequestContext(company=company, actor="run_agent_demo", role=Role.MANAGER, request_id="demo")
    request = RecommendationRequest(
        task_code=args.task_code, as_of=datetime.fromisoformat(args.as_of) if args.as_of else None
    )
    svc = RecommendationService(container)
    try:
        if args.stream:
            for event in svc.stream(ctx, request):
                if event["event"] == "result":
                    r = RecommendationResponse.model_validate(event["recommendation"])
                else:
                    print("  ", event)
        else:
            r = svc.create(ctx, request)
    finally:
        store.close()

    print(
        f"\nrun {r.recommendation_id}  status={r.status.value}  agent_mode={r.agent_mode}  persisted={r.persisted}"
    )
    print(
        f"llm={r.llm_provider}/{r.llm_model}  usage={r.llm_usage}  tool_calls={r.tool_calls}  {r.execution_time_ms} ms"
    )
    if r.research:
        print(
            f"research: status={r.research.get('status')} rounds={r.research.get('rounds')} tools={r.research.get('tools_used')}"
        )
        for f in (r.research.get("summary") or {}).get("findings", [])[:6]:
            print(f"   - {f.get('employee_code')}: {f.get('observation')}")
    if r.primary_candidate:
        p = r.primary_candidate
        print(f"PRIMARY: {p.name} ({p.employee_code}) score={p.suitability_score} coverage={p.data_coverage}")
    for a in r.alternatives:
        print(f"  alternative: {a.name} ({a.employee_code}) score={a.suitability_score}")
    print(f"excluded: {len(r.excluded_candidates)}  warnings: {r.warnings}")
    print(f"errors: {[(e.code, e.category) for e in r.errors]}")
    print(f"\nexplanation ({r.explanation_source}):\n{r.explanation}")
    print("\nThis is a recommendation only; no assignment was made.")
    return 0 if r.status.value != "FAILED" else 1


if __name__ == "__main__":
    sys.exit(main())

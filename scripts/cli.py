"""Unified management CLI for SprintMatch AI.

Usage:
    python -m scripts.cli init-db
    python -m scripts.cli seed [--reset] [--reference-date YYYY-MM-DD] [--seed N]
    python -m scripts.cli reset [--reference-date YYYY-MM-DD] [--seed N]
    python -m scripts.cli validate
    python -m scripts.cli demo [TASK_CODE] [--as-of ISO] [--company NDS] [--stream]
    python -m scripts.cli check-llm
    python -m scripts.cli export-graph [OUTPUT_PATH]
    python -m scripts.cli hash-key "<api-key>"
    python -m scripts.cli prune-checkpoints
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from sqlalchemy import Engine

from app.agents.graph import AgentRuntime, mermaid
from app.agents.state import CheckpointStore, resolve_agent_mode
from app.agents.tools import ALL_TOOLS
from app.application.runner import RecommendationService
from app.application.services import AppContainer, RequestContext
from app.core import Settings, SprintMatchError, configure_logging, get_settings, register_secrets
from app.domain import AgentMode, Role
from app.infrastructure.llm import ChatMessage, LLMError, build_chat_model, build_llm_provider
from app.infrastructure.orm import create_db_engine, create_session_factory
from app.infrastructure.repositories import SqlAlchemyUnitOfWorkFactory
from app.schemas import RecommendationRequest, RecommendationResponse
from scripts.demo_data import seed_demo_data, validate_dataset

ROOT = Path(__file__).resolve().parents[1]


def _engine_and_settings(database_url: str | None = None) -> tuple[Engine, Settings]:
    settings = get_settings()
    return create_db_engine(settings, url=database_url), settings


def _reference_date(settings: Settings, override: str | None) -> date:
    if override:
        return date.fromisoformat(override)
    if settings.demo_reference_date:
        return settings.demo_reference_date
    return datetime.now(ZoneInfo("Africa/Cairo")).date()


def upgrade(database_url: str | None = None, revision: str = "head") -> None:
    """Create or upgrade the PostgreSQL schema via Alembic."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    if database_url:
        cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, revision)


def setup_checkpoints() -> str:
    """Create the LangGraph checkpoint tables with PostgresSaver.setup()."""
    settings = get_settings()
    store = CheckpointStore(settings)
    try:
        store.sync_saver()
    finally:
        store.close()
    return settings.checkpointer


def _cmd_init_db(_: argparse.Namespace) -> int:
    upgrade()
    print("Database schema is at the latest revision.")
    backend = setup_checkpoints()
    print(f"Checkpoint storage ready (backend: {backend}).")
    return 0


def _seed(reset: bool, ref_date: str | None, seed: int | None) -> int:
    engine, settings = _engine_and_settings()
    try:
        ds = seed_demo_data(
            engine,
            environment=settings.environment,
            reference_date=_reference_date(settings, ref_date),
            seed=seed if seed is not None else settings.demo_seed,
            reset=reset,
        )
    except SprintMatchError as exc:
        print(f"Seeding refused: {exc.message}", file=sys.stderr)
        return 1
    print(f"Seeded demo data for reference day {ds.reference_date} (now = {ds.now.isoformat()}):")
    for table, n in ds.counts().items():
        print(f"  {table:<28} {n:>5}")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    return _seed(args.reset, args.reference_date, args.seed)


def _cmd_reset(args: argparse.Namespace) -> int:
    return _seed(True, args.reference_date, args.seed)


def _cmd_validate(_: argparse.Namespace) -> int:
    engine, _ = _engine_and_settings()
    results = validate_dataset(engine)
    failed = [r for r in results if not r.passed]
    print(f"Validated dataset consistency: {len(results) - len(failed)}/{len(results)} checks passed.")
    for r in results:
        status_str = "PASS" if r.passed else "FAIL"
        print(f"  [{status_str}] {r.name}: {r.description} (violations: {r.violations})")
    if failed:
        print(f"\n{len(failed)} consistency check(s) failed!", file=sys.stderr)
        return 1
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
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
    ctx = RequestContext(company=company, actor="cli_demo", role=Role.MANAGER, request_id="demo")
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


class _Echo(BaseModel):
    status: str
    number: int


def _cmd_check_llm(_: argparse.Namespace) -> int:
    settings = get_settings()
    register_secrets(settings.secret_values())
    llm = build_llm_provider(settings)
    if llm is None:
        print("LLM_PROVIDER=none: nothing to test (the deterministic engine does not need an LLM).")
        return 0
    print(f"provider={llm.name} model={llm.model}")
    health = llm.health_check()
    print(f"1. health: ok={health.ok} latency_ms={health.latency_ms} detail={health.detail}")
    if not health.ok:
        return 1
    decision = resolve_agent_mode(settings, llm)
    print(f"2. capabilities: {decision.capabilities} -> agent mode {decision.mode.value} ({decision.reason})")
    try:
        result = llm.generate_structured(
            [ChatMessage(role="user", content='Return {"status": "ok", "number": 7} as JSON.')],
            _Echo,
            schema_name="echo",
        )
        print(
            f"3. structured output: {result.value} ({result.latency_ms} ms, tokens {result.prompt_tokens}/{result.completion_tokens})"
        )
    except LLMError as exc:
        print(f"3. structured output FAILED: {exc.code}")
        return 1
    if decision.mode == AgentMode.DISABLED:
        print("4. tool calling: skipped (agent mode disabled)")
        return 0
    model = build_chat_model(llm, "native" if decision.mode == AgentMode.NATIVE else "json_plan")
    message = model.bind_tools(ALL_TOOLS).invoke(
        [
            SystemMessage("You can call tools. Call exactly one tool."),
            HumanMessage("Look up the skills of employee EMP-001."),
        ]
    )
    calls = [(c["name"], c["args"]) for c in message.tool_calls]
    print(f"4. tool calling ({decision.mode.value}): {calls}")
    return 0 if calls else 1


def _cmd_export_graph(args: argparse.Namespace) -> int:
    target = Path(args.output_path) if args.output_path else ROOT / "agent_graph.mmd"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mermaid(), encoding="utf-8")
    print(f"Wrote {target}")
    return 0


def _cmd_hash_key(args: argparse.Namespace) -> int:
    print(hashlib.sha256(args.api_key.encode()).hexdigest())
    return 0


def _cmd_prune_checkpoints(_: argparse.Namespace) -> int:
    settings = get_settings()
    store = CheckpointStore(settings)
    try:
        uow = SqlAlchemyUnitOfWorkFactory(create_session_factory(create_db_engine(settings)))
        n = store.prune(uow)
    finally:
        store.close()
    print(f"Pruned checkpoint threads of {n} run(s) older than {settings.checkpoint_retention_days} days.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SprintMatch AI CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Apply Alembic migrations and initialize checkpoint tables")

    p_seed = sub.add_parser("seed", help="Seed synthetic demo dataset")
    p_seed.add_argument("--reset", action="store_true", help="Truncate application data first")
    p_seed.add_argument("--reference-date", default=None)
    p_seed.add_argument("--seed", type=int, default=None)

    p_reset = sub.add_parser("reset", help="Reset and re-seed demo dataset")
    p_reset.add_argument("--reference-date", default=None)
    p_reset.add_argument("--seed", type=int, default=None)

    sub.add_parser("validate", help="Run consistency checks on the dataset")

    p_demo = sub.add_parser("demo", help="Run one recommendation through the compiled LangGraph agent")
    p_demo.add_argument("task_code", nargs="?", default="TASK-SCN-01")
    p_demo.add_argument("--as-of", default=None)
    p_demo.add_argument("--company", default="NDS")
    p_demo.add_argument("--stream", action="store_true")

    sub.add_parser("check-llm", help="Verify LLM provider reachability and tool-calling capabilities")

    p_graph = sub.add_parser("export-graph", help="Export LangGraph Mermaid topology")
    p_graph.add_argument("output_path", nargs="?", default=None)

    p_hash = sub.add_parser("hash-key", help="Print SHA-256 digest of an API key")
    p_hash.add_argument("api_key")

    sub.add_parser("prune-checkpoints", help="Delete expired LangGraph checkpoint threads")

    args = parser.parse_args(argv)
    dispatch = {
        "init-db": _cmd_init_db,
        "seed": _cmd_seed,
        "reset": _cmd_reset,
        "validate": _cmd_validate,
        "demo": _cmd_demo,
        "check-llm": _cmd_check_llm,
        "export-graph": _cmd_export_graph,
        "hash-key": _cmd_hash_key,
        "prune-checkpoints": _cmd_prune_checkpoints,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

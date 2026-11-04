# Project status — SprintMatch AI

_Last updated: 2026-11-03_

## Summary

The application runs end to end: PostgreSQL (Alembic) → FastAPI (async) → services → compiled LangGraph agent
(16 nodes, ToolNode with 20 read-only PostgreSQL-backed tools, LLM research agent, PostgreSQL checkpointing) →
deterministic recommendation engine → grounded explanation → persisted, auditable result. Verified with 180
automated tests, a live server (uvicorn + `AsyncPostgresSaver`) and live runs against Groq
(`openai/gpt-oss-120b`). Details and evidence: [AGENT_IMPLEMENTATION_REPORT.md](AGENT_IMPLEMENTATION_REPORT.md).

## Definition of Done (prompt Part 27)

Legend: ✅ verified (evidence) · ⚠️ partially verified · ❌ not verified.

**Database**
- ⚠️ PostgreSQL starts — local PostgreSQL 18 cluster ✅; Docker Compose service not executed (no Docker here)
- ✅ Schema via Alembic, ✅ migrations from empty (drift test), ✅ demo data seeds (27/27 checks), ✅ relationships
  valid, ✅ employee profiles and ✅ workforce data retrievable (repository + tool tests)

**Backend** — ✅ FastAPI starts (uvicorn, Windows selector loop) · ✅ OpenAPI (`/docs` 200) · ✅ repositories ·
✅ Employee 360 · ✅ workforce calculations · ✅ eligibility (incl. certifications) · ✅ recommendation engine

**LLM** — ✅ provider abstraction · ✅ cloud adapter (LIVE Groq) · ⚠️ local adapter (Ollama: mocked HTTP only) ·
✅ configuration · ✅ capability detection (LIVE probe) · ✅ structured-output validation · ✅ timeout/retry
(incl. Retry-After, unit + LIVE)

**LangGraph agent** — ✅ state schema · ✅ all required nodes · ✅ edges · ✅ conditional routing · ✅ compiled
StateGraph · ✅ invoke · ✅ ainvoke · ✅ ToolNode registered · ✅ tools registered · ✅ every tool tested ·
✅ tool calls execute · ✅ results return to agent · ✅ multiple tool calls · ✅ loop limits · ✅ invalid tool
requests handled · ✅ database services connected · ✅ engine connected · ✅ explanation node · ✅ final
validation node · ✅ persistence node · ✅ checkpointing (PostgreSQL, resume) · ✅ failure paths · ✅ deterministic
fallback

**End-to-end** — ✅ receives task · ✅ analyzes requirements · ✅ retrieves employee information · ✅ executes
DB-backed tools (scripted and LIVE) · ✅ availability · ✅ eligibility · ✅ ranking · ✅ grounded explanation ·
✅ validated · ✅ stored in PostgreSQL · ✅ returned by the API · ✅ never auto-assigns (`auto_assigned: false`)

**Testing** — ✅ unit · ✅ tool · ✅ node · ✅ routing · ✅ graph execution · ✅ end-to-end · ✅ database
integration · ✅ no unresolved critical errors

## Test results (2026-11-03)

```
uv run pytest                    -> 180 passed, 2 skipped (live tests are opt-in)
  tests/unit 85 · tests/integration 14 · tests/tools 29 · tests/agent 37 · tests/api 15
RUN_LIVE_LLM_TESTS=1 uv run pytest tests/live -m live   -> 2 passed (Groq)
uv run ruff check .              -> All checks passed
uv run mypy app scripts          -> Success: no issues found in 130 source files
scripts.validate_demo_data       -> 27/27 checks passed (+2 informational data-gap reports)
```

## Database migrations

| Revision | Description |
|---|---|
| `003de51576d2` | initial schema – 29 tables |
| `1aff0ff902d2` (head) | agent runtime: `task_dependencies`, `performance_evaluations`, `performance_metrics`, `agent_tool_executions`, `agent_run_errors`, run metadata (thread, idempotency, agent mode, research, usage, error category, resumable, retry link), `tasks.required_certifications`, node-log timestamps/tokens, schema `agent_checkpoints` |

## Implemented agent components

* Nodes (16): validate_request, load_task_context, task_analysis, workforce_research_agent, tool_execution
  (ToolNode), load_authoritative_data, build_employee_context, calculate_availability, calculate_workload,
  eligibility_filter, performance_analysis, recommendation_engine, explanation_agent, final_validation,
  failure_node, save_recommendation.
* Tools (20): see AGENT_IMPLEMENTATION_REPORT.md §5.
* Modes: native tool calling / JSON tool plan / disabled (auto-detected).
* API: `/api/v1/agent/recommend` (+ `/stream`), `/agent/runs/{id}` (+ logs, tools, errors, candidates,
  checkpoints, retry), `/api/v1/health/llm`, `/api/v1/health/agent`, `/evaluation/metrics`, idempotency keys.

## Architecture decisions (this phase)

1. The LLM research agent is advisory; ranking/eligibility only come from deterministic nodes that reload
   authoritative data from PostgreSQL.
2. One provider layer for everything: `ProviderChatModel` (LangChain `BaseChatModel`) wraps the existing
   adapters, so retries, redaction, Retry-After and token accounting are shared; JSON tool plans reuse the same
   ToolNode.
3. Checkpointed state is JSON-only; heavy employee data lives in a non-checkpointed workspace rebuilt after resume.
4. Retryable failures use LangGraph `RetryPolicy` and stop the run at the failing node (resumable); others go
   through `failure_node`. Resumed runs upsert the same record.
5. Tools run in READ ONLY transactions with `statement_timeout`; no tool can write.
6. Prompt-injection hardening: instruction-like sentences in task text never become requirements.

## Local environment notes

* Private PostgreSQL 18 cluster in `.local/pgdata` on port **55432** (git-ignored). Start:
  `pg_ctl -D .local/pgdata -o "-p 55432" start`.
* `.env` (git-ignored) contains a Groq test key (`LLM_PROVIDER=openai_compatible`, `openai/gpt-oss-120b`,
  `EXPLANATION_MODE=llm`). **Rotate that key** – it was shared in chat.
* Windows API start: `uv run uvicorn app.main:create_app --factory --loop app.core.event_loop:selector_loop_factory`.
* Dev database seeded for reference date 2026-11-03; nothing committed to git yet.

## Known issues / limitations

1. Docker Compose / Dockerfile not executed here (no Docker).
2. Ollama adapter verified with mocked HTTP only.
3. Groq free tier (8k tokens/min): deep research rounds can hit 429; handled by Retry-After and the deterministic
   fallback; recommended `AGENT_MAX_TOOL_ROUNDS=3`, `AGENT_MAX_TOOL_RESULT_CHARS=3500` on that tier.
4. Research summaries are grounded on employee codes, not on every sentence (advisory text).
5. In-memory rate limiter (per process); scoring weights are expert defaults (not calibrated on real data).
6. `performance_evaluations` / `performance_metrics` are informational only (not scoring inputs, by design).

## Next development tasks

1. Run `docker compose up` on a Docker host; run the live tests against a local Ollama model.
2. Manager feedback (accept/reject with reason) on recommendations, feeding evaluation metrics.
3. CI pipeline (ruff, mypy, pytest with a PostgreSQL service; live tests on a schedule).
4. Odoo 17 adapter per `docs/ODOO_INTEGRATION.md`.

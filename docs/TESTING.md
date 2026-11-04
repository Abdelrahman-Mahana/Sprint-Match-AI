# Testing

```bash
uv run pytest                 # everything (integration tests skip if TEST_DATABASE_URL is unreachable)
uv run pytest tests/unit      # pure unit tests, no database, no network, no LLM
uv run pytest -m integration  # PostgreSQL-backed tests only
uv run pytest tests/tools tests/agent   # agent tools, nodes, graph execution, scenarios
RUN_LIVE_LLM_TESTS=1 uv run pytest tests/live -m live   # opt-in: the real provider from .env
```

Integration, agent and API tests use `TEST_DATABASE_URL`. The session fixture drops the `public` schema,
**builds it exclusively with Alembic** and seeds the demo data for the fixed reference date `2026-11-03`
(`as_of = 2026-11-03T10:00+02:00`), so results do not depend on today's date. Migration tests use a separate
scratch database `<test_db>_migrations`. Tests use `CHECKPOINTER=memory` except the PostgreSQL checkpointer tests,
which use the official `PostgresSaver` / `AsyncPostgresSaver` on the test database. No default test calls a real
LLM: adapters are tested with `httpx.MockTransport`; the agent with a **scripted chat model** (`MockLLMProvider`
`chat_responses`) whose tool calls go through the real `ProviderChatModel`, conditional routing, `ToolNode`,
PostgreSQL-backed tools and back to the agent. Live provider tests are separate (`tests/live`, marker `live`).

| Level | Files | Covers |
|---|---|---|
| Unit | `tests/unit/test_workforce_capacity.py` | schedules, breaks, holidays, approved/pending/overlapping leave, remaining-effort precedence, no timesheet double counting, proration, overdue work, commitments, presence, classification, DST |
| Unit | `test_windows.py` | window presets, company calendar, clamping, validation |
| Unit | `test_recommendation_engine.py` | skill curve, verification/staleness, unrelated skills, every exclusion reason, capacity vs. having tasks, missing-data priors & coverage, external delays, rework/rejection, shrinkage, experience, complexity fit, N/A weight redistribution, tie-breaking, determinism, NO_SUITABLE/DEADLINE_INFEASIBLE, PARTIAL_DATA, config validation & fingerprint |
| Unit | `test_llm_providers.py` | OpenAI-compatible & Ollama request shapes, auth header, structured-output modes, bounded retries, timeout/4xx classification, secret redaction in errors, repair round, health checks, factory selection, **provider independence** |
| Unit | `test_task_analysis_and_explanation.py` | manager priority, unknown skills, LLM estimate policy, text fallback, PROVIDER_UNAVAILABLE, prompt delimiting, deterministic explanation, grounding check |
| Unit | `test_security_and_tools.py` | log redaction, API keys, roles, rate limit, closed tool registry, tool input validation, tool permissions |
| Integration | `tests/integration/test_migrations_and_seed.py` | migrations from empty + no model drift, downgrade/upgrade, seeding guards, reproducible reseeding, date-relative generation, dataset targets, 21 consistency checks |
| Integration | `test_repositories.py` | Employee 360 from PostgreSQL, verified vs self-declared, no sensitive fields, workforce data (logged hours, assignee counts), tenant isolation, transaction rollback, DB-unavailable translation |
| Agent / E2E | `tests/agent/test_recommendation_scenarios.py` | the 15 required scenarios (below), persistence, no auto-assignment, determinism |
| Tools | `tests/tools/test_agent_tools.py` | every one of the 20 tools executed through the ToolNode on PostgreSQL; invalid/missing/extra/malformed arguments, unknown employee/task, empty results, database errors, timeout, unknown tool, duplicates, call budget, profile-expansion limit, result bounding, read-only sessions, logging, async ToolNode |
| Agent nodes | `tests/agent/test_nodes.py` | each node invoked on its own with `Runtime(context=...)`, workspace hydration after resume, failure node, every routing function |
| Graph | `tests/agent/test_graph_execution.py` | compilation/structure, LLM tool call -> routing -> ToolNode -> DB result -> agent -> recommendation, multiple calls, duplicates, unknown/malformed tools, bounded loop, call budget, research LLM failure fallback, JSON tool-plan mode, disabled mode, `ainvoke`, `astream`, `stream`, final-validation failure path, PostgreSQL checkpoint recovery (resume same run), retry of completed FAILED run, checkpoint minimality, inspection and pruning, async Postgres checkpointer |
| Agent API | `tests/api/test_agent_api.py` | `/agent/recommend`, run/logs/tools/errors/candidates/checkpoints, retry 409/404, idempotency, SSE stream, health endpoints, permissions, metrics |
| Live | `tests/live/test_live_llm.py` | opt-in: real provider capabilities and a full agent run with real tool calls |
| API | `tests/api/test_api.py` | health/ready, 401/403, all routes, pagination/filtering, 404/409/422, recommendation lifecycle, outcomes, OpenAPI |

## Required end-to-end scenarios

| # | Scenario | Demo data | Expected |
|---|---|---|---|
| 1 | Best-qualified employee available | TASK-SCN-01, Ahmed (EMP-001) | SUCCESS, Ahmed primary |
| 2 | Best-qualified employee overloaded | Karim (EMP-002) | BUSY, INSUFFICIENT_AVAILABLE_CAPACITY |
| 3 | Present but no capacity | Youssef (EMP-005) | presence PRESENT, BUSY, excluded |
| 4 | Qualified but absent in target period | Salma (EMP-004) | ON_LEAVE_DURING_WINDOW |
| 5 | Absent now, available later | Hana (EMP-006) | excluded for today, eligible for next 3 days |
| 6 | Missing mandatory skill | Nour (EMP-003) | MANDATORY_SKILL_MISSING |
| 7 | Incomplete performance history | Laila (EMP-008) | eligible, quality prior 60, lower coverage/confidence |
| 8 | Two similar candidates | TASK-SCN-03, Mariam/Omar | CLOSE_CANDIDATES, deterministic order |
| 9 | All candidates unavailable | TASK-SCN-04, Mostafa on leave | NO_SUITABLE_CANDIDATE |
| 10 | Incomplete requirements | TASK-SCN-05 (no skills), TASK-SCN-06 (no estimate) | NEEDS_INPUT |
| 11 | PostgreSQL unavailable | unreachable DB | FAILED, DATABASE_UNAVAILABLE, 1 bounded retry, not persisted |
| 12 | LLM unavailable | MockLLM raising | structured task SUCCESS; vague task PROVIDER_UNAVAILABLE |
| 13 | LLM invalid structured output | MockLLM garbage | rejected after repair, text-match fallback |
| 14 | Insufficient supporting evidence | Laila-only task, strict policy | PARTIAL_DATA |
| 15 | Availability changes before revalidation | leave inserted for Ahmed | still_valid = false, primary changed |

Also covered: outdated/unverified skills (Mahmoud, EMP-009), strong performer with poor fit (Rania, EMP-010),
strong experience with limited availability (Tarek, EMP-007), deadline shorter than effort (TASK-SCN-07,
DEADLINE_INFEASIBLE), unknown remaining effort on a shared task (EMP-014/015), estimated remaining effort
(EMP-016), pending leave (EMP-020), missing schedule (EMP-030), inactive employee (EMP-025), tenant isolation
(Delta Labs), prompt-injection attempt in task text.

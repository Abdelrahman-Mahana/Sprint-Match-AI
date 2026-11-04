# Architecture

## Layers and dependency rule

```text
            api (FastAPI routers, auth)          scripts (CLI)
                         │
                   application  ──────────────►  agents (LangGraph graph, nodes, tools)
                         │                              │
                         ▼                              ▼
                   intelligence (deterministic engines, task analysis, prompts)
                         │
                         ▼
                      domain  (entities, enums, policies, repository Protocols)
                         ▲
                         │ implements
                  infrastructure (PostgreSQL repositories, LLM adapters, demo data)
```

* `domain` depends on nothing but Pydantic. It defines **what** the system needs from a data source
  (`app/domain/interfaces.py`: `EmployeeRepository`, `TaskRepository`, `WorkforceRepository`,
  `PerformanceRepository`, `RecommendationRepository`, `AuditRepository`, `UnitOfWork`).
* `intelligence` contains all business computation as pure functions/classes over domain objects. It never
  touches SQLAlchemy, FastAPI or LangGraph. The only infrastructure import is the abstract
  `LLMProvider` interface (`app/infrastructure/llm/base.py`, no adapters).
* `agents` orchestrates: graph nodes call application services (`RecommendationPipeline`, task/workforce
  services) through short-lived units of work; the LLM research agent reaches data only through the 20 approved
  read-only tools (`app/agents/tools/`), executed by LangGraph's `ToolNode` under `ToolCallPolicy`, each in a
  READ ONLY transaction. Nodes never see SQLAlchemy models.
* `infrastructure` implements the protocols. Replacing PostgreSQL by Odoo means adding another implementation;
  engines, scoring, policies and the graph stay unchanged.
* `application/container.py` is the composition root: it wires settings, the unit-of-work factory, the LLM
  provider and the scoring configuration.

## Request flow (recommendation)

`POST /api/v1/agent/recommend` (or `/recommendations`) → API-key auth (role ≥ manager, tenant from key) →
idempotency check → `RecommendationService.acreate` → `AgentRuntime` (compiled graph per checkpointer, resolved
agent mode, chat model) → `graph.ainvoke(state, {thread_id: run id}, context=AgentContext)` → validate → task
context → analysis → research agent ⇄ ToolNode → authoritative data → availability → workload → eligibility →
performance → ranking → explanation → final validation → persistence (run, candidates, components, node logs,
tool executions, errors, audit) → response. See [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md).

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Deterministic engines own all critical calculations; the LLM never computes hours, eligibility or scores | Correctness, reproducibility, testability without a paid API |
| 2 | Single orchestrator LangGraph graph with fixed nodes; an LLM research agent with typed, read-only, policy-checked tools inside it; ranking only from deterministic nodes | Controlled behaviour; the LLM adds evidence gathering without decision power |
| 3 | LLM adapters over raw `httpx` (no vendor SDK) behind `LLMProvider` | Provider independence, no SDK coupling, easy mocking with `httpx.MockTransport` |
| 4 | Synchronous data access (SQLAlchemy 2 sync + psycopg 3); async agent endpoints (`ainvoke`/`astream`, `AsyncPostgresSaver`) with sync nodes run in LangGraph's executor | Robust data layer; non-blocking API |
| 4b | LangChain `BaseChatModel` adapter (`ProviderChatModel`) over the own provider layer | Real `bind_tools`/ToolNode semantics while keeping retries, redaction and accounting in one place |
| 4c | JSON-only checkpointed state, heavy data in a non-checkpointed workspace | Minimal, safe checkpoints; resume reloads authoritative data |
| 5 | Repository Protocols + Unit of Work; explicit `commit()` in services/routes, rollback by default | Replaceable data source, explicit transactions |
| 6 | Enums stored as `VARCHAR + CHECK` (non-native) | Migration-friendly; validated values |
| 7 | UUID primary keys; demo data uses `uuid5` of natural keys | Reproducible seeding with stable IDs |
| 8 | Tenant boundary = `companies`; every top-level entity has `company_id`; API keys are bound to a company | Multi-company demos and isolation from day one without extra complexity |
| 9 | Weekly capacity is derived from `work_schedule_days`, not stored on employees; schedule times use the employee timezone | Single source of truth |
| 10 | Employee-level `task_assignments.remaining_estimated_hours` is authoritative for workload; task-level remaining is used only for a sole assignee | Avoid charging a shared task's full effort to every assignee |
| 11 | Timesheets are never subtracted from capacity | Logged time is historical consumption, not remaining work |
| 12 | Missing evidence ⇒ documented neutral prior + reduced coverage/confidence (never zero) | Fairness and honest uncertainty |
| 13 | Every run stores `algorithm_version`, `scoring_config_fingerprint`, `prompt_version`, `as_of` and an immutable response snapshot | Historical results stay explainable after policy changes |
| 14 | `as_of` is part of the request | Reproducible evaluation; revalidation uses the current time |
| 15 | No auto-assignment: the API has no endpoint that writes assignments | Human-in-the-loop by design |
| 16 | Prompts live in `app/intelligence/prompts.py` and are versioned | Prompts are business knowledge used by the analyser, not by the graph |
| 17 | No ML model in v1 | No real historical data; synthetic data must not be used to claim accuracy (see roadmap) |

## Security & governance

* API keys (SHA-256 digests in config, constant-time comparison), roles `viewer < manager < admin`.
* Per-principal rate limit on expensive endpoints (in-memory; use a shared store when scaling out).
* Parameterized SQL only (SQLAlchemy); the LLM has no database or shell access; tools are read-only.
* Task text is untrusted: delimited in prompts, LLM output schema-validated, skills resolved against the
  catalog, LLM "mandatory" skills require a verbatim supporting quote, manager values always win.
* Data minimization: no gender, religion, ethnicity, age, birth date, medical data, marital status or salary is
  stored or used. Leave types are stored for HR completeness but never used in scoring or explanations.
* Structured JSON logs with request IDs; secrets and DSN passwords are redacted; node logs store only sanitized
  summaries (counts/statuses), never personal data or chain-of-thought.

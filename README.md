# SprintMatch AI

AI-assisted, explainable **employee–task recommendation** for Scrum teams.

Given a task, SprintMatch AI analyses its requirements, builds task-specific evidence for every employee
(verified skills, relevant experience, reviewed task outcomes, schedules, leaves, attendance, current workload)
and recommends **one primary employee** plus ranked alternatives — or explicitly reports that nobody is
suitable. It **never assigns work automatically**; the project manager decides.

Key properties:

* **Deterministic core.** Capacity, eligibility, scoring and ranking are pure Python with versioned,
  configurable policies. Same data + same task + same `as_of` ⇒ same result. No LLM is required.
* **LLM-provider independent.** Optional LLM use (natural-language task analysis, explanation wording) goes
  through a provider interface with OpenAI-compatible, Ollama and Mock adapters selected by configuration.
* **Operational LangGraph agent.** A compiled `StateGraph` with 16 nodes, a `ToolNode` executing 20 read-only,
  PostgreSQL-backed tools for an LLM *workforce research agent* (native tool calling or validated JSON tool plans),
  conditional routing, bounded tool loops, retry policies, PostgreSQL checkpointing with resume, and full
  execution audit. The research agent informs; the deterministic engine decides the ranking.
* **Explainable.** Every candidate gets 7 score components with evidence; every excluded employee gets explicit
  exclusion reasons; data coverage and confidence are reported; missing evidence is never treated as zero.
* **Odoo-ready, Odoo-free.** Business logic depends only on repository protocols; PostgreSQL implements them
  today, an Odoo 17 adapter can later (see [docs/ODOO_INTEGRATION.md](docs/ODOO_INTEGRATION.md)).

| Document | Content |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, dependency rules, architecture decisions |
| [docs/DATABASE.md](docs/DATABASE.md) | Schema, relationships, constraints, tenancy |
| [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md) | LangGraph workflow, state, tools, failure handling |
| [AGENT_IMPLEMENTATION_REPORT.md](AGENT_IMPLEMENTATION_REPORT.md) | Graph, nodes, edges, tools, checkpointing, test evidence |
| [docs/SCORING.md](docs/SCORING.md) | Workforce formulas, eligibility, scoring, missing-data policy |
| [docs/API.md](docs/API.md) | REST API, authentication, examples |
| [docs/TESTING.md](docs/TESTING.md) | Test strategy and scenario matrix |
| [docs/DEVELOPMENT_ROADMAP.md](docs/DEVELOPMENT_ROADMAP.md) | Milestones |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | Current state, test results, known issues |

## Requirements

* Python 3.12 and [uv](https://docs.astral.sh/uv/)
* PostgreSQL 16+ (tested with 18) — via Docker Compose **or** a local installation

## Quick start with Docker Compose

```bash
cp .env.example .env            # set POSTGRES_PASSWORD and matching DATABASE_URL/TEST_DATABASE_URL
docker compose up -d db
uv sync
uv run python -m scripts.init_db                 # alembic upgrade head + LangGraph checkpoint tables
uv run python -m scripts.seed_demo_data          # reproducible demo dataset
uv run python -m scripts.validate_demo_data      # consistency checks
docker compose up api                            # or: uv run uvicorn app.main:create_app --factory --reload
```

Open <http://localhost:8000/docs> (Swagger). Optional pgAdmin: `docker compose --profile tools up pgadmin`.

## Local PostgreSQL without Docker

Any PostgreSQL works; point `DATABASE_URL` / `TEST_DATABASE_URL` at it. A private cluster can be created with the
PostgreSQL binaries (this is how the project was developed on Windows):

```powershell
$bin = "C:\Program Files\PostgreSQL\18\bin"
& "$bin\initdb.exe" -D .local\pgdata -U sprintmatch --auth=scram-sha-256 --pwprompt -E UTF8
& "$bin\pg_ctl.exe" -D .local\pgdata -o "-p 55432" -l .local\pg.log start
& "$bin\psql.exe" -h localhost -p 55432 -U sprintmatch -d postgres -c "CREATE DATABASE sprintmatch;" -c "CREATE DATABASE sprintmatch_test;"
```

`.local/` is git-ignored.

## Commands

| Task | Command |
|---|---|
| Install dependencies | `uv sync` |
| Run migrations | `uv run python -m scripts.init_db` (Alembic upgrade + checkpoint tables) |
| New migration | `uv run alembic revision --autogenerate -m "..."` |
| Seed demo data | `uv run python -m scripts.seed_demo_data [--reference-date 2026-11-03] [--seed N]` |
| Reset demo data | `uv run python -m scripts.reset_demo_data` |
| Validate data | `uv run python -m scripts.validate_demo_data` |
| Run API (Linux/macOS/Docker) | `uv run uvicorn app.main:create_app --factory --reload` |
| Run API (Windows) | `uv run uvicorn app.main:create_app --factory --loop app.core.event_loop:selector_loop_factory` (psycopg async needs a selector loop) |
| Test the LLM provider | `uv run python -m scripts.test_llm_connection` (health, capability probe, structured output, one tool call) |
| Run the agent once | `uv run python -m scripts.run_agent_demo TASK-SCN-01 --as-of 2026-11-03T10:00:00+02:00 [--stream]` |
| Graph diagram | `uv run python -m scripts.export_graph` (writes `docs/agent_graph.mmd`) |
| Prune checkpoints | `uv run python -m scripts.prune_checkpoints` |
| Live LLM tests (opt-in) | `RUN_LIVE_LLM_TESTS=1 uv run pytest tests/live -m live` |
| All tests | `uv run pytest` |
| Unit tests only (no DB) | `uv run pytest tests/unit` |
| Lint / format / types | `uv run ruff check .` · `uv run ruff format .` · `uv run mypy app` |
| Hash an API key | `uv run python scripts/hash_api_key.py "<key>"` |

Seeding refuses to run with `ENVIRONMENT=production` and refuses to overwrite data without `--reset`.

## Configuration

All configuration is environment-based (see [.env.example](.env.example)). Important variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL`, `TEST_DATABASE_URL` | PostgreSQL connection (SQLAlchemy URL, `postgresql+psycopg://...`) |
| `LLM_PROVIDER` | `none` (default) · `mock` · `openai_compatible` · `ollama` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Provider endpoint, secret, model |
| `LLM_TEMPERATURE`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | Call behaviour |
| `LLM_STRUCTURED_OUTPUT_MODE` | `json_schema` or `json_object` |
| `EXPLANATION_MODE` | `deterministic` (default) or `llm` (grounding-checked, deterministic fallback) |
| `AGENT_TOOL_MODE` | `auto` (default: native tool calling if supported, else JSON tool plan) · `native` · `json_plan` · `disabled` |
| `AGENT_MAX_TOOL_ROUNDS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_TOOL_TIMEOUT_SECONDS` | Tool-loop bounds (5, 20, 10 s) |
| `AGENT_MAX_TOOL_RESULT_ITEMS/CHARS`, `AGENT_MAX_PROFILE_EXPANSIONS` | Result size and breadth bounds |
| `CHECKPOINTER`, `CHECKPOINT_SCHEMA`, `CHECKPOINT_RETENTION_DAYS` | `postgres` (default) · `memory` · `none`; schema; retention |
| `LLM_MAX_RETRY_WAIT_SECONDS` | Cap for provider `Retry-After` on HTTP 429 |
| `API_KEYS` | JSON list of `{name, role, company_code, key_sha256}` — only digests are configured |
| `ACCEPT_LLM_EFFORT_ESTIMATES` | `false` (default): LLM effort estimates are suggestions only |
| `DEMO_REFERENCE_DATE`, `DEMO_SEED` | Demo data anchor date (default: today) and seed |

Development keys in `.env.example`: `dev-manager-key` (manager) and `dev-viewer-key` (viewer). Replace them for
any shared environment. Secrets are never stored in code or the database and are redacted from logs.

## Example

```bash
curl -X POST http://localhost:8000/api/v1/agent/recommend \
  -H "X-API-Key: dev-manager-key" -H "Content-Type: application/json" \
  -d '{"task_code": "TASK-SCN-01", "as_of": "2026-11-03T10:00:00+02:00"}'
```

With demo data seeded for reference date 2026-11-03 this recommends **Ahmed Hassan (EMP-001)** and lists, for
example, Karim Mostafa as excluded for `INSUFFICIENT_AVAILABLE_CAPACITY` and Salma Farouk for
`ON_LEAVE_DURING_WINDOW`. Demo scenarios are listed in [docs/TESTING.md](docs/TESTING.md).

## Repository layout

```text
app/
  core/            config, structured logging + redaction, security, exceptions
  domain/          entities, enums, repository protocols (interfaces.py), versioned policies
  intelligence/    deterministic engines: workforce (calendar, capacity, windows), skills, performance,
                   recommendation (eligibility, criteria, engine, explanation), task analysis, prompts
  application/     use-case services + composition root (container.py)
  agents/          LangGraph: state, context, nodes/, tools/ (20 tools + ToolNode policy), routing, builder,
                   checkpointer, runtime, prompts
  infrastructure/  database (models, repositories, unit of work), llm adapters, demo data generator
  api/             FastAPI routers (v1) and dependencies
  schemas/         API contracts
alembic/           migrations          scripts/  CLI commands          tests/  unit, integration, agent, api
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `psycopg cannot use the 'ProactorEventLoop'` | Windows: start uvicorn with `--loop app.core.event_loop:selector_loop_factory` |
| Research shows `RESEARCH_AGENT_UNAVAILABLE:PROVIDER_UNAVAILABLE` | Provider down or rate limited (e.g. free tiers with a tokens-per-minute cap). The deterministic recommendation is still produced; lower `AGENT_MAX_TOOL_ROUNDS` / `AGENT_MAX_TOOL_RESULT_CHARS` or raise `LLM_MAX_RETRIES` |
| `agent_mode: disabled` | No LLM, or the model supports neither tool calling nor structured output (`GET /api/v1/health/agent` shows the reason) |
| Run FAILED with `resumable: true` | Retryable outage (database/checkpoints); fix it and call `POST /api/v1/agent/runs/{id}/retry` |
| Integration tests skipped | `TEST_DATABASE_URL` unreachable |

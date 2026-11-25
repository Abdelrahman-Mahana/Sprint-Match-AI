# SprintMatch AI

Explainable employee–task recommendation platform for engineering and Scrum teams.

Given a task and execution window, SprintMatch AI analyses technical requirements, gathers authoritative workforce records from PostgreSQL (verified skills, historical task outcomes, work schedules, approved leaves, attendance, and active commitments), filters out ineligible staff against hard business constraints, and ranks eligible candidates deterministically — or explicitly reports when no candidate fits. Recommendations are strictly advisory; the system never assigns work automatically without manager confirmation.

## Key Architecture Properties

- **Deterministic Core:** Availability, workload capacity, eligibility gates, and 7-criterion weighted scoring live in pure Python (`app/intelligence/`) under versioned policies (`app/domain/policies.py`). Same inputs always yield identical rankings.
- **Zero-Hallucination Dual-Plane Agent:** A compiled LangGraph `StateGraph` (`app/agents/graph.py`) runs an optional LLM workforce research loop over read-only database tools (`app/agents/tools/`), followed by direct PostgreSQL ingestion, deterministic evaluation, and independent final invariant checks before saving.
- **Provider-Independent LLM Integration:** Supports OpenAI-compatible APIs, Ollama, Mock, or `none` (pure deterministic mode) via `app/infrastructure/llm/`.
- **Adaptive Schema Discovery:** Connects natively to the bundled PostgreSQL schema or adapts dynamically to external SQL databases via `app/infrastructure/schema_discovery/` and `app/infrastructure/adaptive/`.

## Requirements

- **Python 3.12+** (managed via `uv` or standard virtualenv)
- **PostgreSQL 16+** (Docker Compose or local PostgreSQL instance)

## Quick Start

### 1. Environment & Database Setup

```bash
cp .env.example .env            # Configure DATABASE_URL, TEST_DATABASE_URL, and API_KEYS
docker compose up -d db         # Or start a local PostgreSQL 16+ cluster
uv sync
uv run python -m scripts.cli init-db
uv run python -m scripts.cli seed
uv run python -m scripts.cli validate
```

### 2. Run the API Server

```bash
# Linux / macOS / Docker
uv run uvicorn app.main:create_app --factory --reload

# Windows (psycopg async checkpointer requires selector event loop)
uv run uvicorn app.main:create_app --factory --loop app.core:selector_loop_factory
```

Open `http://localhost:8000/docs` for Swagger API documentation or `http://localhost:8000/` for the web UI.

## Repository Layout

```text
app/
  main.py            FastAPI factory, lifespan, middleware, and router wiring
  core.py            Settings (.env), logging with secret redaction, API key auth, exceptions, Windows event loop
  domain.py          Domain enums, error categories, immutable entities, policies, and repository protocols
  schemas.py         Pydantic v2 API request/response and agent tool schemas
  api/               REST routers & dependencies (deps, agent, tasks, workforce, database)
  application/       Services & DI container (services), agent read-only queries (queries), agent runner/SSE (runner)
  agents/            LangGraph StateGraph (graph), state/checkpointer (state), nodes, and read-only tools/policy
  intelligence/      Deterministic engines: analyzer, capacity, scoring
  infrastructure/    SQLAlchemy models (orm), repositories, discovery, adaptive DB adapter, and LLM providers
  static/            Self-contained web UI (index.html) served directly by FastAPI
alembic/             PostgreSQL schema migrations
scripts/             Unified CLI (cli.py) and deterministic demo dataset catalog/generator/seeder (demo_data.py)
tests/               Flat unit, integration, API, agent, tool-policy, and opt-in live LLM tests
```

## Useful Commands

| Task | Command |
|---|---|
| Initialize / migrate DB | `uv run python -m scripts.cli init-db` |
| Seed demo dataset | `uv run python -m scripts.cli seed [--reference-date YYYY-MM-DD] [--seed N]` |
| Reset demo dataset | `uv run python -m scripts.cli reset [--reference-date YYYY-MM-DD] [--seed N]` |
| Validate database consistency | `uv run python -m scripts.cli validate` |
| Run CLI agent demo | `uv run python -m scripts.cli demo TASK-SCN-01 [--stream]` |
| Export Mermaid graph diagram | `uv run python -m scripts.cli export-graph` |
| Test LLM connectivity | `uv run python -m scripts.cli check-llm` |
| Prune expired checkpoints | `uv run python -m scripts.cli prune-checkpoints` |
| Hash an API key | `uv run python -m scripts.cli hash-key "<key>"` |
| Run all offline tests | `uv run pytest` |
| Run pure unit tests only | `uv run pytest -m "not integration"` |
| Run opt-in live LLM tests | `RUN_LIVE_LLM_TESTS=1 uv run pytest tests/test_live.py -m live` |
| Lint & static type check | `uv run ruff check app scripts tests && uv run mypy app` |

## Key Environment Variables

See `.env.example` for full defaults:

| Variable | Description |
|---|---|
| `DATABASE_URL`, `TEST_DATABASE_URL` | PostgreSQL connection URLs (`postgresql+psycopg://...`) |
| `DATABASE_ADAPTER` | `auto` (default) · `native` · `adaptive` |
| `LLM_PROVIDER` | `none` (default) · `mock` · `openai_compatible` · `ollama` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Provider endpoint, API secret, and model identifier |
| `EXPLANATION_MODE` | `deterministic` (default) or `llm` (evidence-grounded with deterministic fallback) |
| `AGENT_TOOL_MODE` | `auto` (default) · `native` · `json_plan` · `disabled` |
| `AGENT_MAX_TOOL_ROUNDS`, `AGENT_MAX_TOOL_CALLS` | Bounded research loop limits (defaults: `5`, `20`) |
| `CHECKPOINTER`, `CHECKPOINT_SCHEMA`, `CHECKPOINT_RETENTION_DAYS` | `postgres` (default) · `memory` · `none` |
| `API_KEYS` | JSON array of `{name, role, company_code, key_sha256}` SHA-256 digests |

## Example API Request

```bash
curl -X POST http://localhost:8000/api/v1/agent/recommend \
  -H "X-API-Key: dev-manager-key" \
  -H "Content-Type: application/json" \
  -d '{"task_code": "TASK-SCN-01"}'
```

## Development Notes

- **Windows Event Loop:** Start `uvicorn` with `--loop app.core:selector_loop_factory` so Psycopg's async checkpoint pool runs on `SelectorEventLoop`.
- **LLM Fallback:** If an external LLM provider rate-limits or goes offline, `workforce_research_agent` degrades gracefully and the deterministic evaluation pipeline still produces an auditable recommendation.

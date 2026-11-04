# Development roadmap

| Milestone | Scope | Status |
|---|---|---|
| 0 Foundation | uv project with pinned deps, config, logging, Docker Compose, Alembic, docs | Done |
| 1 Database & demo data | 29-table schema, initial migration, reproducible generator, seed/reset/validate commands | Done |
| 2 Employee intelligence | Employee 360, effective skills (verification + staleness), evidence, experience, performance | Done |
| 3 Workforce intelligence | calendars, leaves, holidays, presence, remaining workload, capacity, classification, windows | Done |
| 4 Recommendation engine | normalization, eligibility, 7 criteria, weighting, ranking, tie-breaks, NO_SUITABLE handling | Done |
| 5 LLM integration | provider interface, OpenAI-compatible / Ollama / Mock adapters, structured analysis, explanation, health, retries | Done (verified with mocked transports; no live provider run yet) |
| 6 LangGraph agent | typed state, 11 nodes, conditional failure paths, approved tools, persistence, revalidation | Done |
| 7 FastAPI | versioned REST API, auth, roles, rate limiting, OpenAPI | Done |
| 8 Testing & evaluation | unit, integration, E2E scenario and API suites | Done for MVP (see PROJECT_STATUS.md) |
| 9 Odoo readiness | integration contract documented | Done (design only, by scope) |

| 10 Operational agent | ToolNode + 20 PostgreSQL-backed tools, LLM research agent (native / JSON plan), PostgreSQL checkpointing and resume, async API, run inspection endpoints, live Groq verification | Done (see AGENT_IMPLEMENTATION_REPORT.md) |

## Next phases (proposed)

1. **Live LLM validation** – run the analysis/explanation paths against the chosen cloud and local models;
   tune prompts; measure structured-output validity rate and latency (keep deterministic fallback).
2. **Manager feedback loop** – record accept/reject of recommendations with a reason (still no
   auto-assignment), feeding the evaluation summary.
3. **Operations** – shared rate-limit store, metrics endpoint (Prometheus), container hardening, CI pipeline
   (ruff, mypy, pytest with a PostgreSQL service).
4. **Odoo 17 adapter** – implement `docs/ODOO_INTEGRATION.md` against a test Odoo instance.
5. **ML (only with real history)** – suitability/duration models (e.g. gradient boosting, learning-to-rank)
   trained on representative historical outcomes and evaluated *against the deterministic baseline*. Synthetic
   data is for functional tests only and must never be used to claim model accuracy.

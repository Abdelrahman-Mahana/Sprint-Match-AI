# Database

PostgreSQL, SQLAlchemy 2 models in `app/infrastructure/database/models/`, schema managed **only** by Alembic
(`alembic/versions/`). Revisions: `003de51576d2` (initial schema, 29 tables) -> `1aff0ff902d2` (agent runtime: 34
tables + the `agent_checkpoints` schema). Current head: `1aff0ff902d2`. The URL comes from
`DATABASE_URL`; `alembic.ini` holds no credentials.

Conventions: UUID primary keys (`gen_random_uuid()` server default), `timestamptz` for every instant, `numeric`
for hours and scores, enums as `VARCHAR + CHECK`, named constraints (`ix_/uq_/ck_/fk_/pk_` convention),
`ON DELETE CASCADE` from tenant to children, `SET NULL` for optional references.

## Entities

**Organization / tenancy** — `companies` (code, timezone, working weekdays), `departments`, `teams`,
`calendar_holidays`.

**Employee domain** — `employees` (professional data only; no protected attributes; weekly capacity derived from
schedules), `skills` (per-company catalog with aliases), `employee_skills` (proficiency 0–100,
verification status; VERIFIED requires `verified_at`), `employee_skill_evidence`, `employee_experiences` +
`employee_experience_skills` (normalized technologies), `employee_credentials` (certifications/trainings).

**Project / Scrum** — `projects` (+ `project_members`), `sprints`, `tasks` (code unique per company, complexity,
priority, status, estimate, task-level remaining, planned start, deadline, category, optional team restriction,
`required_certifications` - a hard eligibility constraint), `task_required_skills` (minimum level, mandatory,
verification requirement, weight), `task_dependencies` (finish-to-start / related; no self or direct cycles;
informational: open dependencies produce an `OPEN_DEPENDENCIES` warning).

**Workforce** — `work_schedule_days` (one row per weekday, employee timezone), `attendance_records` (one per
employee and work date), `leave_records`, `task_assignments` (employee-level allocation and remaining effort),
`timesheet_entries` (historical consumption), `employee_commitments` (other capacity-consuming duties).

**Performance** — `task_outcomes` (quality, acceptance, rework, on-time, delay reason, blocker hours, reviewer;
one per task and employee; self-review forbidden). Outcomes can only be recorded for DONE tasks by employees
assigned to them. `performance_evaluations` (periodic work-related reviews: technical quality, delivery,
collaboration; no HR/compensation data) and `performance_metrics` (period snapshots of avg quality, on-time,
acceptance, rework, tasks completed with sample size) are informational/trend data: the engine never reads them
and always recomputes from `task_outcomes`.

**AI / audit** — `task_analyses`, `recommendation_runs` (status, versions, as-of, window, request payload,
immutable `result_snapshot`, revalidation link), `recommendation_candidates`,
`recommendation_score_components`, `agent_execution_logs` (sanitized node trace with start/end time, attempts,
tokens), `agent_tool_executions` (one row per research-agent tool call: status ok/error/rejected/duplicate/timeout,
sanitized argument/result summaries, error code, duration), `agent_run_errors` (code, standardized category,
node, retryable), `audit_events`. `recommendation_runs` also stores thread id, idempotency key (unique per
company), agent mode, research summary, LLM calls/tokens, tool-call count, error category (required when
FAILED), `resumable` (only for FAILED) and `retry_of_id`. A resumed run updates its own row (upsert): no duplicate
recommendations.

**LangGraph checkpoints** — schema `agent_checkpoints` (created by the migration; tables created by the official
`PostgresSaver.setup()` via `scripts.init_db` / app startup). Graph execution state only, JSON-compatible and
without full employee records; separate from the business history; pruned after `CHECKPOINT_RETENTION_DAYS`
(`scripts.prune_checkpoints`).

## Key constraints (selection)

| Table | Constraint |
|---|---|
| employee_skills | `proficiency_level BETWEEN 0 AND 100`, unique (employee, skill), verified ⇒ verified_at |
| tasks | unique (company, task_code), estimate > 0, remaining ≥ 0, deadline > planned_start |
| task_assignments | unique (task, employee), planned_end > planned_start, non-negative hours |
| attendance_records | unique (employee, work_date), check_out > check_in, PRESENT/REMOTE ⇒ check_in |
| leave_records | end_at > start_at |
| work_schedule_days | weekday 0–6, break 0–240 min, unique (employee, weekday) |
| task_outcomes | quality 0–100, rework ≥ 0, reviewer ≠ employee, unique (task, employee) |
| recommendation_candidates | rank ≥ 1, only eligible candidates are ranked |

## Duplicate sources of truth avoided

* Weekly capacity: derived from `work_schedule_days` (no `employees.weekly_capacity_hours`).
* Schedule timezone: `employees.timezone` only.
* Remaining effort: per employee on `task_assignments`; the task-level value is an aggregate used only for
  a sole assignee (documented in SCORING.md).
* `recommendation_runs.result_snapshot` intentionally duplicates candidate data as an **immutable audit
  artifact** of what was returned; normalized candidate/component tables serve querying and evaluation.

## Demo data

`app/infrastructure/demo/` — `catalog.py` (declarative scenario specification), `generator.py` (deterministic
rows with `uuid5` IDs relative to the reference date), `seeder.py`, `validation.py` (21 consistency checks + 2
informational data-gap reports). Main tenant *Nile Digital Solutions* (Africa/Cairo, Sun–Thu): 30 employees,
5 departments, 9 teams, 24 skills, 6 projects, 12 sprints, ~130 tasks, ~120 assignments, ~300 attendance
records, leaves (approved/requested/rejected), schedules, ~370 timesheet entries, ~100 outcomes, evidence.
Second tenant *Delta Labs* (Europe/Berlin, Mon–Fri, 3 deliberately strong employees) proves tenant isolation.

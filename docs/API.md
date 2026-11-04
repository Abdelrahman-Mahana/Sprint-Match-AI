# REST API (v1)

Interactive docs: `/docs` (Swagger UI), `/openapi.json`. Authentication: header `X-API-Key`. Keys are bound to
a role (`viewer`, `manager`, `admin`) and a company (tenant). Every response carries `X-Request-ID`.

Errors use one format: `{"error": {"code", "message", "request_id", "details"}}` with 401, 403, 404, 409, 422,
429, 503 or 500. Run errors inside a recommendation carry `{code, category, message, node, retryable}`; the
category is one of `VALIDATION_ERROR, DATABASE_ERROR, LLM_PROVIDER_ERROR, TOOL_EXECUTION_ERROR,
GRAPH_EXECUTION_ERROR, INCOMPLETE_DATA, NO_SUITABLE_CANDIDATE, PROVIDER_UNAVAILABLE, TIMEOUT, INTERNAL_ERROR`.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness |
| GET | `/ready?check_llm=false` | — | DB readiness (503 if down); LLM reported, never required |
| GET | `/api/v1/health/llm?probe=true` | viewer | Provider health, model, capabilities (tool calling probed once) |
| GET | `/api/v1/health/agent` | viewer | Compiled graph nodes, 20 tools, resolved agent mode, checkpointer, limits |
| POST | `/api/v1/agent/recommend` | manager | Run the LangGraph agent (`ainvoke`); header `Idempotency-Key` optional |
| POST | `/api/v1/agent/recommend/stream` | manager | Same run as Server-Sent Events (`astream`): `started`, `node`, `tool`, `result` |
| GET | `/api/v1/agent/runs/{run_id}` | viewer | Run metadata: status, agent mode, LLM usage, tool calls, resumable |
| GET | `/api/v1/agent/runs/{run_id}/logs` | viewer | Node log: start/end, duration, attempts, tokens, sanitized summaries |
| GET | `/api/v1/agent/runs/{run_id}/tools` | viewer | Tool calls: status, sanitized arguments/results, error code, duration |
| GET | `/api/v1/agent/runs/{run_id}/errors` | viewer | Categorized run errors |
| GET | `/api/v1/agent/runs/{run_id}/candidates` | viewer | Primary, alternatives, excluded |
| GET | `/api/v1/agent/runs/{run_id}/checkpoints` | viewer | Sanitized LangGraph checkpoint history (no state values) |
| POST | `/api/v1/agent/runs/{run_id}/retry` | manager | Resume from checkpoint (same run id) or re-run a completed FAILED run (new run, `retry_of_id`); 409 for completed runs |
| GET | `/api/v1/employees?status&department_id&skill&limit&offset` | viewer | Paginated list |
| GET | `/api/v1/employees/{id}` | viewer | Employee |
| GET | `/api/v1/employees/{id}/360?preset&start&deadline&working_days&as_of` | viewer | Profile, effective skills, evidence, performance, current workload & availability |
| GET | `/api/v1/employees/{id}/availability?...` | viewer | Availability for a window |
| GET | `/api/v1/employees/{id}/performance` | viewer | Evidence-based performance summary |
| POST | `/api/v1/tasks?task_code=` | manager | Create a task (skills resolved against the catalog) |
| GET | `/api/v1/tasks?status&project_id&sprint_id&limit&offset` | viewer | Paginated list |
| GET | `/api/v1/tasks/{id}` | viewer | Task with required skills |
| POST | `/api/v1/tasks/analyze` | manager | Task analysis (stored with analyzer version) |
| POST | `/api/v1/workforce/analyze` | viewer | Availability for selected/all employees |
| GET | `/api/v1/workforce/availability?...` | viewer | Availability of all active employees + status counts |
| POST | `/api/v1/recommendations` | manager | Run the recommendation agent (same graph; `Idempotency-Key` supported) |
| GET | `/api/v1/recommendations/{id}` | viewer | Stored (immutable) result |
| POST | `/api/v1/recommendations/{id}/revalidate?as_of` | manager | Re-evaluate against current data |
| GET | `/api/v1/recommendations/{id}/candidates` | viewer | Primary, alternatives, excluded |
| POST | `/api/v1/task-outcomes` | manager | Record a reviewed outcome (DONE task, assigned employee) |
| GET | `/api/v1/evaluation/summary`, `/api/v1/evaluation/metrics` | viewer | Runs by status, coverage, agreement with delivered outcomes |

Expensive endpoints (`POST /recommendations`, `/agent/recommend*`, `/agent/runs/{id}/retry`, `/revalidate`,
`/tasks/analyze`) are rate limited per API key. A repeated `Idempotency-Key` returns the stored result with HTTP
200 and `idempotent_replay: true`. FAILED runs map to HTTP by cause (404 not found, 422 invalid, 503 database /
checkpoint / provider unavailable, 504 timeout, else 500); business outcomes (NEEDS_INPUT, NO_SUITABLE_CANDIDATE,
PARTIAL_DATA, PROVIDER_UNAVAILABLE) are 201.

## Recommendation request

Exactly one of `task_id`, `task_code`, `task` (structured specification):

```json
{
  "task_code": "TASK-SCN-01",
  "execution_window": {"start": "2026-11-04T09:00:00+02:00", "deadline": "2026-11-08T17:00:00+02:00"},
  "requested_by": "USER-001",
  "include_alternatives": true,
  "max_alternatives": 3,
  "as_of": "2026-11-03T10:00:00+02:00"
}
```

`execution_window` may instead use `{"preset": "NEXT_WORKING_DAYS", "working_days": 3}` (presets: `TODAY`,
`REMAINING_DAY`, `NEXT_WORKING_DAY`, `NEXT_WORKING_DAYS`, `CURRENT_SPRINT`, `TASK_PLANNED`, `EXPLICIT`). All
datetimes must include a timezone offset. Omit `as_of` to evaluate at the current time.

## Recommendation response (abridged)

```json
{
  "recommendation_id": "…", "status": "SUCCESS", "task_code": "TASK-SCN-01",
  "execution_window": {"start": "…", "end": "…", "preset": "TASK_PLANNED"},
  "primary_candidate": {
    "employee_code": "EMP-001", "name": "Ahmed Hassan", "rank": 1, "eligible": true,
    "suitability_score": 85.5, "availability_status": "AVAILABLE", "presence": "PRESENT",
    "available_hours": 18.22, "workload_certainty": "CERTAIN", "confidence_level": "HIGH",
    "data_coverage": 1.0,
    "score_components": [{"criterion": "SKILL_MATCH", "raw_score": 94.5, "score_used": 94.5,
                          "weight": 0.3, "weighted_score": 28.35, "has_evidence": true,
                          "evidence": ["Python: declared 92, verified (required 75, mandatory)"]}],
    "exclusion_reasons": [], "missing_information": []
  },
  "alternatives": [{"employee_code": "EMP-006", "suitability_score": 77.23}],
  "excluded_candidates": [{"employee_code": "EMP-002", "exclusion_reasons": [
      {"code": "INSUFFICIENT_AVAILABLE_CAPACITY", "message": "Available capacity 0.0h is below the required 16.0h"}]}],
  "explanation": "Ahmed Hassan (EMP-001) is recommended …", "explanation_source": "deterministic",
  "warnings": [], "missing_information": [], "data_quality": {"coverage": 1.0, "eligible_candidates": 3},
  "algorithm_version": "sprintmatch-scoring-1.1.0", "scoring_config_fingerprint": "…",
  "trace": [{"node": "validate_request", "status": "ok", "duration_ms": 31, "attempts": 1}],
  "errors": [], "persisted": true, "auto_assigned": false
}
```

HTTP status: `201` for every completed business outcome (`SUCCESS`, `PARTIAL_DATA`, `NO_SUITABLE_CANDIDATE`,
`NEEDS_INPUT`, `PROVIDER_UNAVAILABLE`); `FAILED` maps to `404` (task not found), `503` (database unavailable),
`422` or `500`, with the response body still describing the failure.

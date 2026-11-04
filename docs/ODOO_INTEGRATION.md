# Future Odoo 17 integration contract (Milestone 9 — design only)

Odoo is **not** required to build, run or test SprintMatch AI. Integration is a separate adapter that implements
the existing data-source protocols in `app/domain/interfaces.py`; engines, scoring, policies, the LangGraph
workflow and the API stay unchanged.

## Adapter shape

```text
app/infrastructure/data_sources/odoo/
  client.py          JSON-RPC/XML-RPC client (external API, API key of a technical Odoo user, read-only rights)
  mappers.py         Odoo records -> domain entities
  repositories.py    OdooEmployeeRepository, OdooTaskRepository, OdooWorkforceRepository, ...
  unit_of_work.py    OdooUnitOfWork (+ PostgreSQL for SprintMatch-owned tables, see below)
```

`AppContainer` receives a different `UnitOfWorkFactory` (selected by configuration, e.g.
`DATA_SOURCE=odoo`). Nothing else changes.

## Mapping (Odoo 17 standard models)

| Domain protocol / entity | Odoo source |
|---|---|
| Company | `res.company` (+ `resource.calendar` for working days, company timezone) |
| Employee | `hr.employee` (name, `job_title`, `department_id`, `company_id`, `resource_calendar_id`, `tz`, `active`) |
| Department / team | `hr.department` (teams: department hierarchy or a custom field) |
| Skills | `hr.skill`, `hr.skill.type`, `hr.employee.skill` (`skill_level_id.level_progress` → proficiency 0–100) |
| Experience / certifications | `hr.resume.line` (by `line_type_id`) |
| Schedules | `resource.calendar.attendance` of the employee calendar |
| Holidays | `resource.calendar.leaves` without resource (global time off) |
| Attendance | `hr.attendance` (`check_in`, `check_out`) |
| Leaves | `hr.leave` (`state`: `validate` → APPROVED, `confirm`/`validate1` → REQUESTED, `refuse` → REJECTED) |
| Projects / tasks | `project.project`, `project.task` (`user_ids`, `allocated_hours`, `remaining_hours`, `date_deadline`, `stage_id`) |
| Sprints | not standard: custom model/tags or a project milestone convention |
| Timesheets | `account.analytic.line` (logged hours; never subtracted from capacity) |
| Task required skills, task outcomes, verification status | not standard: custom fields/models or kept in SprintMatch PostgreSQL |

## Rules the adapter must respect

* Return domain entities exactly as defined (timezone-aware datetimes, hours as floats, statuses mapped to the
  domain enums); unknown values must be surfaced as missing data, not defaulted to zero.
* `project.task.user_ids` can contain several users without a per-user split: map `remaining_estimated_hours` to
  `None` per assignment unless an explicit allocation exists (the engine then reports UNKNOWN, as designed).
* Translate connectivity failures to `DataSourceUnavailableError` (retryable) and access errors to
  `AuthorizationError`.
* Read-only for HR data. SprintMatch-owned data (recommendation runs, candidates, logs, audit, task analyses,
  optionally outcomes) stays in SprintMatch's PostgreSQL via the existing `RecommendationRepository` /
  `AuditRepository`, i.e. the Odoo unit of work is a composite.
* Tenant isolation: one API key ↔ one Odoo company; every query filters by `company_id`.
* Never write assignments to Odoo from the recommendation flow (human approval remains outside the MVP).

## Acceptance for the future phase

The existing agent/E2E test-suite must pass against an Odoo-backed fixture database after mapping the demo
dataset into Odoo, with no changes to `app/intelligence`, `app/agents` or `app/domain/policies.py`.

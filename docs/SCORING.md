# Workforce, eligibility and scoring rules

Algorithm version: `sprintmatch-scoring-1.0.0` (`app/domain/policies.py`). Every threshold below is a field of
`ScoringConfig`; its SHA-256 fingerprint is stored with each recommendation run.

## 1. Execution window

Precedence: explicit request window → requested preset → task `planned_start`/`deadline` (`TASK_PLANNED`) →
task sprint (`CURRENT_SPRINT`) → `NEXT_WORKING_DAYS(DEFAULT_HORIZON_WORKING_DAYS)`.
Presets (`TODAY`, `REMAINING_DAY`, `NEXT_WORKING_DAY`, `NEXT_WORKING_DAYS`, `CURRENT_SPRINT`) use the company
calendar (timezone, working weekdays, holidays) and span whole local days. A window entirely in the past is
rejected; a start earlier than `as_of` is clamped to `as_of` (time that has passed cannot be used).

## 2. Workforce availability (`app/intelligence/workforce/capacity.py`)

Schedules are expanded per employee in the employee's timezone; arithmetic is done in UTC (DST-safe). Breaks are
prorated: an interval covering part of a shift counts `overlap_hours × net_shift/gross_shift`.

```
scheduled_hours          = net scheduled time inside the window
holiday_hours            = part of scheduled_hours on company holidays
leave_hours              = remaining scheduled time covered by APPROVED leave (overlaps merged, counted once)
available_working_hours  = scheduled_hours − holiday_hours − leave_hours
committed_work_hours     = Σ remaining effort of PLANNED/ACTIVE assignments prorated into the window
other_commitment_hours   = Σ commitment.hours × (overlap of commitment period with window / its duration)
available_capacity_hours = max(0, available_working_hours − committed_work_hours − other_commitment_hours)
overcommitted_hours      = max(0, −(available_working_hours − committed − other))
```

Pending (REQUESTED) leave is not subtracted; it produces `PENDING_LEAVE_IN_WINDOW`. Rejected/cancelled leave is
ignored.

**Remaining effort of an assignment** (precedence):

| Basis | Rule | Certainty |
|---|---|---|
| `ASSIGNMENT_REMAINING_ESTIMATE` | `task_assignments.remaining_estimated_hours` | CERTAIN |
| `TASK_REMAINING_SOLE_ASSIGNEE` | task-level remaining, **only** if the employee is the sole active assignee | CERTAIN |
| `ALLOCATION_MINUS_LOGGED` | `max(allocated − logged, 0)` | ESTIMATED |
| `UNKNOWN` | none of the above (e.g. shared task without a per-employee split) | UNCERTAIN (counts 0, never guessed) |

Timesheet hours are used only for the `ALLOCATION_MINUS_LOGGED` estimate and are **never** subtracted from
capacity (no double counting).

**Proration into the window:** remaining effort is spread evenly over the employee's effective working time
(schedule minus holidays and approved leave) between `max(as_of, planned_start)` and `planned_end`; the window
receives `remaining × effective_hours_in_window / effective_hours_of_plan`. Overdue assignments
(`planned_end ≤ as_of`) and assignments without `planned_end` are charged in full (conservative, with
`OVERDUE_ASSIGNMENT` / ESTIMATED certainty).

**Presence** (current indicator only — never used as availability): ON_LEAVE (approved leave covers `as_of`),
then today's attendance record (ABSENT, PRESENT = checked in without check-out, CHECKED_OUT), NOT_SCHEDULED,
NO_RECORD. An employee absent today can be fully available in a future window.

**Classification** (evaluated in order; `free_ratio = capacity / available_working_hours`):

| # | Condition | Status |
|---|---|---|
| 1 | employment status not ACTIVE | UNAVAILABLE |
| 2 | no work schedule | UNKNOWN |
| 3 | available_working_hours = 0 (leave/holidays/no shifts) | UNAVAILABLE |
| 4 | free_ratio ≤ 0.15, or capacity < 2h and free_ratio < 0.5 | BUSY |
| 5 | workload certainty UNCERTAIN | UNKNOWN |
| 6 | free_ratio ≥ 0.5 | AVAILABLE |
| 7 | otherwise | PARTIALLY_AVAILABLE |

## 3. Effective skill level

`effective = proficiency × verification_factor × recency_factor`
verification: VERIFIED 1.0 · PENDING_REVIEW 0.9 · SELF_DECLARED 0.8 · REJECTED 0.
recency (months since last assessment): ≥ 36 → 0.7, ≥ 24 → 0.85, else 1.0.

## 4. Eligibility (hard constraints, `eligibility.py`)

Evaluated before and independently from ranking. Each failure produces an `ExclusionReason` with details:

| Code | Rule |
|---|---|
| EMPLOYEE_INACTIVE | employment status ≠ ACTIVE |
| MANDATORY_SKILL_MISSING | mandatory skill absent (or effective 0) |
| MANDATORY_SKILL_BELOW_THRESHOLD | effective level < minimum |
| MANDATORY_SKILL_NOT_VERIFIED | requirement demands verification and skill is not VERIFIED |
| NO_REQUIRED_SKILL_COVERAGE | task has only optional skills and none is met |
| TEAM_ACCESS_REQUIRED / PROJECT_ACCESS_REQUIRED | task restricted to a team / project members |
| ON_LEAVE_DURING_WINDOW | approved leave covers all working time in the window |
| NO_WORKING_TIME_IN_WINDOW | status UNAVAILABLE for another reason |
| AVAILABILITY_UNKNOWN | status UNKNOWN (a **data gap**, reported as such, not a rejection of the person) |
| INSUFFICIENT_AVAILABLE_CAPACITY | capacity < estimated_hours × capacity_buffer (1.0) |

Having other tasks never disqualifies by itself; only insufficient remaining capacity does. If nobody is
eligible the result is `NO_SUITABLE_CANDIDATE` (never forced); `DEADLINE_INFEASIBLE` is added when the
estimate exceeds every active employee's working time in the window.

## 5. Criteria (0–100 each)

| Criterion | Weight | Calculation | No-evidence prior |
|---|---:|---|---|
| SKILL_MATCH | 0.30 | weighted mean over required skills of `85×min(eff/min,1)` (+ up to 15 for surplus `(eff−min)/(100−min)` once met); mandatory weight 1, optional 0.5 (or task weight). Unrelated skills are ignored. | always evaluable |
| EXPERIENCE | 0.15 | `0.6 × min(similar_tasks/5,1)×100 + 0.4 × min(relevant_years/5,1)×100`; similar task = similarity ≥ 0.5 (80% weighted share of required skills, 20% category match); relevant years = merged periods of experience records using a required skill. If only one component has data, it is used alone. | 50 |
| QUALITY | 0.15 | per outcome `quality − 10×rework` (capped at 40 if not accepted); similar tasks weighted ×2; then Bayesian shrinkage `(n·mean + 3·60)/(n+3)` | 60 |
| RELIABILITY | 0.10 | on-time rate over attributable deliveries (late with EMPLOYEE/UNSPECIFIED reason); external blockers, dependencies and scope changes are **excluded**; shrinkage toward 0.7 with pseudo-count 3 | 70 |
| CAPACITY | 0.15 | `ratio = capacity/estimated_hours`; ratio ≥ 1: `50 + 50×min((ratio−1)/(2−1),1)`; ratio < 1: `50×ratio` (only for excluded employees) | 50 if no schedule |
| COMPLEXITY_FIT | 0.10 | capability = max(seniority tier from experience years [<2:1, 2–5:2, 5–8:3, ≥8:4], highest complexity delivered with quality ≥ 70); gap = required − capability; gap > 0: `100 − 35×gap`; gap 0/−1: 100; ≤ −2: 85 (overqualified) | 60 |
| PROJECT_FAMILIARITY | 0.05 | completed project tasks 0/1/2/≥3 → 0/50/75/100; current member with 0 tasks → 25. **Not applicable** when the task has no project (weight redistributed). | — |

`suitability = Σ normalized_weight × score_used` (weights normalized over applicable criteria).
Suitability is computed only for eligible employees and is always task-specific (no global ranking exists).

## 6. Missing-data policy, coverage and confidence

* Missing evidence is never zero: the criterion uses its neutral prior, `raw_score = null`,
  `has_evidence = false`, and `NO_EVIDENCE:<criterion>` is listed in `missing_information`.
* `data_coverage = Σ weight(applicable ∧ has_evidence) / Σ weight(applicable)`.
* `confidence = data_coverage × certainty_factor` (CERTAIN 1.0, ESTIMATED 0.85, UNCERTAIN 0.6);
  HIGH ≥ 0.8, MEDIUM ≥ 0.55, else LOW.
* Run status is `PARTIAL_DATA` instead of `SUCCESS` when the primary's coverage < 0.5 or its workload is
  UNCERTAIN.

## 7. Ranking and tie-breaking

Sort eligible candidates by: suitability desc → data coverage desc → available capacity desc →
employee code asc. `CLOSE_CANDIDATES` is raised when the top two differ by ≤ 2.0 points.

## 8. Task analysis precedence

Manager/task-record values > validated LLM suggestions > deterministic defaults. Unknown skills in structured
input ⇒ `NEEDS_INPUT`. Without structured skills: LLM suggestions (resolved against the catalog; mandatory only
with a verbatim quote naming the skill; flagged `REQUIREMENTS_SUGGESTED_BY_LLM`), else catalog keyword match
(optional skills, minimum level by complexity: low 40, medium 55, high 70, critical 80). Missing effort ⇒
`NEEDS_INPUT` (LLM estimates are only suggestions unless `ACCEPT_LLM_EFFORT_ESTIMATES=true`). Missing complexity
defaults to medium with a warning.

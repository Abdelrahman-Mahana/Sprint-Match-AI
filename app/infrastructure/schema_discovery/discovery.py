"""Schema discovery engine.

Inspects any SQL database through SQLAlchemy's inspector and maps its tables/columns to the domain
concepts the agent needs (employees, tasks, skills, ...). Uses table-name heuristics, column-presence
scoring and the foreign-key graph. The result is a SchemaMapping that can be saved to JSON and
hand-corrected once per system.

Designed to handle plain schemas, Odoo-style prefixed schemas (hr_employee, project_task, res_partner)
and WordPress-style prefixed schemas (wp_*).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.infrastructure.schema_discovery.mapping import (
    ColumnMap,
    SchemaMapping,
    TableMapping,
    save_mapping,
)

logger = logging.getLogger(__name__)

DISCOVERY_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Concept signatures
# ---------------------------------------------------------------------------

# For each domain concept: table-name patterns (regex) and the columns that indicate it.
# Columns are grouped: "strong" (very indicative), "weak" (supportive). A table needs enough
# strong columns to be confidently selected.

# Column name candidates are matched case-insensitively against substrings/patterns.
_STRONG = "strong"
_WEAK = "weak"

_CONCEPT_SIGNATURES: dict[str, dict[str, Any]] = {
    "employees": {
        "name_patterns": [
            r"(^|_)employee(s)?($|_)", r"(^|_)staff($|_)", r"(^|_)worker(s)?($|_)",
            r"(^|_)hr_?employee(s)?($|_)", r"(^|_)res_users$",
        ],
        "strong": {
            "employee_code": [r"emp(l)?(oyee)?_?code", r"^code$", r"emp_?id", r"matricule"],
            "full_name": [r"full_?name", r"^name$", r"display_?name", r"employee_?name"],
            "email": [r"email", r"mail", r"work_?email"],
        },
        "weak": {
            "job_title": [r"job_?title", r"position", r"title", r"role", r"function"],
            "hire_date": [r"hire_?date", r"joining_?date", r"start_?date", r"admission"],
            "employment_status": [r"employment_?status", r"active", r"^status$", r"state"],
            "department_id": [r"department_?id", r"dept_?id", r"department"],
            "team_id": [r"team_?id", r"team"],
            "company_id": [r"company_?id", r"tenant_?id", r"org_?id"],
            "experience_years": [r"experience", r"years_?exp", r"seniority"],
            "timezone": [r"timezone", r"time_?zone", r"tz"],
        },
        "expected_fks": {"department_id": "departments", "team_id": "teams"},
    },
    "skills": {
        "name_patterns": [r"(^|_)skill(s)?($|_)", r"(^|_)competenc(e|y)(s|ies)?($|_)"],
        "strong": {
            "name": [r"^name$", r"skill_?name", r"label", r"title"],
        },
        "weak": {
            "category": [r"categor(y|ies)", r"type", r"domain"],
            "aliases": [r"alias(es)?", r"synonyms"],
        },
    },
    "employee_skills": {
        "name_patterns": [
            r"(^|_)employee_?skill(s)?($|_)", r"(^|_)emp(l)?(oyee)?_?skill(s)?($|_)",
            r"(^|_)skill_?link(s)?($|_)", r"(^|_)hr_?employee_?skill(s)?($|_)",
            r"(^|_)user_?skill(s)?($|_)", r"(^|_)competenc(e|y)_?link($|_)",
        ],
        "strong": {},  # identified primarily by FK graph
        "weak": {
            "declared_level": [r"declared_?level", r"level", r"proficiency", r"rating"],
            "verification_status": [r"verif(ication|y)?_?status", r"verified", r"status"],
            "effective_level": [r"effective_?level"],
        },
        "expected_fks": {"employee_id": "employees", "skill_id": "skills"},
    },
    "departments": {
        "name_patterns": [r"(^|_)department(s)?($|_)", r"(^|_)dept(s)?($|_)", r"(^|_)hr_?department(s)?($|_)"],
        "strong": {
            "name": [r"^name$", r"department_?name", r"label"],
        },
        "weak": {
            "company_id": [r"company_?id", r"tenant_?id"],
        },
    },
    "teams": {
        "name_patterns": [r"(^|_)team(s)?($|_)", r"(^|_)squad(s)?($|_)"],
        "strong": {
            "name": [r"^name$", r"team_?name", r"label"],
        },
        "weak": {
            "department_id": [r"department_?id", r"dept_?id"],
            "company_id": [r"company_?id", r"tenant_?id"],
        },
    },
    "tasks": {
        "name_patterns": [
            r"(^|_)task(s)?($|_)", r"(^|_)ticket(s)?($|_)", r"(^|_)issue(s)?($|_)",
            r"(^|_)project_?task(s)?($|_)", r"(^|_)todo(s)?($|_)",
        ],
        "strong": {
            "title": [r"title", r"subject", r"^name$", r"task_?name", r"summary"],
            "description": [r"description", r"detail(s)?", r"body", r"content"],
        },
        "weak": {
            "task_code": [r"task_?code", r"^code$", r"ticket_?id", r"key", r"reference"],
            "status": [r"status", r"state", r"stage"],
            "priority": [r"priority"],
            "complexity": [r"complexity", r"difficulty", r"size", r"story_?points"],
            "estimated_hours": [r"estimated_?hours", r"estimate", r"planned_?hours", r"eta", r"effort"],
            "project_id": [r"project_?id", r"project"],
            "company_id": [r"company_?id", r"tenant_?id"],
            "task_category": [r"categor(y|ies)", r"type", r"task_?type"],
        },
        "expected_fks": {"project_id": "projects"},
    },
    "task_assignments": {
        "name_patterns": [
            r"(^|_)assignment(s)?($|_)", r"(^|_)task_?assignment(s)?($|_)",
            r"(^|_)task_?employee(s)?($|_)", r"(^|_)allocation(s)?($|_)",
            r"(^|_)task_?assignee(s)?($|_)",
        ],
        "strong": {},  # identified by FK graph
        "weak": {
            "assignment_status": [r"status", r"state", r"assignment_?status"],
            "allocated_hours": [r"allocated_?hours", r"hours", r"planned_?hours", r"effort"],
            "assigned_at": [r"assigned_?at", r"assigned_?date", r"created_?at", r"date"],
        },
        "expected_fks": {"task_id": "tasks", "employee_id": "employees"},
    },
    "projects": {
        "name_patterns": [r"(^|_)project(s)?($|_)", r"(^|_)epic(s)?($|_)"],
        "strong": {
            "name": [r"^name$", r"project_?name", r"title", r"label"],
        },
        "weak": {
            "code": [r"^code$", r"key", r"project_?code", r"reference"],
            "description": [r"description", r"detail(s)"],
            "company_id": [r"company_?id", r"tenant_?id"],
        },
    },
    "companies": {
        "name_patterns": [
            r"(^|_)compan(y|ies)($|_)", r"(^|_)organization(s)?($|_)", r"(^|_)org(s)?($|_)",
            r"(^|_)tenant(s)?($|_)", r"(^|_)res_?partner(s)?($|_)",
        ],
        "strong": {
            "name": [r"^name$", r"company_?name", r"partner_?name", r"legal_?name"],
        },
        "weak": {
            "code": [r"^code$", r"key", r"reference"],
            "timezone": [r"timezone", r"time_?zone", r"tz"],
        },
    },
    "leaves": {
        "name_patterns": [
            r"(^|_)leave(s)?($|_)", r"(^|_)absence(s)?($|_)", r"(^|_)time_?off($|_)",
            r"(^|_)hr_?leave(s)?($|_)", r"(^|_)holiday(s)?_?request($|_)",
        ],
        "strong": {},  # identified by date columns + FK to employees
        "weak": {
            "employee_id": [r"employee_?id", r"emp_?id", r"user_?id"],
            "start_date": [r"start_?date", r"from_?date", r"date_?from", r"begin"],
            "end_date": [r"end_?date", r"to_?date", r"date_?to", r"until"],
            "status": [r"status", r"state", r"approval"],
            "leave_type": [r"leave_?type", r"type", r"category"],
            "hours": [r"hours", r"duration"],
        },
        "expected_fks": {"employee_id": "employees"},
    },
    "attendance": {
        "name_patterns": [
            r"(^|_)attendance(s)?($|_)", r"(^|_)check_?in(s)?($|_)", r"(^|_)timesheet(s)?($|_)",
            r"(^|_)hr_?attendance(s)?($|_)", r"(^|_)presenc(e|es)($|_)",
        ],
        "strong": {},  # identified by check-in/out columns + FK to employees
        "weak": {
            "employee_id": [r"employee_?id", r"emp_?id", r"user_?id"],
            "check_in": [r"check_?in", r"start", r"punch_?in", r"arrival"],
            "check_out": [r"check_?out", r"end", r"punch_?out", r"departure"],
            "date": [r"^date$", r"day", r"attendance_?date"],
        },
        "expected_fks": {"employee_id": "employees"},
    },
    "task_required_skills": {
        "name_patterns": [
            r"(^|_)task_?skill(s)?($|_)", r"(^|_)task_?requirement(s)?($|_)",
            r"(^|_)task_?required_?skill(s)?($|_)", r"(^|_)required_?skill(s)?($|_)",
        ],
        "strong": {},  # junction table: identified by FK graph
        "weak": {
            "minimum_level": [r"minimum_?level", r"min_?level", r"required_?level", r"level"],
            "mandatory": [r"mandatory", r"is_?required", r"required"],
            "weight": [r"weight", r"importance"],
        },
        "expected_fks": {"task_id": "tasks", "skill_id": "skills"},
    },
    "project_members": {
        "name_patterns": [
            r"(^|_)project_?member(s)?($|_)", r"(^|_)project_?employee(s)?($|_)",
            r"(^|_)project_?assignment(s)?($|_)", r"(^|_)member(s)?_?project($|_)",
        ],
        "strong": {},  # junction table: identified by FK graph
        "weak": {
            "role": [r"^role$", r"position", r"function"],
        },
        "expected_fks": {"project_id": "projects", "employee_id": "employees"},
    },
    "work_schedule": {
        "name_patterns": [
            r"(^|_)schedule(s)?_?day(s)?($|_)", r"(^|_)work_?schedule(s)?($|_)",
            r"(^|_)working_?hour(s)?($|_)", r"(^|_)shift(s)?($|_)",
        ],
        "strong": {},  # identified by weekday/start/end columns + FK to employees
        "weak": {
            "employee_id": [r"employee_?id", r"emp_?id", r"user_?id"],
            "weekday": [r"weekday", r"day_?of_?week", r"^day$"],
            "start_time": [r"start_?time", r"^start$", r"from_?time"],
            "end_time": [r"end_?time", r"^end$", r"to_?time"],
            "break_minutes": [r"break", r"lunch", r"pause"],
        },
        "expected_fks": {"employee_id": "employees"},
    },
    "holidays": {
        "name_patterns": [r"(^|_)holiday(s)?($|_)", r"(^|_)public_?holiday(s)?($|_)", r"(^|_)day_?off($|_)"],
        "strong": {},  # identified by a date column
        "weak": {
            "holiday_date": [r"holiday_?date", r"^date$", r"day"],
            "name": [r"^name$", r"title", r"label"],
            "company_id": [r"company_?id", r"tenant_?id"],
        },
    },
    "sprints": {
        "name_patterns": [r"(^|_)sprint(s)?($|_)", r"(^|_)iteration(s)?($|_)", r"(^|_)cycle(s)?($|_)"],
        "strong": {
            "name": [r"^name$", r"sprint_?name", r"title"],
        },
        "weak": {
            "start_date": [r"start_?date", r"date_?start", r"from_?date"],
            "end_date": [r"end_?date", r"date_?end", r"to_?date"],
            "project_id": [r"project_?id", r"project"],
            "status": [r"status", r"state"],
        },
        "expected_fks": {"project_id": "projects"},
    },
    "task_outcomes": {
        "name_patterns": [
            r"(^|_)task_?outcome(s)?($|_)", r"(^|_)outcome(s)?($|_)", r"(^|_)review(s)?($|_)",
            r"(^|_)feedback($|_)", r"(^|_)task_?review(s)?($|_)",
        ],
        "strong": {},  # identified by quality/review columns + FK graph
        "weak": {
            "quality_score": [r"quality", r"score", r"rating"],
            "accepted": [r"accepted", r"approved", r"is_?accepted"],
            "rework_count": [r"rework", r"reopen"],
            "on_time": [r"on_?time", r"delayed", r"late"],
            "reviewed_at": [r"reviewed_?at", r"reviewed_?date", r"^date$", r"created_?at"],
            "employee_id": [r"employee_?id", r"assignee_?id", r"user_?id"],
        },
        "expected_fks": {"task_id": "tasks", "employee_id": "employees"},
    },
    "timesheets": {
        "name_patterns": [
            r"(^|_)timesheet(s)?($|_)", r"(^|_)time_?entr(y|ies)($|_)", r"(^|_)worklog(s)?($|_)",
            r"(^|_)hour_?log(s)?($|_)",
        ],
        "strong": {},  # junction-ish: identified by FK graph + hours column
        "weak": {
            "task_id": [r"task_?id", r"task"],
            "employee_id": [r"employee_?id", r"user_?id", r"emp_?id"],
            "hours": [r"^hours$", r"duration", r"time_?spent", r"logged_?hours"],
            "work_date": [r"work_?date", r"^date$", r"day"],
        },
        "expected_fks": {"task_id": "tasks", "employee_id": "employees"},
    },
}

# Order matters: concepts with clear signatures first; junction tables (employee_skills,
# task_assignments, ...) resolved after their parents so FK-graph scoring can use resolved parents.
_DISCOVERY_ORDER = [
    "companies", "employees", "skills", "departments", "teams", "projects",
    "tasks", "sprints", "leaves", "attendance", "employee_skills",
    "task_assignments", "task_required_skills", "project_members",
    "work_schedule", "holidays", "task_outcomes", "timesheets",
]

# Domain fields expected per concept (for column mapping within a selected table).
_DOMAIN_FIELDS: dict[str, list[str]] = {
    "employees": ["id", "employee_code", "full_name", "email", "job_title", "hire_date",
                  "employment_status", "department_id", "team_id", "company_id",
                  "experience_years", "timezone", "skills_text"],
    "skills": ["id", "name", "category", "aliases"],
    "employee_skills": ["employee_id", "skill_id", "declared_level",
                        "verification_status", "effective_level"],
    "departments": ["id", "name", "company_id"],
    "teams": ["id", "name", "department_id", "company_id"],
    "tasks": ["id", "task_code", "title", "description", "status", "priority", "complexity",
              "estimated_hours", "remaining_estimated_hours", "planned_start", "deadline",
              "acceptance_criteria", "project_id", "company_id", "task_category"],
    "task_assignments": ["task_id", "employee_id", "assignment_status",
                         "allocated_hours", "assigned_at"],
    "task_required_skills": ["task_id", "skill_id", "minimum_level", "mandatory", "weight"],
    "project_members": ["project_id", "employee_id", "role"],
    "projects": ["id", "code", "name", "description", "company_id"],
    "companies": ["id", "code", "name", "timezone"],
    "leaves": ["employee_id", "start_date", "end_date", "status", "leave_type", "hours"],
    "attendance": ["employee_id", "check_in", "check_out", "date"],
    "work_schedule": ["employee_id", "weekday", "start_time", "end_time", "break_minutes"],
    "holidays": ["holiday_date", "name", "company_id"],
    "sprints": ["id", "name", "start_date", "end_date", "project_id", "status"],
    "task_outcomes": ["task_id", "employee_id", "quality_score", "accepted",
                      "rework_count", "on_time", "reviewed_at"],
    "timesheets": ["task_id", "employee_id", "hours", "work_date"],
}

# Extra column patterns that only matter for a few concepts.
_EXTRA_FIELDS: dict[str, dict[str, list[str]]] = {
    "employees": {
        "skills_text": [r"skills?($|_)", r"competenc", r"technolog", r"expertise", r"^tags$"],
        "id": [r"^id$", r"^uuid$", r"^pk$", r"employee_?id"],
    },
    "tasks": {
        "id": [r"^id$", r"^uuid$", r"^pk$", r"task_?id", r"^issue_?id$"],
        "planned_start": [r"planned_?start", r"start_?date", r"scheduled_?start"],
        "deadline": [r"deadline", r"due_?date", r"end_?date", r"target_?date"],
        "remaining_estimated_hours": [r"remaining_?hours", r"remaining_?estimate", r"time_?remaining"],
        "acceptance_criteria": [r"acceptance_?criteria", r"definition_?of_?done", r"^dod$"],
    },
    "skills": {"id": [r"^id$", r"^uuid$", r"^pk$", r"skill_?id"]},
    "projects": {"id": [r"^id$", r"^uuid$", r"^pk$", r"project_?id"]},
    "departments": {"id": [r"^id$", r"^uuid$", r"^pk$", r"department_?id", r"dept_?id"]},
    "teams": {"id": [r"^id$", r"^uuid$", r"^pk$", r"team_?id"]},
    "companies": {"id": [r"^id$", r"^uuid$", r"^pk$", r"company_?id"]},
    "sprints": {"id": [r"^id$", r"^uuid$", r"^pk$", r"sprint_?id"]},
}


def _matches_any(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(re.search(p, low) for p in patterns)


def _strip_prefix(table: str) -> str:
    """Remove common ORM/ERP prefixes (wp_, hr_, project_, res_, account_) for name matching."""
    low = table.lower()
    for prefix in ("wp_", "hr_", "project_", "res_", "account_", "mail_", "ir_"):
        if low.startswith(prefix):
            return table[len(prefix):]
    return table


# ---------------------------------------------------------------------------
# Discovery engine
# ---------------------------------------------------------------------------


class SchemaDiscovery:
    """Inspects a connected database and produces a SchemaMapping."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.inspector = inspect(engine)

    # --- public API -------------------------------------------------------------------------
    def discover(self, *, db_url_masked: str = "") -> SchemaMapping:
        tables = self._relevant_tables()
        if not tables:
            logger.warning("schema discovery: no tables found")
            return SchemaMapping(source_db_url_masked=db_url_masked,
                                 discovered_at=datetime.utcnow().isoformat() + "Z")

        col_info: dict[str, list[dict[str, Any]]] = {
            t: self.inspector.get_columns(t) for t in tables
        }
        fk_info: dict[str, list[dict[str, Any]]] = {
            t: self.inspector.get_foreign_keys(t) for t in tables
        }
        column_names: dict[str, set[str]] = {
            t: {c["name"].lower() for c in cols} for t, cols in col_info.items()
        }

        resolved: dict[str, str | None] = {}
        mapping = SchemaMapping(
            source_db_url_masked=db_url_masked,
            discovered_at=datetime.utcnow().isoformat() + "Z",
            discovery_version=DISCOVERY_VERSION,
        )

        for concept in _DISCOVERY_ORDER:
            sig = _CONCEPT_SIGNATURES[concept]
            table, confidence = self._best_table(concept, sig, tables, column_names,
                                                  fk_info, resolved)
            if table is None:
                logger.info("schema discovery: concept '%s' not found", concept)
                continue
            resolved[concept] = table
            cols = self._map_columns(concept, col_info[table], fk_info[table], resolved, table)
            tm = TableMapping(
                domain_concept=concept, actual_table=table, confidence=round(confidence, 3),
                columns=cols,
            )
            setattr(mapping, concept, tm)
            logger.info("schema discovery: %s -> %s (confidence %.2f)", concept, table, confidence)

        if not mapping.is_usable():
            logger.warning("schema discovery: no employees table found; mapping not usable")
        return mapping

    def discover_and_save(self, path: str | None = None, *, db_url_masked: str = "") -> SchemaMapping:
        m = self.discover(db_url_masked=db_url_masked)
        save_mapping(m, path)
        return m

    # --- internals --------------------------------------------------------------------------
    def _relevant_tables(self) -> list[str]:
        tables = self.inspector.get_table_names()
        return [
            t for t in tables
            if not t.startswith(("alembic_", "pg_", "sqlite_", "information_schema"))
            and t != "spatial_ref_sys"
        ]

    def _best_table(
        self,
        concept: str,
        sig: dict[str, Any],
        tables: list[str],
        column_names: dict[str, set[str]],
        fk_info: dict[str, list[dict[str, Any]]],
        resolved: dict[str, str | None],
    ) -> tuple[str | None, float]:
        scores: list[tuple[str, float]] = []
        for table in tables:
            score = self._score_table(concept, sig, table, column_names[table],
                                      fk_info[table], resolved)
            if score > 0.0:
                scores.append((table, score))
        if not scores:
            return None, 0.0
        scores.sort(key=lambda x: x[1], reverse=True)
        best_table, best_score = scores[0]
        # Require a minimum confidence so a random table is not misassigned.
        threshold = 0.35
        if best_score < threshold:
            return None, 0.0
        # If the top two are very close, prefer the one whose (stripped) name matches the concept.
        if len(scores) > 1 and best_score - scores[1][1] < 0.05:
            for t, _ in scores[:2]:
                if _matches_any(_strip_prefix(t), sig.get("name_patterns", [])):
                    return t, best_score
        return best_table, best_score

    def _score_table(
        self,
        concept: str,
        sig: dict[str, Any],
        table: str,
        cols: set[str],
        fks: list[dict[str, Any]],
        resolved: dict[str, str | None],
    ) -> float:
        name_score = 0.0
        stripped = _strip_prefix(table)
        if _matches_any(stripped, sig.get("name_patterns", [])):
            name_score = 0.4
        elif _matches_any(table, sig.get("name_patterns", [])):
            name_score = 0.3

        # Column presence scoring
        strong = sig.get("strong", {})
        weak = sig.get("weak", {})
        strong_hits = 0
        strong_total = max(len(strong), 1)
        for _, patterns in strong.items():
            if any(any(re.search(p, c) for c in cols) for p in patterns):
                strong_hits += 1
        weak_hits = 0
        weak_total = max(len(weak), 1)
        for _, patterns in weak.items():
            if any(any(re.search(p, c) for c in cols) for p in patterns):
                weak_hits += 1

        col_score = 0.5 * (strong_hits / strong_total) + 0.15 * (weak_hits / weak_total)

        # FK graph scoring (junction tables need FKs to resolved parents)
        fk_score = 0.0
        expected_fks = sig.get("expected_fks", {})
        if expected_fks:
            referred = {f.get("referred_table") for f in fks}
            matched = 0
            for field, parent_concept in expected_fks.items():
                parent_table = resolved.get(parent_concept)
                if parent_table and parent_table in referred:
                    matched += 1
            fk_score = 0.35 * (matched / len(expected_fks))
            # Junction tables with no name match are identified almost entirely by FKs.
            if not strong and name_score == 0.0:
                col_score = 0.0  # don't reward unrelated column noise
                fk_score = 0.6 * (matched / len(expected_fks))

        total = name_score + col_score + fk_score
        # Cap at 1.0
        return min(total, 1.0)

    def _map_columns(
        self,
        concept: str,
        col_info: list[dict[str, Any]],
        fks: list[dict[str, Any]],
        resolved: dict[str, str | None],
        table: str,
    ) -> dict[str, ColumnMap]:
        """Map each domain field of the concept to the best actual column."""
        result: dict[str, ColumnMap] = {}
        cols_lower: dict[str, str] = {c["name"].lower(): c["name"] for c in col_info}

        # Build a FK map: local_col -> referred_table
        fk_map: dict[str, str] = {}
        for fk in fks:
            for local, _ in zip(fk.get("constrained_columns", []),
                                fk.get("referred_columns", [])):
                fk_map[local.lower()] = fk.get("referred_table", "")

        sig = _CONCEPT_SIGNATURES.get(concept, {})
        strong = sig.get("strong", {})
        weak = sig.get("weak", {})
        extra = _EXTRA_FIELDS.get(concept, {})
        primary_key = self._primary_key(table)

        for field in _DOMAIN_FIELDS.get(concept, []):
            candidates: list[tuple[str, float]] = []
            if field == "id" and primary_key:
                candidates.append((primary_key, 1.0))
            patterns: list[str] = strong.get(field, []) + weak.get(field, []) + extra.get(field, [])
            for pattern in patterns:
                for low, actual in cols_lower.items():
                    if re.search(pattern, low):
                        # Prefer shorter column names (exact-ish match) over long ones.
                        penalty = 0.05 if len(low) > len(pattern) * 2 else 0.0
                        candidates.append((actual, 1.0 - penalty))
                        break
            # FK fields: prefer a column that is an FK to the resolved parent table
            expected_fks = sig.get("expected_fks", {})
            if field in expected_fks:
                parent = resolved.get(expected_fks[field])
                for low, actual in cols_lower.items():
                    if low in fk_map and parent and fk_map[low] == parent:
                        candidates.append((actual, 1.0))
                    elif low in fk_map and field.endswith("_id") and low.endswith("_id"):
                        candidates.append((actual, 0.5))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                best_col, best_score = candidates[0]
                result[field] = ColumnMap(
                    domain_field=field, actual_column=best_col,
                    confidence=round(best_score, 3),
                )
            else:
                result[field] = ColumnMap(domain_field=field, actual_column=None,
                                           confidence=0.0, notes="not found")
        return result

    def _primary_key(self, table: str) -> str | None:
        try:
            pk = self.inspector.get_pk_constraint(table)
        except Exception:
            return None
        columns = pk.get("constrained_columns") or []
        return columns[0] if len(columns) == 1 else None

"""Safe SQL helpers and dynamic database schema discovery & mapping."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.core import SprintMatchError
from app.infrastructure.llm import ChatMessage, LLMError, LLMProvider

logger = logging.getLogger(__name__)


# ===========================================================================
# Safe SQL Identifier & Dialect Helpers
# ===========================================================================

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")


class MappingError(SprintMatchError):
    """The schema mapping cannot address the requested data (missing table, unusable column)."""

    code = "SCHEMA_MAPPING_ERROR"
    http_status = 422


class ReadOnlySourceError(SprintMatchError):
    """A write was attempted against the connected external database, which is read-only."""

    code = "READ_ONLY_SOURCE"
    http_status = 403


def quote(identifier: str) -> str:
    if not _IDENTIFIER.match(identifier):
        raise MappingError(f"Unsafe SQL identifier: {identifier[:60]!r}")
    return f'"{identifier}"'


def run(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]


def run_one(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = run(conn, sql, params)
    return rows[0] if rows else None


def dialect_of(engine: Engine) -> str:
    return engine.dialect.name

# ===========================================================================
# Schema Mapping Models & Persistence
# ===========================================================================

# Every domain concept the discovery may map. Order is the discovery order (parents first).
CONCEPTS: tuple[str, ...] = (
    "companies",
    "employees",
    "skills",
    "departments",
    "teams",
    "projects",
    "tasks",
    "leaves",
    "attendance",
    "employee_skills",
    "task_assignments",
    "task_required_skills",
    "project_members",
    "work_schedule",
    "holidays",
    "sprints",
    "task_outcomes",
    "timesheets",
)

# Data classes


@dataclass
class ColumnMap:
    """Maps one domain field to a real column name (or None if absent in this DB)."""

    domain_field: str
    actual_column: str | None
    confidence: float = 1.0  # 0-1
    notes: str = ""


@dataclass
class TableMapping:
    """Maps one domain concept (e.g. 'employees') to a real table and its columns."""

    domain_concept: str
    actual_table: str | None
    confidence: float = 1.0
    columns: dict[str, ColumnMap] = field(default_factory=dict)
    notes: str = ""

    # Convenience: return the mapped column name or the fallback
    def col(self, domain_field: str, fallback: str | None = None) -> str | None:
        cm = self.columns.get(domain_field)
        return cm.actual_column if cm else fallback


@dataclass
class SchemaMapping:
    """Complete mapping for all domain concepts the agent needs."""

    # Core domain tables
    employees: TableMapping = field(default_factory=lambda: TableMapping("employees", None))
    skills: TableMapping = field(default_factory=lambda: TableMapping("skills", None))
    employee_skills: TableMapping = field(default_factory=lambda: TableMapping("employee_skills", None))
    departments: TableMapping = field(default_factory=lambda: TableMapping("departments", None))
    teams: TableMapping = field(default_factory=lambda: TableMapping("teams", None))
    tasks: TableMapping = field(default_factory=lambda: TableMapping("tasks", None))
    task_assignments: TableMapping = field(default_factory=lambda: TableMapping("task_assignments", None))
    projects: TableMapping = field(default_factory=lambda: TableMapping("projects", None))
    companies: TableMapping = field(default_factory=lambda: TableMapping("companies", None))
    leaves: TableMapping = field(default_factory=lambda: TableMapping("leaves", None))
    attendance: TableMapping = field(default_factory=lambda: TableMapping("attendance", None))

    # Optional concepts: when absent the agent degrades gracefully (documented per repository method).
    work_schedule: TableMapping = field(default_factory=lambda: TableMapping("work_schedule", None))
    holidays: TableMapping = field(default_factory=lambda: TableMapping("holidays", None))
    project_members: TableMapping = field(default_factory=lambda: TableMapping("project_members", None))
    task_required_skills: TableMapping = field(
        default_factory=lambda: TableMapping("task_required_skills", None)
    )
    sprints: TableMapping = field(default_factory=lambda: TableMapping("sprints", None))
    task_outcomes: TableMapping = field(default_factory=lambda: TableMapping("task_outcomes", None))
    timesheets: TableMapping = field(default_factory=lambda: TableMapping("timesheets", None))

    # Discovery metadata
    source_db_url_masked: str = ""
    discovered_at: str = ""
    discovery_version: str = "1.0"
    manually_reviewed: bool = False
    notes: str = ""

    def available_concepts(self) -> list[str]:
        """Domain concepts for which a real table was found."""
        return [concept for concept in CONCEPTS if getattr(self, concept).actual_table is not None]

    def is_usable(self) -> bool:
        """Minimum: employees table must exist."""
        return self.employees.actual_table is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaMapping:
        m = cls()
        for concept in CONCEPTS:
            if concept in data and isinstance(data[concept], dict):
                raw = data[concept]
                cols = {
                    k: ColumnMap(**v) if isinstance(v, dict) else v for k, v in raw.get("columns", {}).items()
                }
                raw_copy = {**raw, "columns": cols}
                setattr(m, concept, TableMapping(**raw_copy))
        for meta_key in (
            "source_db_url_masked",
            "discovered_at",
            "discovery_version",
            "manually_reviewed",
            "notes",
        ):
            if meta_key in data:
                setattr(m, meta_key, data[meta_key])
        return m


# Persistence

_DEFAULT_MAPPING_FILE = Path("schema_mapping.json")


def save_mapping(mapping: SchemaMapping, path: Path | str | None = None) -> Path:
    p = Path(path) if path else _DEFAULT_MAPPING_FILE
    p.write_text(json.dumps(mapping.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("schema mapping saved to %s", p)
    return p


def load_mapping(path: Path | str | None = None) -> SchemaMapping | None:
    p = Path(path) if path else _DEFAULT_MAPPING_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        m = SchemaMapping.from_dict(data)
        logger.info("schema mapping loaded from %s", p)
        return m
    except Exception as exc:
        logger.warning("could not load schema mapping from %s: %s", p, exc)
        return None

# ===========================================================================
# Schema Discovery Engine
# ===========================================================================

DISCOVERY_VERSION = "2.0"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TECH_SAMPLE_RE = re.compile(
    r"\b(?:python|fastapi|django|react|next\.?js|typescript|javascript|flutter|dart|"
    r"postgres(?:ql)?|sql|docker|kubernetes|k8s|aws|terraform|redis|kafka|odoo|java|"
    r"spring|qa|playwright|cypress|linux|langchain|ml|ai)\b",
    re.IGNORECASE,
)

# For each domain concept: table-name patterns (regex) and the columns that indicate it.
_CONCEPT_SIGNATURES: dict[str, dict[str, Any]] = {
    "employees": {
        "name_patterns": [
            r"(^|_)employee(s)?($|_)",
            r"(^|_)staff($|_)",
            r"(^|_)worker(s)?($|_)",
            r"(^|_)hr_?employee(s)?($|_)",
            r"(^|_)hr_?staff($|_)",
            r"(^|_)developer(s)?($|_)",
            r"(^|_)engineer(s)?($|_)",
            r"(^|_)member(s)?($|_)",
            r"(^|_)team_?member(s)?($|_)",
            r"(^|_)people($|_)",
            r"(^|_)person(s|nel)?($|_)",
            r"(^|_)consultant(s)?($|_)",
            r"(^|_)talent(s)?($|_)",
            r"(^|_)resource(s)?($|_)",
            r"(^|_)roster($|_)",
            r"(^|_)res_users$",
            r"(^|_)user(s)?($|_)",
        ],
        "strong": {
            "employee_code": [
                r"emp(l)?(oyee)?_?code",
                r"emp_?no",
                r"staff_?(code|id|no)",
                r"^code$",
                r"emp_?id",
                r"matricule",
                r"^badge$",
                r"username",
                r"handle",
            ],
            "full_name": [
                r"full_?name",
                r"^name$",
                r"display_?name",
                r"employee_?name",
                r"staff_?name",
                r"member_?name",
                r"dev_?name",
                r"user_?name",
                r"person_?name",
                r"first_?name",
            ],
            "email": [r"email", r"e_?mail", r"mail", r"work_?email"],
        },
        "weak": {
            "job_title": [r"job_?title", r"role_?title", r"role_?name", r"position", r"title", r"role", r"function"],
            "hire_date": [r"hire_?date", r"joining_?date", r"start_?date", r"admission", r"joined_?at"],
            "employment_status": [r"employment_?status", r"active_?flag", r"is_?active", r"active", r"^status$", r"state"],
            "department_id": [r"department_?id", r"dept_?id"],
            "department_name": [r"^department$", r"dept_?name", r"department_?name", r"^dept$", r"^division$"],
            "team_id": [r"team_?id"],
            "team_name": [r"^team$", r"team_?name", r"^squad$", r"group_?name"],
            "company_id": [r"company_?id", r"tenant_?id", r"org_?id"],
            "experience_years": [r"experience", r"years_?exp", r"yrs_?exp", r"seniority"],
            "timezone": [r"timezone", r"time_?zone", r"tz"],
            "bio": [r"^bio$", r"summary", r"about", r"specialt(y|ies)", r"profile_?text", r"notes"],
            "weekly_hours": [r"weekly_?hours", r"capacity_?hours", r"available_?hours", r"max_?hours", r"hours_?per_?week"],
            "daily_hours": [r"daily_?hours", r"hours_?per_?day"],
            "rating": [r"^rating$", r"performance_?score", r"quality_?score"],
        },
        "expected_fks": {"department_id": "departments", "team_id": "teams"},
    },
    "skills": {
        "name_patterns": [r"(^|_)skill(s)?($|_)", r"(^|_)competenc(e|y)(s|ies)?($|_)", r"(^|_)capabilit(y|ies)($|_)"],
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
            r"(^|_)employee_?skill(s)?($|_)",
            r"(^|_)emp(l)?(oyee)?_?skill(s)?($|_)",
            r"(^|_)dev_?skill(s)?($|_)",
            r"(^|_)staff_?skill(s)?($|_)",
            r"(^|_)member_?skill(s)?($|_)",
            r"(^|_)person_?skill(s)?($|_)",
            r"(^|_)talent_?skill(s)?($|_)",
            r"(^|_)skill_?link(s)?($|_)",
            r"(^|_)hr_?employee_?skill(s)?($|_)",
            r"(^|_)user_?skill(s)?($|_)",
            r"(^|_)competenc(e|y)_?link($|_)",
        ],
        "strong": {},
        "weak": {
            "declared_level": [r"declared_?level", r"level", r"proficiency", r"rating", r"score"],
            "verification_status": [r"verif(ication|y)?_?status", r"verified", r"status"],
            "effective_level": [r"effective_?level"],
        },
        "expected_fks": {"employee_id": "employees", "skill_id": "skills"},
    },
    "departments": {
        "name_patterns": [
            r"(^|_)department(s)?($|_)",
            r"(^|_)dept(s)?($|_)",
            r"(^|_)hr_?department(s)?($|_)",
            r"(^|_)division(s)?($|_)",
        ],
        "strong": {
            "name": [r"^name$", r"department_?name", r"dept_?name", r"label"],
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
            r"(^|_)task(s)?($|_)",
            r"(^|_)ticket(s)?($|_)",
            r"(^|_)issue(s)?($|_)",
            r"(^|_)project_?task(s)?($|_)",
            r"(^|_)work_?item(s)?($|_)",
            r"(^|_)stor(y|ies)($|_)",
            r"(^|_)bug(s)?($|_)",
            r"(^|_)card(s)?($|_)",
            r"(^|_)deliverable(s)?($|_)",
            r"(^|_)job(s)?($|_)",
            r"(^|_)backlog_?item(s)?($|_)",
            r"(^|_)todo(s)?($|_)",
        ],
        "strong": {
            "title": [r"title", r"subject", r"^name$", r"task_?name", r"summary"],
            "description": [r"description", r"detail(s)?", r"body", r"content"],
        },
        "weak": {
            "task_code": [r"task_?code", r"^code$", r"ticket_?id", r"key", r"reference"],
            "status": [r"status", r"state", r"stage"],
            "priority": [r"priority", r"urgency"],
            "complexity": [r"complexity", r"difficulty", r"size", r"story_?points"],
            "estimated_hours": [r"estimated_?hours", r"est_?hrs?", r"estimate", r"planned_?hours", r"budget_?hours", r"eta", r"effort", r"^hours$"],
            "project_id": [r"project_?id", r"project"],
            "company_id": [r"company_?id", r"tenant_?id"],
            "task_category": [r"categor(y|ies)", r"type", r"task_?type"],
        },
        "expected_fks": {"project_id": "projects"},
    },
    "task_assignments": {
        "name_patterns": [
            r"(^|_)assignment(s)?($|_)",
            r"(^|_)task_?assignment(s)?($|_)",
            r"(^|_)task_?employee(s)?($|_)",
            r"(^|_)allocation(s)?($|_)",
            r"(^|_)task_?assignee(s)?($|_)",
        ],
        "strong": {},
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
            r"(^|_)compan(y|ies)($|_)",
            r"(^|_)organization(s)?($|_)",
            r"(^|_)org(s)?($|_)",
            r"(^|_)tenant(s)?($|_)",
            r"(^|_)res_?partner(s)?($|_)",
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
            r"(^|_)leave(s)?($|_)",
            r"(^|_)absence(s)?($|_)",
            r"(^|_)time_?off($|_)",
            r"(^|_)hr_?leave(s)?($|_)",
            r"(^|_)holiday(s)?_?request($|_)",
            r"(^|_)vacation(s)?($|_)",
        ],
        "strong": {},
        "weak": {
            "employee_id": [r"employee_?id", r"emp_?id", r"user_?id", r"staff_?id"],
            "start_date": [r"start_?date", r"from_?date", r"date_?from", r"begin", r"start_?at"],
            "end_date": [r"end_?date", r"to_?date", r"date_?to", r"until", r"end_?at"],
            "status": [r"status", r"state", r"approval"],
            "leave_type": [r"leave_?type", r"type", r"category"],
            "hours": [r"hours", r"duration"],
        },
        "expected_fks": {"employee_id": "employees"},
    },
    "attendance": {
        "name_patterns": [
            r"(^|_)attendance(s)?($|_)",
            r"(^|_)check_?in(s)?($|_)",
            r"(^|_)timesheet(s)?($|_)",
            r"(^|_)hr_?attendance(s)?($|_)",
            r"(^|_)presenc(e|es)($|_)",
        ],
        "strong": {},
        "weak": {
            "employee_id": [r"employee_?id", r"emp_?id", r"user_?id", r"staff_?id"],
            "check_in": [r"check_?in", r"start", r"punch_?in", r"arrival"],
            "check_out": [r"check_?out", r"end", r"punch_?out", r"departure"],
            "date": [r"^date$", r"day", r"attendance_?date", r"work_?date"],
        },
        "expected_fks": {"employee_id": "employees"},
    },
    "task_required_skills": {
        "name_patterns": [
            r"(^|_)task_?skill(s)?($|_)",
            r"(^|_)task_?requirement(s)?($|_)",
            r"(^|_)task_?required_?skill(s)?($|_)",
            r"(^|_)required_?skill(s)?($|_)",
        ],
        "strong": {},
        "weak": {
            "minimum_level": [r"minimum_?level", r"min_?level", r"required_?level", r"level"],
            "mandatory": [r"mandatory", r"is_?required", r"required"],
            "weight": [r"weight", r"importance"],
        },
        "expected_fks": {"task_id": "tasks", "skill_id": "skills"},
    },
    "project_members": {
        "name_patterns": [
            r"(^|_)project_?member(s)?($|_)",
            r"(^|_)project_?employee(s)?($|_)",
            r"(^|_)project_?assignment(s)?($|_)",
            r"(^|_)member(s)?_?project($|_)",
        ],
        "strong": {},
        "weak": {
            "role": [r"^role$", r"position", r"function"],
        },
        "expected_fks": {"project_id": "projects", "employee_id": "employees"},
    },
    "work_schedule": {
        "name_patterns": [
            r"(^|_)schedule(s)?_?day(s)?($|_)",
            r"(^|_)work_?schedule(s)?($|_)",
            r"(^|_)working_?hour(s)?($|_)",
            r"(^|_)shift(s)?($|_)",
        ],
        "strong": {},
        "weak": {
            "employee_id": [r"employee_?id", r"emp_?id", r"user_?id", r"staff_?id"],
            "weekday": [r"weekday", r"day_?of_?week", r"^day$"],
            "start_time": [r"start_?time", r"^start$", r"from_?time"],
            "end_time": [r"end_?time", r"^end$", r"to_?time"],
            "break_minutes": [r"break", r"lunch", r"pause"],
        },
        "expected_fks": {"employee_id": "employees"},
    },
    "holidays": {
        "name_patterns": [r"(^|_)holiday(s)?($|_)", r"(^|_)public_?holiday(s)?($|_)", r"(^|_)day_?off($|_)"],
        "strong": {},
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
            r"(^|_)task_?outcome(s)?($|_)",
            r"(^|_)outcome(s)?($|_)",
            r"(^|_)review(s)?($|_)",
            r"(^|_)feedback($|_)",
            r"(^|_)task_?review(s)?($|_)",
        ],
        "strong": {},
        "weak": {
            "quality_score": [r"quality", r"score", r"rating"],
            "accepted": [r"accepted", r"approved", r"is_?accepted"],
            "rework_count": [r"rework", r"reopen"],
            "on_time": [r"on_?time", r"delayed", r"late"],
            "reviewed_at": [r"reviewed_?at", r"reviewed_?date", r"^date$", r"created_?at"],
            "employee_id": [r"employee_?id", r"assignee_?id", r"user_?id", r"staff_?id"],
        },
        "expected_fks": {"task_id": "tasks", "employee_id": "employees"},
    },
    "timesheets": {
        "name_patterns": [
            r"(^|_)timesheet(s)?($|_)",
            r"(^|_)time_?entr(y|ies)($|_)",
            r"(^|_)worklog(s)?($|_)",
            r"(^|_)hour_?log(s)?($|_)",
        ],
        "strong": {},
        "weak": {
            "task_id": [r"task_?id", r"task"],
            "employee_id": [r"employee_?id", r"user_?id", r"emp_?id", r"staff_?id"],
            "hours": [r"^hours$", r"duration", r"time_?spent", r"logged_?hours"],
            "work_date": [r"work_?date", r"^date$", r"day"],
        },
        "expected_fks": {"task_id": "tasks", "employee_id": "employees"},
    },
}

_DISCOVERY_ORDER = [
    "employees",
    "companies",
    "skills",
    "departments",
    "teams",
    "projects",
    "tasks",
    "sprints",
    "leaves",
    "attendance",
    "employee_skills",
    "task_assignments",
    "task_required_skills",
    "project_members",
    "work_schedule",
    "holidays",
    "task_outcomes",
    "timesheets",
]

_DOMAIN_FIELDS: dict[str, list[str]] = {
    "employees": [
        "id",
        "employee_code",
        "full_name",
        "email",
        "job_title",
        "hire_date",
        "employment_status",
        "department_id",
        "department_name",
        "team_id",
        "team_name",
        "company_id",
        "experience_years",
        "timezone",
        "skills_text",
        "bio",
        "weekly_hours",
        "daily_hours",
        "rating",
    ],
    "skills": ["id", "name", "category", "aliases"],
    "employee_skills": [
        "employee_id",
        "skill_id",
        "declared_level",
        "verification_status",
        "effective_level",
    ],
    "departments": ["id", "name", "company_id"],
    "teams": ["id", "name", "department_id", "company_id"],
    "tasks": [
        "id",
        "task_code",
        "title",
        "description",
        "status",
        "priority",
        "complexity",
        "estimated_hours",
        "remaining_estimated_hours",
        "planned_start",
        "deadline",
        "acceptance_criteria",
        "project_id",
        "company_id",
        "task_category",
        "assignee_id",
        "skills_text",
        "completed_at",
        "quality_score",
    ],
    "task_assignments": ["task_id", "employee_id", "assignment_status", "allocated_hours", "assigned_at"],
    "task_required_skills": ["task_id", "skill_id", "minimum_level", "mandatory", "weight"],
    "project_members": ["project_id", "employee_id", "role"],
    "projects": ["id", "code", "name", "description", "company_id"],
    "companies": ["id", "code", "name", "timezone"],
    "leaves": ["employee_id", "start_date", "end_date", "status", "leave_type", "hours"],
    "attendance": ["employee_id", "check_in", "check_out", "date"],
    "work_schedule": ["employee_id", "weekday", "start_time", "end_time", "break_minutes"],
    "holidays": ["holiday_date", "name", "company_id"],
    "sprints": ["id", "name", "start_date", "end_date", "project_id", "status"],
    "task_outcomes": [
        "task_id",
        "employee_id",
        "quality_score",
        "accepted",
        "rework_count",
        "on_time",
        "reviewed_at",
    ],
    "timesheets": ["task_id", "employee_id", "hours", "work_date"],
}

_EXTRA_FIELDS: dict[str, dict[str, list[str]]] = {
    "employees": {
        "skills_text": [
            r"skills?($|_)",
            r"tech_?stack",
            r"stack($|_)",
            r"competenc",
            r"technolog",
            r"expertise",
            r"capabilit",
            r"^tags$",
        ],
        "id": [r"^id$", r"^uuid$", r"^pk$", r"employee_?id", r"emp_?no", r"staff_?id", r"member_?id", r"dev_?id", r"user_?id"],
    },
    "tasks": {
        "id": [r"^id$", r"^uuid$", r"^pk$", r"task_?id", r"^issue_?id$", r"item_?id", r"ticket_?id"],
        "planned_start": [r"planned_?start", r"start_?date", r"scheduled_?start", r"start_?at"],
        "deadline": [r"deadline", r"due_?date", r"due_?at", r"end_?date", r"target_?date"],
        "remaining_estimated_hours": [r"remaining_?hours", r"remaining_?estimate", r"time_?remaining"],
        "acceptance_criteria": [r"acceptance_?criteria", r"definition_?of_?done", r"^dod$"],
        "assignee_id": [
            r"assignee_?id",
            r"assigned_?to",
            r"employee_?id",
            r"owner_?id",
            r"developer_?id",
            r"member_?id",
            r"user_?id",
            r"assignee_?ref",
            r"^assignee$",
        ],
        "skills_text": [r"required_?skills?", r"skills?($|_)", r"tech_?stack", r"^tags$"],
        "completed_at": [r"completed_?at", r"closed_?at", r"finished_?at", r"done_?at"],
        "quality_score": [r"quality_?score", r"review_?score", r"rating"],
    },
    "skills": {"id": [r"^id$", r"^uuid$", r"^pk$", r"skill_?id"]},
    "projects": {"id": [r"^id$", r"^uuid$", r"^pk$", r"project_?id"]},
    "departments": {"id": [r"^id$", r"^uuid$", r"^pk$", r"department_?id", r"dept_?id"]},
    "teams": {"id": [r"^id$", r"^uuid$", r"^pk$", r"team_?id"]},
    "companies": {"id": [r"^id$", r"^uuid$", r"^pk$", r"company_?id"]},
    "sprints": {"id": [r"^id$", r"^uuid$", r"^pk$", r"sprint_?id"]},
}


class LLMSchemaTableSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    concept: str = Field(min_length=1, max_length=60)
    table: str = Field(min_length=1, max_length=120)
    columns: dict[str, str] = Field(default_factory=dict)


class LLMSchemaRefinementOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mappings: list[LLMSchemaTableSuggestion] = Field(default_factory=list, max_length=18)


def _matches_any(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(re.search(p, low) for p in patterns)


def _strip_prefix(table: str) -> str:
    """Remove common ORM/ERP prefixes (wp_, hr_, project_, res_, account_) for name matching."""
    low = table.lower()
    for prefix in ("wp_", "hr_", "project_", "res_", "account_", "mail_", "ir_"):
        if low.startswith(prefix):
            return table[len(prefix) :]
    return table


class SchemaDiscovery:
    """Inspects a connected database and produces a SchemaMapping."""

    def __init__(self, engine: Engine, *, llm: LLMProvider | None = None) -> None:
        self.engine = engine
        self.inspector = inspect(engine)
        self.llm = llm

    def discover(self, *, db_url_masked: str = "") -> SchemaMapping:
        tables = self._relevant_tables()
        if not tables:
            logger.warning("schema discovery: no tables found")
            return SchemaMapping(
                source_db_url_masked=db_url_masked,
                discovered_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                discovery_version=DISCOVERY_VERSION,
            )

        col_info: dict[str, list[dict[str, Any]]] = {
            t: [dict(c) for c in self.inspector.get_columns(t)] for t in tables
        }
        fk_info: dict[str, list[dict[str, Any]]] = {
            t: [dict(fk) for fk in self.inspector.get_foreign_keys(t)] for t in tables
        }
        column_names: dict[str, set[str]] = {
            t: {c["name"].lower() for c in cols} for t, cols in col_info.items()
        }
        sample_profiles = self._sample_column_profiles(tables)

        resolved: dict[str, str | None] = {}
        claimed: set[str] = set()
        mapping = SchemaMapping(
            source_db_url_masked=db_url_masked,
            discovered_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            discovery_version=DISCOVERY_VERSION,
        )

        for concept in _DISCOVERY_ORDER:
            sig = _CONCEPT_SIGNATURES[concept]
            table, confidence = self._best_table(
                concept, sig, tables, column_names, fk_info, resolved, claimed, sample_profiles
            )
            if table is None:
                logger.info("schema discovery: concept '%s' not found", concept)
                continue
            claimed.add(table)
            resolved[concept] = table
            cols = self._map_columns(
                concept,
                col_info[table],
                fk_info[table],
                resolved,
                table,
                sample_profiles.get(table, {}),
            )
            tm = TableMapping(
                domain_concept=concept,
                actual_table=table,
                confidence=round(confidence, 3),
                columns=cols,
            )
            setattr(mapping, concept, tm)
            logger.info("schema discovery: %s -> %s (confidence %.2f)", concept, table, confidence)

        if self.llm is not None and self._needs_llm_refinement(mapping, tables, claimed):
            self._refine_with_llm(mapping, tables, col_info, fk_info, sample_profiles, claimed)

        if not mapping.is_usable():
            logger.warning("schema discovery: no employees table found; mapping not usable")
        return mapping

    def discover_and_save(self, path: str | None = None, *, db_url_masked: str = "") -> SchemaMapping:
        m = self.discover(db_url_masked=db_url_masked)
        save_mapping(m, path)
        return m

    def _relevant_tables(self) -> list[str]:
        tables = self.inspector.get_table_names()
        return [
            t
            for t in tables
            if not t.startswith(("alembic_", "pg_", "sqlite_", "information_schema"))
            and t != "spatial_ref_sys"
        ]

    def _sample_column_profiles(self, tables: list[str]) -> dict[str, dict[str, set[str]]]:
        """Privacy-safe structural profiling of up to 5 rows per table (tags only, no PII kept)."""
        profiles: dict[str, dict[str, set[str]]] = {}
        try:
            with self.engine.connect() as conn:
                for table in tables[:40]:
                    tags_by_col: dict[str, set[str]] = {}
                    try:
                        rows = conn.execute(text(f"SELECT * FROM {quote(table)} LIMIT 5")).mappings().all()
                    except Exception:
                        profiles[table] = tags_by_col
                        continue
                    for row in rows:
                        for col_name, val in row.items():
                            if val is None:
                                continue
                            tags = tags_by_col.setdefault(col_name.lower(), set())
                            if isinstance(val, (list, dict)):
                                tags.add("list_text")
                                continue
                            if isinstance(val, bool):
                                tags.add("bool_status")
                                continue
                            if isinstance(val, (int, float)):
                                tags.add("numeric")
                                continue
                            s = str(val).strip()
                            if not s:
                                continue
                            if _EMAIL_RE.match(s):
                                tags.add("email")
                            if (
                                (s.startswith("[") and s.endswith("]"))
                                or (s.startswith("{") and s.endswith("}"))
                                or (("," in s or ";" in s or "|" in s) and (_TECH_SAMPLE_RE.search(s) or s.count(",") >= 1))
                            ):
                                tags.add("list_text")
                            if _TECH_SAMPLE_RE.search(s):
                                tags.add("tech_hits")
                            if re.match(r"^\d{4}-\d{2}-\d{2}", s):
                                tags.add("date")
                    profiles[table] = tags_by_col
        except Exception:
            logger.debug("sample column profiling skipped", exc_info=True)
        return profiles

    def _best_table(
        self,
        concept: str,
        sig: dict[str, Any],
        tables: list[str],
        column_names: dict[str, set[str]],
        fk_info: dict[str, list[dict[str, Any]]],
        resolved: dict[str, str | None],
        claimed: set[str],
        sample_profiles: dict[str, dict[str, set[str]]],
    ) -> tuple[str | None, float]:
        scores: list[tuple[str, float]] = []
        for table in tables:
            if table in claimed:
                continue
            score = self._score_table(
                concept,
                sig,
                table,
                column_names[table],
                fk_info[table],
                resolved,
                sample_profiles.get(table, {}),
            )
            if score > 0.0:
                scores.append((table, score))
        if not scores:
            return None, 0.0
        scores.sort(key=lambda x: x[1], reverse=True)
        best_table, best_score = scores[0]
        threshold = 0.35
        if best_score < threshold:
            return None, 0.0
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
        col_profiles: dict[str, set[str]],
    ) -> float:
        name_score = 0.0
        stripped = _strip_prefix(table)
        if _matches_any(stripped, sig.get("name_patterns", [])):
            name_score = 0.4
        elif _matches_any(table, sig.get("name_patterns", [])):
            name_score = 0.3

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
        # Prevent generic `name`-only concepts from claiming unrelated tables just
        # because that single strong pattern (`name`) matched without table-name or weak clues.
        if (
            name_score == 0.0
            and weak_hits == 0
            and len(strong) <= 1
            and not sig.get("expected_fks")
        ):
            col_score *= 0.5

        # Structural sample-value signal boost (never dominates over real schemas)
        shape_boost = 0.0
        all_tags = {tag for tags in col_profiles.values() for tag in tags}
        if concept == "employees":
            if "email" in all_tags:
                shape_boost += 0.1
            if "tech_hits" in all_tags and "list_text" in all_tags:
                shape_boost += 0.08
        elif concept == "skills" and "tech_hits" in all_tags and "email" not in all_tags:
            shape_boost += 0.08

        fk_score = 0.0
        expected_fks = sig.get("expected_fks", {})
        if expected_fks:
            referred = {f.get("referred_table") for f in fks}
            matched = 0
            for _field, parent_concept in expected_fks.items():
                parent_table = resolved.get(parent_concept)
                if parent_table and parent_table in referred:
                    matched += 1
            fk_score = 0.35 * (matched / len(expected_fks))
            if not strong and name_score == 0.0:
                col_score = 0.0
                fk_score = 0.6 * (matched / len(expected_fks))

        total = name_score + col_score + fk_score + shape_boost
        return min(total, 1.0)

    def _map_columns(
        self,
        concept: str,
        col_info: list[dict[str, Any]],
        fks: list[dict[str, Any]],
        resolved: dict[str, str | None],
        table: str,
        col_profiles: dict[str, set[str]] | None = None,
    ) -> dict[str, ColumnMap]:
        """Map each domain field of the concept to the best actual column."""
        result: dict[str, ColumnMap] = {}
        cols_lower: dict[str, str] = {c["name"].lower(): c["name"] for c in col_info}
        profiles = col_profiles or {}

        fk_map: dict[str, str] = {}
        for fk in fks:
            for local, _ in zip(fk.get("constrained_columns", []), fk.get("referred_columns", [])):
                fk_map[local.lower()] = fk.get("referred_table", "")

        sig = _CONCEPT_SIGNATURES.get(concept, {})
        strong = sig.get("strong", {})
        weak = sig.get("weak", {})
        extra = _EXTRA_FIELDS.get(concept, {})
        primary_key = self._primary_key(table)
        expected_fks = dict(sig.get("expected_fks", {}))
        if concept == "tasks":
            expected_fks.setdefault("assignee_id", "employees")

        for domain_field in _DOMAIN_FIELDS.get(concept, []):
            candidates: list[tuple[str, float]] = []
            if domain_field == "id" and primary_key:
                candidates.append((primary_key, 1.0))
            patterns: list[str] = (
                strong.get(domain_field, []) + weak.get(domain_field, []) + extra.get(domain_field, [])
            )
            for pattern in patterns:
                for low, actual in cols_lower.items():
                    if re.search(pattern, low):
                        penalty = 0.05 if len(low) > len(pattern) * 2 else 0.0
                        candidates.append((actual, 1.0 - penalty))
                        break
            if domain_field in expected_fks:
                parent = resolved.get(expected_fks[domain_field])
                for low, actual in cols_lower.items():
                    if low in fk_map and parent and fk_map[low] == parent:
                        candidates.append((actual, 1.0))
                    elif low in fk_map and domain_field.endswith("_id") and low.endswith("_id"):
                        candidates.append((actual, 0.5))

            if not candidates:
                for low, actual in cols_lower.items():
                    tags = profiles.get(low, set())
                    if domain_field == "email" and "email" in tags:
                        candidates.append((actual, 0.78))
                    elif domain_field == "skills_text" and "list_text" in tags and "tech_hits" in tags:
                        candidates.append((actual, 0.75))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                best_col, best_score = candidates[0]
                result[domain_field] = ColumnMap(
                    domain_field=domain_field,
                    actual_column=best_col,
                    confidence=round(best_score, 3),
                )
            else:
                result[domain_field] = ColumnMap(
                    domain_field=domain_field, actual_column=None, confidence=0.0, notes="not found"
                )
        return result

    def _primary_key(self, table: str) -> str | None:
        try:
            pk = self.inspector.get_pk_constraint(table)
        except Exception:
            return None
        columns = pk.get("constrained_columns") or []
        return columns[0] if len(columns) == 1 else None

    def _needs_llm_refinement(
        self, mapping: SchemaMapping, tables: list[str], claimed: set[str]
    ) -> bool:
        if not mapping.is_usable():
            return True
        if mapping.employees.confidence < 0.75:
            return True
        if mapping.tasks.actual_table is not None and mapping.tasks.confidence < 0.75:
            return True
        unclaimed = [t for t in tables if t not in claimed]
        return bool(unclaimed) and (
            mapping.tasks.actual_table is None
            or (mapping.skills.actual_table is None and mapping.employees.col("skills_text") is None)
        )

    def _refine_with_llm(
        self,
        mapping: SchemaMapping,
        tables: list[str],
        col_info: dict[str, list[dict[str, Any]]],
        fk_info: dict[str, list[dict[str, Any]]],
        sample_profiles: dict[str, dict[str, set[str]]],
        claimed: set[str],
    ) -> None:
        assert self.llm is not None
        schema_summary = []
        for t in tables[:30]:
            cols = [
                {
                    "name": c["name"],
                    "type": str(c.get("type", "")),
                    "tags": sorted(sample_profiles.get(t, {}).get(c["name"].lower(), set())),
                }
                for c in col_info.get(t, [])[:25]
            ]
            fks = [
                {
                    "cols": fk.get("constrained_columns", []),
                    "ref": fk.get("referred_table"),
                }
                for fk in fk_info.get(t, [])
            ]
            schema_summary.append({"table": t, "columns": cols, "fks": fks})

        system = (
            "You are a database schema alignment assistant. Map tables and columns from an arbitrary SQL database "
            f"to these SprintMatch domain concepts: {', '.join(CONCEPTS)}.\n"
            "Rules:\n"
            "- Only use exact table and column names present in the provided schema.\n"
            "- Never invent tables or columns.\n"
            "- Only return mappings you are genuinely confident about; omit absent concepts."
        )
        user_msg = json.dumps({"schema": schema_summary, "domain_fields": _DOMAIN_FIELDS}, ensure_ascii=False)
        try:
            out = self.llm.generate_structured(
                [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user_msg)],
                LLMSchemaRefinementOutput,
                schema_name="schema_refinement",
            ).value
        except LLMError as exc:
            logger.warning("LLM schema refinement skipped: %s", exc)
            return

        valid_tables = set(tables)
        for item in out.mappings:
            if item.concept not in CONCEPTS or item.table not in valid_tables:
                continue
            current_tm: TableMapping = getattr(mapping, item.concept)
            if current_tm.actual_table is not None and current_tm.confidence >= 0.85:
                # Only fill unmapped columns inside the same table when heuristic already has high confidence.
                if item.table == current_tm.actual_table:
                    self._merge_valid_columns(current_tm, item.columns, col_info[item.table])
                continue
            if current_tm.actual_table is None and item.table in claimed:
                continue
            valid_cols = {c["name"] for c in col_info.get(item.table, [])}
            mapped_cols = dict(current_tm.columns) if current_tm.actual_table == item.table else {}
            if not mapped_cols:
                for df in _DOMAIN_FIELDS.get(item.concept, []):
                    mapped_cols[df] = ColumnMap(domain_field=df, actual_column=None, confidence=0.0, notes="not found")
            self._merge_valid_columns_dict(mapped_cols, item.columns, valid_cols, item.concept)
            if current_tm.actual_table is None and mapped_cols:
                pk = self._primary_key(item.table)
                if "id" in mapped_cols and mapped_cols["id"].actual_column is None and pk:
                    mapped_cols["id"] = ColumnMap(domain_field="id", actual_column=pk, confidence=0.9)
                claimed.add(item.table)
                setattr(
                    mapping,
                    item.concept,
                    TableMapping(
                        domain_concept=item.concept,
                        actual_table=item.table,
                        confidence=0.72,
                        columns=mapped_cols,
                        notes="llm_refined",
                    ),
                )
            elif current_tm.actual_table == item.table:
                current_tm.columns = mapped_cols

    def _merge_valid_columns(
        self, tm: TableMapping, suggested: dict[str, str], table_cols: list[dict[str, Any]]
    ) -> None:
        valid_cols = {c["name"] for c in table_cols}
        self._merge_valid_columns_dict(tm.columns, suggested, valid_cols, tm.domain_concept)

    @staticmethod
    def _merge_valid_columns_dict(
        target: dict[str, ColumnMap],
        suggested: dict[str, str],
        valid_cols: set[str],
        concept: str,
    ) -> None:
        allowed_fields = set(_DOMAIN_FIELDS.get(concept, []))
        valid_lower = {c.lower(): c for c in valid_cols}
        for domain_field, raw_col in suggested.items():
            if domain_field not in allowed_fields or not isinstance(raw_col, str):
                continue
            actual_col = valid_lower.get(raw_col.strip().lower())
            if actual_col is None:
                continue
            existing = target.get(domain_field)
            if existing is None or existing.actual_column is None:
                target[domain_field] = ColumnMap(
                    domain_field=domain_field,
                    actual_column=actual_col,
                    confidence=0.72,
                    notes="llm_refined",
                )

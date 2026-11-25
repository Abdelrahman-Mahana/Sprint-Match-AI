"""Read-only adaptive repositories, Unit of Work, and native-vs-adaptive data source resolver."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.core import Settings
from app.domain import (
    Assignment,
    AssignmentStatus,
    AttendanceRecord,
    AttendanceStatus,
    AuditRepository,
    Company,
    CompanyRepository,
    Employee,
    EmployeeProfile,
    EmployeeRepository,
    EmployeeSkill,
    EmployeeWorkforceData,
    EmploymentStatus,
    EvaluationSummary,
    ExecutionTraceEntry,
    Holiday,
    LeaveRecord,
    LeaveStatus,
    LeaveType,
    NewTask,
    NewTaskOutcome,
    Page,
    PerformanceEvaluation,
    PerformanceMetricSnapshot,
    PerformanceRepository,
    ProjectMembership,
    RecommendationRepository,
    RecommendationRunRecord,
    RequiredSkill,
    RequirementSource,
    RunErrorRecord,
    ScheduleDay,
    Skill,
    SkillVerificationStatus,
    Sprint,
    StoredRecommendation,
    TaskCategory,
    TaskComplexity,
    TaskOutcomeRecord,
    TaskPriority,
    TaskRecord,
    TaskRepository,
    TaskStatus,
    ToolExecutionRecord,
    UnitOfWork,
    UnitOfWorkFactory,
    WorkforceRepository,
)
from app.infrastructure.discovery import (
    MappingError,
    ReadOnlySourceError,
    SchemaDiscovery,
    SchemaMapping,
    TableMapping,
    dialect_of,
    load_mapping,
    quote,
    run,
    run_one,
    save_mapping,
)
from app.infrastructure.llm import LLMProvider
from app.infrastructure.orm import create_session_factory
from app.infrastructure.repositories import SqlAlchemyUnitOfWorkFactory

logger = logging.getLogger(__name__)

# ===========================================================================
# Deterministic External ID Registry
# ===========================================================================

_MAX_REGISTRY_ENTRIES = 200_000


def raw_to_uuid(concept: str, raw: Any) -> UUID | None:
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return UUID(text)
    except (ValueError, AttributeError):
        return uuid5(NAMESPACE_URL, f"foreign-db/{concept}/{text}")




class IdRegistry:
    """Thread-safe ``UUID -> raw id`` map per concept, filled as rows are read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._maps: dict[str, dict[UUID, str]] = {}

    def remember(self, concept: str, raw: Any) -> UUID | None:
        uid = raw_to_uuid(concept, raw)
        if uid is None:
            return None
        text = str(raw).strip()
        with self._lock:
            mapping = self._maps.setdefault(concept, {})
            if len(mapping) >= _MAX_REGISTRY_ENTRIES:
                # Bounded: drop the oldest half rather than grow without limit.
                for key in list(mapping)[: _MAX_REGISTRY_ENTRIES // 2]:
                    del mapping[key]
            mapping[uid] = text
        return uid

    def resolve(self, concept: str, uid: UUID | None) -> str | None:
        if uid is None:
            return None
        with self._lock:
            return self._maps.get(concept, {}).get(uid)

    def resolve_or_str(self, concept: str, uid: UUID | None) -> str | None:
        if uid is None:
            return None
        return self.resolve(concept, uid) or str(uid)

    def clear(self) -> None:
        with self._lock:
            self._maps.clear()

# ===========================================================================
# Value & Enum Coercion
# ===========================================================================

E = TypeVar("E")

_TRUE = {"1", "true", "t", "yes", "y", "on", "active", "enabled"}


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any, default: int | None = None) -> int | None:
    f = as_float(value)
    return round(f) if f is not None else default


def as_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in {"0", "false", "f", "no", "n", "off", "inactive", "disabled"}:
        return False
    return default


def as_text(value: Any, max_len: int | None = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len] if max_len else text


# Datetime handling (naive values are assumed to be UTC, which is safe for ordering)

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%Y%m%d",
    "%d.%m.%Y",
)

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M",
    "%Y/%m/%d %H:%M:%S",
)


def as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(text, fmt).replace(tzinfo=UTC).date()
            return datetime(d.year, d.month, d.day, tzinfo=UTC)
        except ValueError:
            continue
    return None


def as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    dt = as_datetime(value)
    return dt.date() if dt else None


def as_time(value: Any) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    text = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).time()
        except ValueError:
            continue
    return None


# Enum coercion


def _enum_by_value[E](enum_cls: type[E], value: str) -> E | None:
    try:
        return enum_cls(value)  # type: ignore[call-arg]
    except ValueError:
        return None


def employment_status(
    value: Any,
    *,
    default_if_missing: EmploymentStatus = EmploymentStatus.INACTIVE,
) -> EmploymentStatus:
    raw = as_text(value, 40)
    if raw is None:
        return default_if_missing
    text = raw.upper()
    if text in EmploymentStatus.__members__:
        return EmploymentStatus[text]
    low = text.lower()
    if (
        low in {"active", "employed", "1", "true", "on", "working", "enabled", "available", "نشط", "متاح", "يعمل"}
        or as_bool(value) is True
    ):
        return EmploymentStatus.ACTIVE
    if low in {"suspended", "on_hold", "hold", "leave", "on_leave", "maternity", "parental", "إجازة", "موقوف"}:
        return EmploymentStatus.SUSPENDED
    if low in {
        "inactive",
        "terminated",
        "left",
        "resigned",
        "dismissed",
        "retired",
        "archived",
        "0",
        "false",
        "disabled",
        "غير نشط",
        "مستقيل",
        "منتهي",
    }:
        return EmploymentStatus.INACTIVE
    # Unknown text: never grant capacity on an interpretation we cannot justify.
    return EmploymentStatus.INACTIVE


_TASK_STATUS_ALIASES: dict[str, TaskStatus] = {
    "new": TaskStatus.BACKLOG,
    "open": TaskStatus.TODO,
    "to_do": TaskStatus.TODO,
    "todo": TaskStatus.TODO,
    "to do": TaskStatus.TODO,
    "ready": TaskStatus.TODO,
    "planned": TaskStatus.TODO,
    "in_progress": TaskStatus.IN_PROGRESS,
    "in progress": TaskStatus.IN_PROGRESS,
    "doing": TaskStatus.IN_PROGRESS,
    "in_review": TaskStatus.IN_REVIEW,
    "review": TaskStatus.IN_REVIEW,
    "testing": TaskStatus.IN_REVIEW,
    "done": TaskStatus.DONE,
    "closed": TaskStatus.DONE,
    "completed": TaskStatus.DONE,
    "finished": TaskStatus.DONE,
    "resolved": TaskStatus.DONE,
    "cancelled": TaskStatus.CANCELLED,
    "canceled": TaskStatus.CANCELLED,
    "rejected": TaskStatus.CANCELLED,
}


def task_status(value: Any) -> TaskStatus:
    text = (as_text(value, 40) or "").upper()
    if text in TaskStatus.__members__:
        return TaskStatus[text]
    return _TASK_STATUS_ALIASES.get(text.lower().replace("-", "_"), TaskStatus.TODO)


def task_priority(value: Any) -> TaskPriority | None:
    text = (as_text(value, 40) or "").lower()
    if not text:
        return None
    aliases = {
        "normal": TaskPriority.MEDIUM,
        "medium": TaskPriority.MEDIUM,
        "moderate": TaskPriority.MEDIUM,
        "high": TaskPriority.HIGH,
        "important": TaskPriority.HIGH,
        "urgent": TaskPriority.URGENT,
        "critical": TaskPriority.URGENT,
        "blocker": TaskPriority.URGENT,
        "highest": TaskPriority.URGENT,
        "low": TaskPriority.LOW,
        "minor": TaskPriority.LOW,
        "lowest": TaskPriority.LOW,
        "trivial": TaskPriority.LOW,
    }
    by_value = _enum_by_value(TaskPriority, text)
    return by_value or aliases.get(text)


def task_complexity(value: Any) -> TaskComplexity | None:
    text = (as_text(value, 40) or "").lower()
    if not text:
        return None
    # Numeric story points map onto the documented four levels.
    points = as_float(value)
    if points is not None and not any(c.isalpha() for c in text):
        if points <= 2:
            return TaskComplexity.LOW
        if points <= 5:
            return TaskComplexity.MEDIUM
        if points <= 13:
            return TaskComplexity.HIGH
        return TaskComplexity.CRITICAL
    aliases = {
        "trivial": TaskComplexity.LOW,
        "easy": TaskComplexity.LOW,
        "simple": TaskComplexity.LOW,
        "medium": TaskComplexity.MEDIUM,
        "normal": TaskComplexity.MEDIUM,
        "moderate": TaskComplexity.MEDIUM,
        "hard": TaskComplexity.HIGH,
        "high": TaskComplexity.HIGH,
        "complex": TaskComplexity.HIGH,
        "critical": TaskComplexity.CRITICAL,
        "very_high": TaskComplexity.CRITICAL,
        "blocker": TaskComplexity.CRITICAL,
    }
    return _enum_by_value(TaskComplexity, text) or aliases.get(text)


def task_category(value: Any) -> TaskCategory | None:
    text = (as_text(value, 60) or "").lower().replace("-", "_").replace(" ", "_")
    return _enum_by_value(TaskCategory, text)


def assignment_status(value: Any) -> AssignmentStatus:
    text = (as_text(value, 40) or "").upper()
    if text in AssignmentStatus.__members__:
        return AssignmentStatus[text]
    low = text.lower()
    if low in {"active", "in_progress", "doing", "started", "running", "open"}:
        return AssignmentStatus.ACTIVE
    if low in {"completed", "done", "closed", "finished", "delivered"}:
        return AssignmentStatus.COMPLETED
    if low in {"cancelled", "canceled", "rejected", "dropped"}:
        return AssignmentStatus.CANCELLED
    return AssignmentStatus.PLANNED


def leave_status(value: Any) -> LeaveStatus:
    text = (as_text(value, 40) or "").upper()
    if text in LeaveStatus.__members__:
        return LeaveStatus[text]
    low = text.lower()
    if low in {"approved", "accepted", "confirmed", "validate"}:
        return LeaveStatus.APPROVED
    if low in {"rejected", "refused", "denied"}:
        return LeaveStatus.REJECTED
    if low in {"cancelled", "canceled"}:
        return LeaveStatus.CANCELLED
    return LeaveStatus.REQUESTED


def leave_type(value: Any) -> LeaveType:
    text = (as_text(value, 40) or "").upper()
    if text in LeaveType.__members__:
        return LeaveType[text]
    low = text.lower()
    aliases = {
        "vacation": LeaveType.ANNUAL,
        "annual": LeaveType.ANNUAL,
        "holiday": LeaveType.ANNUAL,
        "paid": LeaveType.ANNUAL,
        "pto": LeaveType.ANNUAL,
        "sick": LeaveType.SICK,
        "sick_leave": LeaveType.SICK,
        "illness": LeaveType.SICK,
        "casual": LeaveType.CASUAL,
        "personal": LeaveType.CASUAL,
        "training": LeaveType.TRAINING,
        "education": LeaveType.TRAINING,
    }
    return aliases.get(low, LeaveType.OTHER)


def attendance_status(value: Any) -> AttendanceStatus:
    text = (as_text(value, 40) or "").upper()
    if text in AttendanceStatus.__members__:
        return AttendanceStatus[text]
    low = text.lower()
    if low in {"present", "in", "checked_in", "office"}:
        return AttendanceStatus.PRESENT
    if low in {"remote", "wfh", "telework", "home"}:
        return AttendanceStatus.REMOTE
    if low in {"leave", "on_leave", "vacation"}:
        return AttendanceStatus.ON_LEAVE
    return AttendanceStatus.ABSENT


def skill_verification(value: Any) -> SkillVerificationStatus:
    text = (as_text(value, 40) or "").upper()
    if text in SkillVerificationStatus.__members__:
        return SkillVerificationStatus[text]
    low = text.lower()
    if low in {"verified", "validated", "certified"}:
        return SkillVerificationStatus.VERIFIED
    if low in {"rejected", "invalid"}:
        return SkillVerificationStatus.REJECTED
    if low in {"pending", "pending_review", "in_review", "to_review"}:
        return SkillVerificationStatus.PENDING_REVIEW
    return SkillVerificationStatus.SELF_DECLARED



# ===========================================================================
# Adaptive Read-Only Repositories
# ===========================================================================

MAX_ROWS = 20_000
DEFAULT_SKILL_LEVEL = 50
DEFAULT_TIMEZONE = "UTC"
DEFAULT_WORKING_WEEKDAYS = (0, 1, 2, 3, 4)

SYNTHETIC_COMPANY_ID = uuid5(NAMESPACE_URL, "foreign-db/company/default")


def _or_null(expr: str | None) -> str:
    return expr if expr is not None else "NULL"


class AdaptiveRepository:
    """Shared plumbing: mapping lookups, identifier translation and row helpers."""

    def __init__(
        self,
        conn: Connection,
        mapping: SchemaMapping,
        registry: IdRegistry,
        *,
        dialect: str,
        company_code: str = "default",
        default_timezone: str = DEFAULT_TIMEZONE,
        default_working_weekdays: tuple[int, ...] = DEFAULT_WORKING_WEEKDAYS,
        assume_default_schedule: bool = True,
        default_daily_hours: float = 8.0,
        employee_repo: AdaptiveEmployeeRepository | None = None,
    ) -> None:
        self._c = conn
        self._m = mapping
        self._ids = registry
        self._dialect = dialect
        self._company_code = company_code
        self._tz = default_timezone
        self._weekdays = default_working_weekdays
        self._assume_schedule = assume_default_schedule
        self._daily_hours = default_daily_hours
        self._employee_repo = employee_repo

    # mapping helpers
    def _tm(self, concept: str) -> TableMapping:
        return getattr(self._m, concept)

    def _has(self, concept: str) -> bool:
        return self._tm(concept).actual_table is not None

    def _table(self, concept: str) -> str:
        tm = self._tm(concept)
        if tm.actual_table is None:
            raise MappingError(f"The connected database has no table for {concept!r}")
        return tm.actual_table

    def _col(self, concept: str, field: str) -> str | None:
        return self._tm(concept).col(field)

    def _expr(self, concept: str, field: str, alias: str | None = None) -> str | None:
        column = self._col(concept, field)
        if column is None:
            return None
        return f"{quote(alias)}.{quote(column)}" if alias else quote(column)

    def _expr_or_null(self, concept: str, field: str, alias: str | None = None) -> str:
        return _or_null(self._expr(concept, field, alias))

    def _employees(self) -> AdaptiveEmployeeRepository:
        if self._employee_repo is None:
            self._employee_repo = AdaptiveEmployeeRepository(
                self._c,
                self._m,
                self._ids,
                dialect=self._dialect,
                company_code=self._company_code,
                default_timezone=self._tz,
                default_working_weekdays=self._weekdays,
                assume_default_schedule=self._assume_schedule,
                default_daily_hours=self._daily_hours,
            )
        return self._employee_repo

    def _company_id(self) -> UUID:
        return SYNTHETIC_COMPANY_ID

    def synthetic_company(self) -> Company:
        return Company(
            id=SYNTHETIC_COMPANY_ID,
            code=self._company_code,
            name="Connected database",
            timezone=self._tz,
            working_weekdays=self._weekdays,
        )


# Companies


class AdaptiveCompanyRepository(AdaptiveRepository):
    def _rows(self) -> list[dict[str, Any]]:
        if not self._has("companies"):
            return []
        c = quote(self._table("companies"))
        alias = "c"
        id_col = self._col("companies", "id")
        rid = f"{alias}.{quote(id_col)}" if id_col else self._expr_or_null("companies", "code", alias)
        sql = (
            f"SELECT {rid} AS rid, "
            f"{self._expr_or_null('companies', 'code', alias)} AS company_code, "
            f"{self._expr_or_null('companies', 'name', alias)} AS company_name, "
            f"{self._expr_or_null('companies', 'timezone', alias)} AS timezone "
            f"FROM {c} AS {alias}"
        )
        return run(self._c, sql)

    def _to_domain(self, row: dict[str, Any], fallback_code: str) -> Company | None:
        uid = self._ids.remember("companies", row.get("rid") or row.get("company_code"))
        if uid is None:
            return None
        return Company(
            id=uid,
            code=as_text(row.get("company_code"), 60) or fallback_code,
            name=as_text(row.get("company_name"), 200) or "Connected database",
            timezone=as_text(row.get("timezone"), 60) or self._tz,
            working_weekdays=self._weekdays,
        )

    def get_by_code(self, code: str) -> Company | None:
        rows = self._rows()
        for row in rows:
            if (as_text(row.get("company_code"), 60) or "").lower() == code.strip().lower():
                return self._to_domain(row, code)
        if rows:
            return self._to_domain(rows[0], code)
        # Single-tenant source: one synthetic company when employees exist.
        return self.synthetic_company() if self._has("employees") else None

    def get(self, company_id: UUID) -> Company | None:
        if company_id == SYNTHETIC_COMPANY_ID:
            return self.synthetic_company()
        for row in self._rows():
            uid = self._ids.remember("companies", row.get("rid") or row.get("company_code"))
            if uid == company_id:
                return self._to_domain(row, self._company_code)
        return None


# Employees


class AdaptiveEmployeeRepository(AdaptiveRepository):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cache: list[dict[str, Any]] | None = None

    # rows
    def rows(self) -> list[dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        if not self._has("employees") or self._col("employees", "id") is None:
            self._cache = []
            return self._cache

        alias = "e"
        table = quote(self._table("employees"))
        id_col = self._col("employees", "id")
        select_parts = [f"{alias}.{quote(id_col or 'id')} AS rid"]
        joins: list[str] = []
        for field, out_name in (
            ("employee_code", "employee_code"),
            ("full_name", "full_name"),
            ("email", "email"),
            ("job_title", "job_title"),
            ("hire_date", "hire_date"),
            ("employment_status", "employment_status"),
            ("experience_years", "experience_years"),
            ("timezone", "timezone"),
            ("department_id", "department_id"),
            ("department_name", "direct_department_name"),
            ("team_id", "team_id"),
            ("team_name", "direct_team_name"),
            ("skills_text", "skills_text"),
            ("bio", "bio"),
            ("weekly_hours", "weekly_hours"),
            ("daily_hours", "daily_hours"),
            ("rating", "rating"),
        ):
            select_parts.append(f"{self._expr_or_null('employees', field, alias)} AS {out_name}")

        dept = self._tm("departments")
        if self._has("departments") and self._col("employees", "department_id"):
            dept_id, dept_name = dept.col("id"), dept.col("name")
            if dept_id and dept_name:
                joins.append(
                    f"LEFT JOIN {quote(dept.actual_table or '')} AS d "
                    f"ON d.{quote(dept_id)} = {alias}.{quote(self._col('employees', 'department_id') or '')}"
                )
                select_parts.append(f"d.{quote(dept_name)} AS department_name")

        team = self._tm("teams")
        if self._has("teams") and self._col("employees", "team_id"):
            team_id, team_name = team.col("id"), team.col("name")
            if team_id and team_name:
                joins.append(
                    f"LEFT JOIN {quote(team.actual_table or '')} AS tm "
                    f"ON tm.{quote(team_id)} = {alias}.{quote(self._col('employees', 'team_id') or '')}"
                )
                select_parts.append(f"tm.{quote(team_name)} AS team_name")

        sql = f"SELECT {', '.join(select_parts)} FROM {table} AS {alias} {' '.join(joins)} LIMIT {MAX_ROWS}"
        rows = run(self._c, sql)
        for row in rows:
            row["_uid"] = self._ids.remember("employees", row["rid"])
        self._cache = rows
        return rows

    # domain
    def _to_domain(self, row: dict[str, Any]) -> Employee | None:
        uid = row.get("_uid") or self._ids.remember("employees", row.get("rid"))
        if uid is None:
            return None
        code = as_text(row.get("employee_code"), 60) or as_text(row.get("rid"), 60) or str(uid)
        dept_name = as_text(row.get("department_name"), 120) or as_text(
            row.get("direct_department_name"), 120
        )
        team_name = as_text(row.get("team_name"), 120) or as_text(
            row.get("direct_team_name"), 120
        )
        hire_date = as_date(row.get("hire_date"))
        exp_years = as_float(row.get("experience_years"))
        if exp_years is None and hire_date is not None:
            exp_years = round(max(0.0, (datetime.now(UTC).date() - hire_date).days / 365.25), 1)
        has_status_col = self._col("employees", "employment_status") is not None
        status = employment_status(
            row.get("employment_status"),
            default_if_missing=EmploymentStatus.ACTIVE,
        )
        if not has_status_col:
            status = EmploymentStatus.ACTIVE
        return Employee(
            id=uid,
            company_id=self._company_id(),
            employee_code=code,
            full_name=as_text(row.get("full_name"), 200) or code,
            email=as_text(row.get("email"), 254),
            job_title=as_text(row.get("job_title"), 120) or "Unspecified",
            department_id=self._ids.remember("departments", row.get("department_id"))
            if row.get("department_id") is not None
            else (self._ids.remember("departments", f"name:{dept_name.lower()}") if dept_name else None),
            department_name=dept_name,
            team_id=self._ids.remember("teams", row.get("team_id"))
            if row.get("team_id") is not None
            else (self._ids.remember("teams", f"name:{team_name.lower()}") if team_name else None),
            team_name=team_name,
            hire_date=hire_date,
            experience_years=exp_years,
            employment_status=status,
            timezone=as_text(row.get("timezone"), 60) or self._tz,
        )

    def filtered(
        self,
        *,
        status: EmploymentStatus | None = None,
        department: str | None = None,
        team: str | None = None,
        job_title: str | None = None,
        employee_id: UUID | None = None,
        employee_code: str | None = None,
    ) -> list[Employee]:
        wanted_code = employee_code.strip().lower() if employee_code else None
        out: list[Employee] = []
        for row in self.rows():
            if employee_id is not None and row.get("_uid") != employee_id:
                continue
            emp = self._to_domain(row)
            if emp is None:
                continue
            if wanted_code and emp.employee_code.lower() != wanted_code:
                continue
            if status is not None and emp.employment_status != status:
                continue
            if department and (emp.department_name or "").strip().lower() != department.strip().lower():
                continue
            if team and (emp.team_name or "").strip().lower() != team.strip().lower():
                continue
            if job_title and job_title.strip().lower() not in emp.job_title.lower():
                continue
            out.append(emp)
        out.sort(key=lambda e: e.employee_code)
        return out

    # Protocol
    def list_employees(
        self,
        company_id: UUID,
        *,
        status: EmploymentStatus | None = None,
        department_id: UUID | None = None,
        skill_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Employee]:
        employees = self.filtered(status=status)
        if department_id is not None:
            employees = [e for e in employees if e.department_id == department_id]
        if skill_name:
            holders = self._ids_with_skill(skill_name)
            employees = [e for e in employees if e.id in holders]
        return Page[Employee](
            items=tuple(employees[offset : offset + limit]),
            total=len(employees),
            limit=limit,
            offset=offset,
        )

    def list_employee_ids(self, company_id: UUID, *, active_only: bool = False) -> list[UUID]:
        employees = self.filtered(status=EmploymentStatus.ACTIVE if active_only else None)
        return [e.id for e in employees]

    def get_employee(self, company_id: UUID, employee_id: UUID) -> Employee | None:
        found = self.filtered(employee_id=employee_id)
        return found[0] if found else None

    def get_employee_by_code(self, company_id: UUID, employee_code: str) -> Employee | None:
        found = self.filtered(employee_code=employee_code)
        return found[0] if found else None

    def search_employees(
        self,
        company_id: UUID,
        *,
        department: str | None = None,
        team: str | None = None,
        job_title: str | None = None,
        skill_names: tuple[str, ...] = (),
        status: EmploymentStatus | None = None,
        limit: int = 25,
    ) -> Page[Employee]:
        employees = self.filtered(status=status, department=department, team=team, job_title=job_title)
        for name in skill_names:
            holders = self._ids_with_skill(name)
            employees = [e for e in employees if e.id in holders]
        return Page[Employee](items=tuple(employees[:limit]), total=len(employees), limit=limit, offset=0)

    def update_employee(self, company_id: UUID, employee_id: UUID, **_: Any) -> Employee:
        raise ReadOnlySourceError("The connected database is read-only: employee data cannot be changed here")

    # skills
    def _ids_with_skill(self, skill_name: str) -> set[UUID]:
        wanted = skill_name.strip().lower()
        return {
            uid
            for uid, skills in self.skills_by_employee().items()
            if any(s.skill_name.lower() == wanted for s in skills)
        }

    def skills_by_employee(self) -> dict[UUID, list[EmployeeSkill]]:
        result: dict[UUID, list[EmployeeSkill]] = defaultdict(list)
        if self._has("employee_skills") and self._has("skills"):
            es, skill_table = self._tm("employee_skills"), self._tm("skills")
            emp_col, skill_fk = es.col("employee_id"), es.col("skill_id")
            skill_id, skill_name = skill_table.col("id"), skill_table.col("name")
            if emp_col and skill_fk and skill_id and skill_name:
                level_col = es.col("declared_level")
                verif_col = es.col("verification_status")
                sql = (
                    f"SELECT es.{quote(emp_col)} AS emp, "
                    f"s.{quote(skill_id)} AS skill_rid, "
                    f"s.{quote(skill_name)} AS skill_name, "
                    f"{_or_null(f'es.{quote(level_col)}' if level_col else None)} AS level, "
                    f"{_or_null(f'es.{quote(verif_col)}' if verif_col else None)} AS verification "
                    f"FROM {quote(es.actual_table or '')} AS es "
                    f"JOIN {quote(skill_table.actual_table or '')} AS s "
                    f"ON s.{quote(skill_id)} = es.{quote(skill_fk)}"
                )
                es_rows = run(self._c, sql)
                numeric_levels = [
                    lvl for r in es_rows if (lvl := as_float(r.get("level"))) is not None
                ]
                scale = _detect_level_scale(numeric_levels)
                for row in es_rows:
                    emp_uid = self._ids.remember("employees", row["emp"])
                    name = as_text(row.get("skill_name"), 100)
                    if emp_uid is None or not name:
                        continue
                    raw_lvl = as_float(row.get("level"))
                    level = round(raw_lvl * scale) if raw_lvl is not None else 70
                    result[emp_uid].append(
                        EmployeeSkill(
                            skill_id=self._ids.remember("skills", row["skill_rid"]) or self._skill_uuid(name),
                            skill_name=name,
                            proficiency_level=max(0, min(100, level)),
                            verification_status=skill_verification(row.get("verification")),
                        )
                    )
        if result:
            return dict(result)

        # Fallback 1: parse inline structured/CSV skills text or JSON on employee rows.
        for row in self.rows():
            uid = row.get("_uid")
            if uid is None:
                continue
            exp = _infer_experience_years(row)
            default_level = min(92, max(70, round(66 + exp * 4.0)))
            entries = _parse_skill_entries(row.get("skills_text"), default_level)
            if entries:
                for token, level in entries:
                    result[uid].append(
                        EmployeeSkill(
                            skill_id=self._skill_uuid(token),
                            skill_name=token,
                            proficiency_level=max(0, min(100, level)),
                            verification_status=SkillVerificationStatus.SELF_DECLARED,
                        )
                    )
                continue

            # Fallback 2: minimal schemas without skill tables/columns infer skills from role & bio.
            role_corpus = " ".join(
                str(part)
                for part in (
                    row.get("job_title"),
                    row.get("bio"),
                    row.get("direct_department_name") or row.get("department_name"),
                    row.get("direct_team_name") or row.get("team_name"),
                )
                if part
            )
            if role_corpus.strip():
                from app.intelligence.analyzer import hinted_skill_names

                inferred_names = sorted(hinted_skill_names(role_corpus))
                role_level = min(90, max(70, round(64 + exp * 4.0)))
                for token in inferred_names[:12]:
                    result[uid].append(
                        EmployeeSkill(
                            skill_id=self._skill_uuid(token),
                            skill_name=token,
                            proficiency_level=role_level,
                            verification_status=SkillVerificationStatus.SELF_DECLARED,
                        )
                    )
        return dict(result)

    @staticmethod
    def _skill_uuid(name: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"foreign-db/skills/by-name/{name.strip().lower()}")

    def memberships(self) -> dict[UUID, list[ProjectMembership]]:
        result: dict[UUID, list[ProjectMembership]] = defaultdict(list)
        if not (self._has("project_members") and self._has("projects")):
            return {}
        pm, projects = self._tm("project_members"), self._tm("projects")
        emp_col, project_fk = pm.col("employee_id"), pm.col("project_id")
        project_id, project_name = projects.col("id"), projects.col("name")
        if not (emp_col and project_fk and project_id and project_name):
            return {}
        role_col = pm.col("role")
        sql = (
            f"SELECT pm.{quote(emp_col)} AS emp, "
            f"pr.{quote(project_id)} AS project_rid, "
            f"pr.{quote(project_name)} AS project_name, "
            f"{_or_null(f'pm.{quote(role_col)}' if role_col else None)} AS role "
            f"FROM {quote(pm.actual_table or '')} AS pm "
            f"JOIN {quote(projects.actual_table or '')} AS pr "
            f"ON pr.{quote(project_id)} = pm.{quote(project_fk)}"
        )
        for row in run(self._c, sql):
            emp_uid = self._ids.remember("employees", row["emp"])
            project_uid = self._ids.remember("projects", row["project_rid"])
            if emp_uid is None or project_uid is None:
                continue
            result[emp_uid].append(
                ProjectMembership(
                    project_id=project_uid,
                    project_name=as_text(row.get("project_name"), 200) or "Project",
                    role=as_text(row.get("role"), 80) or "member",
                )
            )
        return dict(result)

    def get_profiles(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, EmployeeProfile]:
        if not employee_ids:
            return {}
        wanted = set(employee_ids)
        skills = self.skills_by_employee()
        memberships = self.memberships()
        profiles: dict[UUID, EmployeeProfile] = {}
        for row in self.rows():
            uid = row.get("_uid")
            if uid is None or uid not in wanted:
                continue
            employee = self._to_domain(row)
            if employee is None:
                continue
            profiles[uid] = EmployeeProfile(
                employee=employee,
                skills=tuple(skills.get(uid, ())),
                memberships=tuple(memberships.get(uid, ())),
            )
        return profiles


def _infer_experience_years(row: dict[str, Any]) -> float:
    exp = as_float(row.get("experience_years"))
    if exp is not None:
        return max(0.0, exp)
    title = (as_text(row.get("job_title"), 120) or "").lower()
    if any(k in title for k in ("principal", "staff", "architect", "lead", "head", "director")):
        return 7.0
    if any(k in title for k in ("senior", "sr.", "sr ")):
        return 5.0
    if any(k in title for k in ("junior", "jr.", "jr ", "intern", "trainee")):
        return 1.5
    return 3.0


def _detect_level_scale(levels: list[float]) -> float:
    if not levels:
        return 1.0
    max_val = max(levels)
    if 0 < max_val <= 5.0:
        return 20.0
    if 5.0 < max_val <= 10.0:
        return 10.0
    return 1.0


_QUALITATIVE_LEVELS: dict[str, int] = {
    "expert": 92,
    "principal": 94,
    "master": 92,
    "lead": 90,
    "senior": 84,
    "advanced": 84,
    "mid": 72,
    "intermediate": 72,
    "competent": 70,
    "junior": 55,
    "beginner": 50,
    "basic": 50,
}


def _parse_skill_entries(raw: Any, default_level: int = 70) -> list[tuple[str, int]]:
    """Parse JSON lists/dicts, rated CSV pairs ('Python:90'), or delimited text into (skill, 0..100)."""
    if raw is None:
        return []
    parsed: list[tuple[str, float | None]] = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            name = str(k).strip()
            if name:
                parsed.append((name, as_float(v)))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                parsed.append((str(item["name"]).strip(), as_float(item.get("level"))))
            elif isinstance(item, str) and item.strip():
                parsed.append((item.strip(), None))
    else:
        text_val = as_text(raw, 4000)
        if not text_val:
            return []
        if text_val.startswith(("{", "[")):
            try:
                loaded = json.loads(text_val)
                return _parse_skill_entries(loaded, default_level)
            except ValueError:
                pass
        for chunk in re.split(r"[,;|/\n]+", text_val):
            part = chunk.strip()
            if not part or len(part) > 100:
                continue
            m_num = re.match(r"^(.+?)\s*[:=\-]\s*(\d+(?:\.\d+)?)\s*%?$", part)
            if m_num:
                parsed.append((m_num.group(1).strip(), float(m_num.group(2))))
                continue
            m_paren = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", part)
            if m_paren:
                tag = m_paren.group(2).strip().lower()
                num = as_float(tag.rstrip("%"))
                if num is not None:
                    parsed.append((m_paren.group(1).strip(), num))
                    continue
                if tag in _QUALITATIVE_LEVELS:
                    parsed.append((m_paren.group(1).strip(), float(_QUALITATIVE_LEVELS[tag])))
                    continue
            parsed.append((part, None))

    if not parsed:
        return []
    nums = [lvl for _, lvl in parsed if lvl is not None]
    scale = _detect_level_scale(nums)
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for name, lvl in parsed[:40]:
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        score = round(lvl * scale) if lvl is not None else default_level
        out.append((name, max(0, min(100, score))))
    return out


def _split_skill_text(text: str) -> list[str]:
    return [name for name, _ in _parse_skill_entries(text)]


# Skills catalogue


class AdaptiveSkillRepository(AdaptiveRepository):
    def catalog(self) -> list[Skill]:
        if self._has("skills"):
            s = self._tm("skills")
            skill_id, skill_name = s.col("id"), s.col("name")
            if skill_id and skill_name:
                category_col, alias_col = s.col("category"), s.col("aliases")
                sql = (
                    f"SELECT {quote(skill_id)} AS rid, {quote(skill_name)} AS skill_name, "
                    f"{_or_null(f'{quote(category_col)}' if category_col else None)} AS category, "
                    f"{_or_null(f'{quote(alias_col)}' if alias_col else None)} AS aliases "
                    f"FROM {quote(s.actual_table or '')}"
                )
                skills: list[Skill] = []
                for row in run(self._c, sql):
                    uid = self._ids.remember("skills", row["rid"])
                    name = as_text(row.get("skill_name"), 100)
                    if uid is None or not name:
                        continue
                    raw_aliases = row.get("aliases")
                    aliases = (
                        tuple(a.strip() for a in str(raw_aliases).split(",") if a.strip())
                        if isinstance(raw_aliases, str)
                        else ()
                    )
                    skills.append(
                        Skill(
                            id=uid,
                            name=name,
                            category=as_text(row.get("category"), 80) or "general",
                            aliases=aliases,
                        )
                    )
                if skills:
                    return sorted(skills, key=lambda s: s.name)

        # Fallback: skills implied by employee records (junction rows or free text).
        by_name: dict[str, Skill] = {}
        for skills_list in self._employees().skills_by_employee().values():
            for entry in skills_list:
                by_name.setdefault(
                    entry.skill_name.lower(),
                    Skill(id=entry.skill_id, name=entry.skill_name, category="inferred"),
                )
        return sorted(by_name.values(), key=lambda s: s.name)


# Tasks


class AdaptiveTaskRepository(AdaptiveRepository):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cache: list[dict[str, Any]] | None = None

    def rows(self) -> list[dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        if not self._has("tasks") or self._col("tasks", "id") is None:
            self._cache = []
            return self._cache

        alias = "t"
        select_parts = [f"{alias}.{quote(self._col('tasks', 'id') or 'id')} AS rid"]
        for field in (
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
            "project_id",
            "task_category",
            "acceptance_criteria",
            "assignee_id",
            "skills_text",
            "completed_at",
            "quality_score",
        ):
            select_parts.append(f"{self._expr_or_null('tasks', field, alias)} AS {field}")

        joins = ""
        projects = self._tm("projects")
        if self._has("projects") and self._col("tasks", "project_id"):
            project_id, project_name = projects.col("id"), projects.col("name")
            if project_id and project_name:
                joins = (
                    f" LEFT JOIN {quote(projects.actual_table or '')} AS pr "
                    f"ON pr.{quote(project_id)} = {alias}.{quote(self._col('tasks', 'project_id') or '')}"
                )
                select_parts.append(f"pr.{quote(project_name)} AS project_name")

        sql = (
            f"SELECT {', '.join(select_parts)} FROM {quote(self._table('tasks'))} AS {alias}"
            f"{joins} LIMIT {MAX_ROWS}"
        )
        rows = run(self._c, sql)
        for row in rows:
            row["_uid"] = self._ids.remember("tasks", row["rid"])
        self._cache = rows
        return rows

    def _required_skills(self, task_ids: list[UUID]) -> dict[UUID, list[RequiredSkill]]:
        result: dict[UUID, list[RequiredSkill]] = defaultdict(list)
        if not (task_ids and self._has("task_required_skills")):
            return {}
        trs = self._tm("task_required_skills")
        task_col = trs.col("task_id")
        if not task_col:
            return {}

        name_expr, skill_id_expr = "NULL", "NULL"
        join = ""
        if self._has("skills") and trs.col("skill_id"):
            s = self._tm("skills")
            skill_id, skill_name = s.col("id"), s.col("name")
            if skill_id and skill_name:
                join = (
                    f" LEFT JOIN {quote(s.actual_table or '')} AS s "
                    f"ON s.{quote(skill_id)} = trs.{quote(trs.col('skill_id') or '')}"
                )
                name_expr = f"s.{quote(skill_name)}"
                skill_id_expr = f"s.{quote(skill_id)}"

        level_col = trs.col("minimum_level")
        mandatory_col = trs.col("mandatory")
        weight_col = trs.col("weight")
        sql = (
            f"SELECT trs.{quote(task_col)} AS task_rid, {skill_id_expr} AS skill_rid, "
            f"{name_expr} AS skill_name, "
            f"{_or_null(f'trs.{quote(level_col)}' if level_col else None)} AS min_level, "
            f"{_or_null(f'trs.{quote(mandatory_col)}' if mandatory_col else None)} AS mandatory, "
            f"{_or_null(f'trs.{quote(weight_col)}' if weight_col else None)} AS weight "
            f"FROM {quote(trs.actual_table or '')} AS trs{join}"
        )
        wanted = set(task_ids)
        for row in run(self._c, sql):
            task_uid = self._ids.remember("tasks", row["task_rid"])
            name = as_text(row.get("skill_name"), 100)
            if task_uid not in wanted or not name:
                continue
            result[task_uid].append(
                RequiredSkill(
                    skill_id=self._ids.remember("skills", row.get("skill_rid")),
                    skill_name=name,
                    minimum_level=max(0, min(100, as_int(row.get("min_level"), 50) or 50)),
                    mandatory=as_bool(row.get("mandatory"), default=False) or False,
                    weight=as_float(row.get("weight")) or 1.0,
                    source=RequirementSource.TASK_RECORD,
                )
            )
        return dict(result)

    def _to_domain(self, row: dict[str, Any]) -> TaskRecord | None:
        uid = row.get("_uid")
        title = as_text(row.get("title"), 300)
        if uid is None or not title:
            return None
        return TaskRecord(
            id=uid,
            company_id=self._company_id(),
            task_code=as_text(row.get("task_code"), 80) or str(row.get("rid")),
            project_id=self._ids.remember("projects", row.get("project_id"))
            if row.get("project_id") is not None
            else None,
            project_name=as_text(row.get("project_name"), 200),
            title=title,
            description=as_text(row.get("description"), 20_000),
            complexity=task_complexity(row.get("complexity")),
            priority=task_priority(row.get("priority")),
            status=task_status(row.get("status")),
            estimated_hours=as_float(row.get("estimated_hours")),
            remaining_estimated_hours=as_float(row.get("remaining_estimated_hours")),
            planned_start=as_datetime(row.get("planned_start")),
            deadline=as_datetime(row.get("deadline")),
            task_category=task_category(row.get("task_category")),
            acceptance_criteria=as_text(row.get("acceptance_criteria"), 10_000),
        )

    def get_task(self, company_id: UUID, task_id: UUID) -> TaskRecord | None:
        for row in self.rows():
            if row.get("_uid") == task_id:
                record = self._to_domain(row)
                if record is None:
                    return None
                skills = self._required_skills([task_id]).get(task_id, [])
                return record.model_copy(update={"required_skills": tuple(skills)})
        return None

    def get_task_by_code(self, company_id: UUID, task_code: str) -> TaskRecord | None:
        wanted = task_code.strip().lower()
        for row in self.rows():
            code = (as_text(row.get("task_code"), 80) or str(row.get("rid"))).lower()
            if code == wanted:
                record = self._to_domain(row)
                if record is None:
                    return None
                skills = self._required_skills([record.id]).get(record.id, [])
                return record.model_copy(update={"required_skills": tuple(skills)})
        return None

    def list_tasks(
        self,
        company_id: UUID,
        *,
        status: Any = None,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[TaskRecord]:
        records: list[TaskRecord] = []
        for row in self.rows():
            record = self._to_domain(row)
            if record is None:
                continue
            if status is not None and record.status != status:
                continue
            if project_id is not None and record.project_id != project_id:
                continue
            if sprint_id is not None:
                continue  # sprint membership is not mapped for tasks: nothing can match
            records.append(record)
        records.sort(key=lambda r: r.task_code)
        return Page[TaskRecord](
            items=tuple(records[offset : offset + limit]),
            total=len(records),
            limit=limit,
            offset=offset,
        )

    def create_task(self, company_id: UUID, task: NewTask) -> TaskRecord:
        raise ReadOnlySourceError("The connected database is read-only: tasks cannot be created here")

    def get_skill_catalog(self, company_id: UUID) -> list[Skill]:
        return AdaptiveSkillRepository(
            self._c,
            self._m,
            self._ids,
            dialect=self._dialect,
            company_code=self._company_code,
            default_timezone=self._tz,
            default_working_weekdays=self._weekdays,
            employee_repo=self._employees(),
        ).catalog()

    def get_sprint(self, company_id: UUID, sprint_id: UUID) -> Sprint | None:
        if not self._has("sprints"):
            return None
        s = self._tm("sprints")
        sprint_pk, name_col = s.col("id"), s.col("name")
        if not (sprint_pk and name_col):
            return None
        raw = self._ids.resolve("sprints", sprint_id)
        if raw is None:
            return None
        sql = (
            f"SELECT {quote(name_col)} AS sprint_name, "
            f"{self._expr_or_null('sprints', 'start_date')} AS start_date, "
            f"{self._expr_or_null('sprints', 'end_date')} AS end_date, "
            f"{self._expr_or_null('sprints', 'project_id')} AS project_id, "
            f"{self._expr_or_null('sprints', 'status')} AS sprint_status "
            f"FROM {quote(s.actual_table or '')} WHERE {quote(sprint_pk)} = :rid"
        )
        row = run_one(self._c, sql, {"rid": _bind(raw)})
        if row is None:
            return None
        start, end = as_date(row.get("start_date")), as_date(row.get("end_date"))
        if start is None or end is None:
            return None
        return Sprint(
            id=sprint_id,
            project_id=self._ids.remember("projects", row.get("project_id")) or UUID(int=0),
            name=as_text(row.get("sprint_name"), 200) or "Sprint",
            start_date=start,
            end_date=end,
            status=(as_text(row.get("sprint_status"), 40) or "ACTIVE").upper(),
        )

    def project_exists(self, company_id: UUID, project_id: UUID) -> bool:
        if not self._has("projects"):
            return False
        project_pk = self._col("projects", "id")
        if not project_pk:
            return False
        rows = run(
            self._c,
            f"SELECT {quote(project_pk)} AS rid FROM {quote(self._table('projects'))}",
        )
        return any(self._ids.remember("projects", row["rid"]) == project_id for row in rows)


def _bind(value: Any) -> Any:
    """Bind a value for a raw SQL comparison, stringified when the caller passed a domain UUID."""
    if isinstance(value, UUID):
        return str(value)
    return value


# Workforce


class AdaptiveWorkforceRepository(AdaptiveRepository):
    def _schedule(self, employee_ids: list[UUID]) -> dict[UUID, list[ScheduleDay]]:
        result: dict[UUID, list[ScheduleDay]] = defaultdict(list)
        if self._has("work_schedule"):
            ws = self._tm("work_schedule")
            emp_col, weekday_col = ws.col("employee_id"), ws.col("weekday")
            if emp_col and weekday_col:
                start_col, end_col, break_col = (
                    ws.col("start_time"),
                    ws.col("end_time"),
                    ws.col("break_minutes"),
                )
                sql = (
                    f"SELECT {quote(emp_col)} AS emp, {quote(weekday_col)} AS weekday, "
                    f"{_or_null(f'{quote(start_col)}' if start_col else None)} AS start_time, "
                    f"{_or_null(f'{quote(end_col)}' if end_col else None)} AS end_time, "
                    f"{_or_null(f'{quote(break_col)}' if break_col else None)} AS break_minutes "
                    f"FROM {quote(ws.actual_table or '')}"
                )
                wanted = set(employee_ids)
                for row in run(self._c, sql):
                    uid = self._ids.remember("employees", row["emp"])
                    if uid not in wanted:
                        continue
                    day = as_int(row.get("weekday"))
                    if day is None or not 0 <= day <= 6:
                        continue
                    start = as_time(row.get("start_time")) or time(9, 0)
                    end = as_time(row.get("end_time")) or time(17, 0)
                    if end <= start:
                        continue
                    result[uid].append(
                        ScheduleDay(
                            weekday=day,
                            start_time=start,
                            end_time=end,
                            break_minutes=max(0, min(240, as_int(row.get("break_minutes"), 0) or 0)),
                        )
                    )
                if result:
                    return dict(result)

        if not self._assume_schedule:
            return {}
        # Documented assumption for sources without a schedule table: employee-level hours if present,
        # else a standard working week.
        by_uid = {row["_uid"]: row for row in self._employees().rows() if row.get("_uid")}
        num_days = max(1, len(self._weekdays))
        for uid in employee_ids:
            row = by_uid.get(uid, {})
            daily_col = as_float(row.get("daily_hours"))
            weekly_col = as_float(row.get("weekly_hours"))
            if daily_col is not None and daily_col > 0:
                per_day = max(1.0, min(16.0, daily_col))
            elif weekly_col is not None and weekly_col > 0:
                per_day = max(1.0, min(16.0, weekly_col / num_days))
            else:
                per_day = max(1.0, min(16.0, self._daily_hours))
            start_hour = 9
            total_minutes = round(per_day * 60)
            end_minutes = min(23 * 60, start_hour * 60 + total_minutes + 60)
            end_t = time(end_minutes // 60, end_minutes % 60)
            for weekday in self._weekdays:
                result[uid].append(
                    ScheduleDay(
                        weekday=weekday,
                        start_time=time(start_hour, 0),
                        end_time=end_t,
                        break_minutes=60,
                    )
                )
        return dict(result)

    def _attendance(self, employee_ids: list[UUID], since: datetime) -> dict[UUID, list[AttendanceRecord]]:
        result: dict[UUID, list[AttendanceRecord]] = defaultdict(list)
        if not self._has("attendance"):
            return {}
        at = self._tm("attendance")
        emp_col = at.col("employee_id")
        if not emp_col:
            return {}
        date_col, cin_col, cout_col = at.col("date"), at.col("check_in"), at.col("check_out")
        date_expr = _or_null(quote(date_col) if date_col else (quote(cin_col) if cin_col else None))
        sql = (
            f"SELECT {quote(emp_col)} AS emp, {date_expr} AS work_date, "
            f"{_or_null(f'{quote(cin_col)}' if cin_col else None)} AS check_in, "
            f"{_or_null(f'{quote(cout_col)}' if cout_col else None)} AS check_out "
            f"FROM {quote(at.actual_table or '')}"
        )
        wanted = set(employee_ids)
        for row in run(self._c, sql):
            uid = self._ids.remember("employees", row["emp"])
            if uid not in wanted:
                continue
            work_date = as_date(row.get("work_date"))
            if work_date is None or work_date < since.date():
                continue
            check_in = as_datetime(row.get("check_in"))
            try:
                result[uid].append(
                    AttendanceRecord(
                        work_date=work_date,
                        check_in=check_in,
                        check_out=as_datetime(row.get("check_out")),
                        status=attendance_status("PRESENT" if check_in is not None else "ABSENT"),
                    )
                )
            except Exception:  # malformed source row (e.g. check-out before check-in)
                logger.debug("Skipping malformed attendance row for employee %s", uid, exc_info=True)
                continue
        return dict(result)

    def _leaves(self, employee_ids: list[UUID], since: datetime) -> dict[UUID, list[LeaveRecord]]:
        result: dict[UUID, list[LeaveRecord]] = defaultdict(list)
        if not self._has("leaves"):
            return {}
        lv = self._tm("leaves")
        emp_col, start_col, end_col = (
            lv.col("employee_id"),
            lv.col("start_date"),
            lv.col("end_date"),
        )
        if not (emp_col and start_col and end_col):
            return {}
        status_col, type_col, id_col = lv.col("status"), lv.col("leave_type"), lv.col("id")
        sql = (
            f"SELECT {quote(emp_col)} AS emp, "
            f"{_or_null(f'{quote(id_col)}' if id_col else None)} AS rid, "
            f"{quote(start_col)} AS start_date, {quote(end_col)} AS end_date, "
            f"{_or_null(f'{quote(status_col)}' if status_col else None)} AS leave_status, "
            f"{_or_null(f'{quote(type_col)}' if type_col else None)} AS leave_type "
            f"FROM {quote(lv.actual_table or '')}"
        )
        wanted = set(employee_ids)
        for row in run(self._c, sql):
            uid = self._ids.remember("employees", row["emp"])
            if uid not in wanted:
                continue
            start = as_datetime(row.get("start_date"))
            end = as_datetime(row.get("end_date")) or start
            if start is None or end is None or end < since:
                continue
            if end.date() == start.date():
                end = end.replace(hour=23, minute=59, second=59)
            status = leave_status(row.get("leave_status"))
            if status not in (LeaveStatus.APPROVED, LeaveStatus.REQUESTED):
                continue
            leave_uid = self._ids.remember("leaves", row.get("rid") or f"{uid}:{start.isoformat()}")
            if leave_uid is None:
                continue
            result[uid].append(
                LeaveRecord(
                    id=leave_uid,
                    leave_type=leave_type(row.get("leave_type")),
                    start_at=start,
                    end_at=end,
                    status=status,
                )
            )
        return dict(result)

    def _logged_hours(self) -> dict[tuple[UUID, UUID], float]:
        totals: dict[tuple[UUID, UUID], float] = defaultdict(float)
        if not self._has("timesheets"):
            return {}
        ts = self._tm("timesheets")
        task_col, emp_col, hours_col = (
            ts.col("task_id"),
            ts.col("employee_id"),
            ts.col("hours"),
        )
        if not (task_col and emp_col and hours_col):
            return {}
        sql = (
            f"SELECT {quote(task_col)} AS task_rid, {quote(emp_col)} AS emp, "
            f"{quote(hours_col)} AS hours FROM {quote(ts.actual_table or '')}"
        )
        for row in run(self._c, sql):
            task_uid = self._ids.remember("tasks", row["task_rid"])
            emp_uid = self._ids.remember("employees", row["emp"])
            hours = as_float(row.get("hours"))
            if task_uid and emp_uid and hours:
                totals[(task_uid, emp_uid)] += hours
        return dict(totals)

    def _assignments(self, employee_ids: list[UUID]) -> dict[UUID, list[Assignment]]:
        result: dict[UUID, list[Assignment]] = defaultdict(list)
        wanted = set(employee_ids)
        if self._has("task_assignments") and self._has("tasks"):
            ta, tasks = self._tm("task_assignments"), self._tm("tasks")
            emp_col, task_fk = ta.col("employee_id"), ta.col("task_id")
            task_pk, title_col = tasks.col("id"), tasks.col("title")
            if emp_col and task_fk and task_pk and title_col:
                status_col = ta.col("assignment_status")
                hours_col = ta.col("allocated_hours")
                assigned_col = ta.col("assigned_at")
                task_code_col = tasks.col("task_code")
                sql = (
                    f"SELECT ta.{quote(emp_col)} AS emp, "
                    f"t.{quote(task_pk)} AS task_rid, "
                    f"t.{quote(title_col)} AS task_title, "
                    f"{_or_null(f't.{quote(task_code_col)}' if task_code_col else None)} AS task_code, "
                    f"{_or_null(f'ta.{quote(status_col)}' if status_col else None)} AS assignment_status, "
                    f"{_or_null(f'ta.{quote(hours_col)}' if hours_col else None)} AS allocated_hours, "
                    f"{_or_null(f'ta.{quote(assigned_col)}' if assigned_col else None)} AS assigned_at, "
                    f"{self._expr_or_null('tasks', 'planned_start', 't')} AS planned_start, "
                    f"{self._expr_or_null('tasks', 'deadline', 't')} AS deadline, "
                    f"{self._expr_or_null('tasks', 'project_id', 't')} AS project_id "
                    f"FROM {quote(ta.actual_table or '')} AS ta "
                    f"JOIN {quote(tasks.actual_table or '')} AS t "
                    f"ON t.{quote(task_pk)} = ta.{quote(task_fk)}"
                )
                rows = run(self._c, sql)

                counts: dict[UUID, int] = defaultdict(int)
                for row in rows:
                    task_uid = self._ids.remember("tasks", row["task_rid"])
                    if task_uid is not None:
                        counts[task_uid] += 1

                for row in rows:
                    emp_uid = self._ids.remember("employees", row["emp"])
                    if emp_uid not in wanted:
                        continue
                    task_uid = self._ids.remember("tasks", row["task_rid"])
                    if task_uid is None:
                        continue
                    status = assignment_status(row.get("assignment_status"))
                    if not status.consumes_capacity:
                        continue
                    assignment_uid = self._ids.remember("task_assignments", f"{row['task_rid']}|{row['emp']}")
                    if assignment_uid is None:
                        continue
                    result[emp_uid].append(
                        Assignment(
                            id=assignment_uid,
                            task_id=task_uid,
                            task_code=as_text(row.get("task_code"), 80) or str(row["task_rid"]),
                            task_title=as_text(row.get("task_title"), 300) or "Task",
                            project_id=self._ids.remember("projects", row.get("project_id")),
                            assignment_status=status,
                            planned_start=as_datetime(row.get("planned_start"))
                            or as_datetime(row.get("assigned_at")),
                            planned_end=as_datetime(row.get("deadline")),
                            allocated_hours=as_float(row.get("allocated_hours")),
                            active_assignee_count=max(1, counts.get(task_uid, 1)),
                        )
                    )
                if result:
                    return dict(result)

        # Fallback: Jira/Linear/flat task schemas put assignee_id directly on tasks.
        if self._has("tasks") and self._col("tasks", "assignee_id"):
            task_repo = AdaptiveTaskRepository(
                self._c,
                self._m,
                self._ids,
                dialect=self._dialect,
                company_code=self._company_code,
            )
            for row in task_repo.rows():
                raw_assignee = row.get("assignee_id")
                if raw_assignee is None:
                    continue
                emp_uid = self._ids.remember("employees", raw_assignee)
                task_uid = row.get("_uid")
                if emp_uid not in wanted or task_uid is None:
                    continue
                t_status = task_status(row.get("status"))
                if t_status in (TaskStatus.DONE, TaskStatus.CANCELLED):
                    continue
                a_status = (
                    AssignmentStatus.ACTIVE
                    if t_status in (TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW)
                    else AssignmentStatus.PLANNED
                )
                hours = as_float(row.get("remaining_estimated_hours")) or as_float(
                    row.get("estimated_hours")
                )
                assignment_uid = self._ids.remember("task_assignments", f"task:{row['rid']}|{raw_assignee}")
                if assignment_uid is None:
                    continue
                result[emp_uid].append(
                    Assignment(
                        id=assignment_uid,
                        task_id=task_uid,
                        task_code=as_text(row.get("task_code"), 80) or str(row["rid"]),
                        task_title=as_text(row.get("title"), 300) or "Task",
                        project_id=self._ids.remember("projects", row.get("project_id")),
                        assignment_status=a_status,
                        planned_start=as_datetime(row.get("planned_start")),
                        planned_end=as_datetime(row.get("deadline")),
                        allocated_hours=hours if hours is not None else 8.0,
                        active_assignee_count=1,
                    )
                )
        return dict(result)

    def get_workforce_data(
        self,
        company_id: UUID,
        employee_ids: list[UUID],
        *,
        window_start: datetime,
        window_end: datetime,
        attendance_since: datetime,
    ) -> dict[UUID, EmployeeWorkforceData]:
        if not employee_ids:
            return {}
        employees = self._employees()
        by_uid = {row["_uid"]: row for row in employees.rows() if row.get("_uid") in set(employee_ids)}
        schedules = self._schedule(employee_ids)
        attendance = self._attendance(employee_ids, attendance_since)
        leaves = self._leaves(employee_ids, attendance_since)
        assignments = self._assignments(employee_ids)
        logged = self._logged_hours()

        has_status_col = self._col("employees", "employment_status") is not None
        out: dict[UUID, EmployeeWorkforceData] = {}
        for eid in employee_ids:
            row = by_uid.get(eid)
            if row is None:
                continue
            employee_assignments = [
                assignment.model_copy(update={"logged_hours": logged.get((assignment.task_id, eid), 0.0)})
                for assignment in assignments.get(eid, ())
            ]
            emp_status = (
                employment_status(
                    row.get("employment_status"),
                    default_if_missing=EmploymentStatus.ACTIVE,
                )
                if has_status_col
                else EmploymentStatus.ACTIVE
            )
            out[eid] = EmployeeWorkforceData(
                employee_id=eid,
                timezone=as_text(row.get("timezone"), 60) or self._tz,
                employment_status=emp_status,
                schedule=tuple(schedules.get(eid, ())),
                attendance=tuple(attendance.get(eid, ())),
                leaves=tuple(leaves.get(eid, ())),
                assignments=tuple(employee_assignments),
            )
        return out

    def get_holidays(self, company_id: UUID, start: date, end: date) -> list[Holiday]:
        if not self._has("holidays"):
            return []
        h = self._tm("holidays")
        date_col = h.col("holiday_date")
        if not date_col:
            return []
        name_col = h.col("name")
        sql = (
            f"SELECT {quote(date_col)} AS holiday_date, "
            f"{_or_null(f'{quote(name_col)}' if name_col else None)} AS holiday_name "
            f"FROM {quote(h.actual_table or '')}"
        )
        out: list[Holiday] = []
        for row in run(self._c, sql):
            day = as_date(row.get("holiday_date"))
            if day is not None and start <= day <= end:
                out.append(
                    Holiday(
                        holiday_date=day,
                        name=as_text(row.get("holiday_name"), 200) or "Holiday",
                    )
                )
        out.sort(key=lambda x: x.holiday_date)
        return out


# Performance


class AdaptivePerformanceRepository(AdaptiveRepository):
    def get_outcomes(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, list[TaskOutcomeRecord]]:
        if not (employee_ids and self._has("tasks")):
            return {}
        wanted = set(employee_ids)
        result: dict[UUID, list[TaskOutcomeRecord]] = defaultdict(list)
        if self._has("task_outcomes"):
            outcomes, tasks = self._tm("task_outcomes"), self._tm("tasks")
            emp_col, task_fk = outcomes.col("employee_id"), outcomes.col("task_id")
            task_pk = tasks.col("id")
            if emp_col and task_fk and task_pk:
                quality_col = outcomes.col("quality_score")
                accepted_col = outcomes.col("accepted")
                rework_col = outcomes.col("rework_count")
                on_time_col = outcomes.col("on_time")
                reviewed_col = outcomes.col("reviewed_at")
                task_code_col = tasks.col("task_code")
                sql = (
                    f"SELECT o.{quote(emp_col)} AS emp, "
                    f"t.{quote(task_pk)} AS task_rid, "
                    f"{_or_null(f't.{quote(task_code_col)}' if task_code_col else None)} AS task_code, "
                    f"{self._expr_or_null('tasks', 'project_id', 't')} AS project_id, "
                    f"{self._expr_or_null('tasks', 'task_category', 't')} AS task_category, "
                    f"{self._expr_or_null('tasks', 'complexity', 't')} AS complexity, "
                    f"{_or_null(f'o.{quote(quality_col)}' if quality_col else None)} AS quality, "
                    f"{_or_null(f'o.{quote(accepted_col)}' if accepted_col else None)} AS accepted, "
                    f"{_or_null(f'o.{quote(rework_col)}' if rework_col else None)} AS rework, "
                    f"{_or_null(f'o.{quote(on_time_col)}' if on_time_col else None)} AS on_time, "
                    f"{_or_null(f'o.{quote(reviewed_col)}' if reviewed_col else None)} AS reviewed_at "
                    f"FROM {quote(outcomes.actual_table or '')} AS o "
                    f"JOIN {quote(tasks.actual_table or '')} AS t "
                    f"ON t.{quote(task_pk)} = o.{quote(task_fk)}"
                )
                for row in run(self._c, sql):
                    emp_uid = self._ids.remember("employees", row["emp"])
                    task_uid = self._ids.remember("tasks", row["task_rid"])
                    if emp_uid not in wanted or task_uid is None:
                        continue
                    quality = as_float(row.get("quality"))
                    result[emp_uid].append(
                        TaskOutcomeRecord(
                            task_id=task_uid,
                            task_code=as_text(row.get("task_code"), 80) or str(row["task_rid"]),
                            project_id=self._ids.remember("projects", row.get("project_id")),
                            task_category=task_category(row.get("task_category")),
                            complexity=task_complexity(row.get("complexity")),
                            quality_score=max(0.0, min(100.0, quality)) if quality is not None else None,
                            accepted=as_bool(row.get("accepted")),
                            rework_count=max(0, as_int(row.get("rework"), 0) or 0),
                            on_time=as_bool(row.get("on_time")),
                            reviewed_at=as_datetime(row.get("reviewed_at")),
                        )
                    )
                if result:
                    return dict(result)

        # Fallback: derive completed task outcomes directly from tasks.assignee_id.
        if self._col("tasks", "assignee_id"):
            task_repo = AdaptiveTaskRepository(
                self._c,
                self._m,
                self._ids,
                dialect=self._dialect,
                company_code=self._company_code,
            )
            for row in task_repo.rows():
                if task_status(row.get("status")) != TaskStatus.DONE:
                    continue
                raw_assignee = row.get("assignee_id")
                if raw_assignee is None:
                    continue
                emp_uid = self._ids.remember("employees", raw_assignee)
                task_uid = row.get("_uid")
                if emp_uid not in wanted or task_uid is None:
                    continue
                completed_at = as_datetime(row.get("completed_at"))
                deadline = as_datetime(row.get("deadline"))
                on_time = (completed_at <= deadline) if (completed_at and deadline) else None
                quality = as_float(row.get("quality_score"))
                if quality is not None and 0 < quality <= 5.0:
                    quality *= 20.0
                raw_cat = row.get("task_category")
                if raw_cat is not None:
                    task_cat = task_category(raw_cat)
                else:
                    corpus = " ".join(
                        str(p)
                        for p in (row.get("title"), row.get("description"), row.get("skills_text"))
                        if p
                    )
                    if corpus.strip():
                        from app.intelligence.analyzer import TaskAnalyzer

                        task_cat = TaskAnalyzer._infer_category(corpus, [], [])
                    else:
                        task_cat = task_category(None)
                result[emp_uid].append(
                    TaskOutcomeRecord(
                        task_id=task_uid,
                        task_code=as_text(row.get("task_code"), 80) or str(row["rid"]),
                        project_id=self._ids.remember("projects", row.get("project_id")),
                        task_category=task_cat,
                        complexity=task_complexity(row.get("complexity")),
                        quality_score=max(0.0, min(100.0, quality)) if quality is not None else None,
                        accepted=True,
                        rework_count=0,
                        on_time=on_time,
                        reviewed_at=completed_at,
                    )
                )
        return dict(result)

    def get_project_history(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, dict[UUID, int]]:
        if not (employee_ids and self._has("tasks")):
            return {}
        wanted = set(employee_ids)
        out: dict[UUID, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))
        if self._has("task_assignments"):
            assignments, tasks = self._tm("task_assignments"), self._tm("tasks")
            emp_col, task_fk = assignments.col("employee_id"), assignments.col("task_id")
            task_pk, project_fk = tasks.col("id"), tasks.col("project_id")
            if emp_col and task_fk and task_pk and project_fk:
                sql = (
                    f"SELECT ta.{quote(emp_col)} AS emp, t.{quote(project_fk)} AS project_rid "
                    f"FROM {quote(assignments.actual_table or '')} AS ta "
                    f"JOIN {quote(tasks.actual_table or '')} AS t "
                    f"ON t.{quote(task_pk)} = ta.{quote(task_fk)}"
                )
                for row in run(self._c, sql):
                    emp_uid = self._ids.remember("employees", row["emp"])
                    project_uid = self._ids.remember("projects", row.get("project_rid"))
                    if emp_uid in wanted and project_uid is not None:
                        out[emp_uid][project_uid] += 1
                if out:
                    return {k: dict(v) for k, v in out.items()}

        if self._col("tasks", "assignee_id") and self._col("tasks", "project_id"):
            task_repo = AdaptiveTaskRepository(
                self._c,
                self._m,
                self._ids,
                dialect=self._dialect,
                company_code=self._company_code,
            )
            for row in task_repo.rows():
                raw_assignee = row.get("assignee_id")
                raw_project = row.get("project_id")
                if raw_assignee is None or raw_project is None:
                    continue
                emp_uid = self._ids.remember("employees", raw_assignee)
                project_uid = self._ids.remember("projects", raw_project)
                if emp_uid in wanted and project_uid is not None:
                    out[emp_uid][project_uid] += 1
        return {k: dict(v) for k, v in out.items()}

    def record_outcome(self, company_id: UUID, outcome: NewTaskOutcome) -> UUID:
        raise ReadOnlySourceError("The connected database is read-only: outcomes cannot be recorded here")

    def evaluation_summary(self, company_id: UUID) -> EvaluationSummary:
        # Computed from the application's own stored runs; a foreign source has nothing to summarize.
        return EvaluationSummary(
            recommendations_by_status={},
            average_primary_coverage=None,
            outcomes_recorded=0,
            outcomes_for_recommended_primary=0,
            average_quality_recommended_primary=None,
            average_quality_other_assignees=None,
        )

    def get_evaluations(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 12
    ) -> list[PerformanceEvaluation]:
        return []

    def get_metric_snapshots(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 60
    ) -> list[PerformanceMetricSnapshot]:
        return []


# Recommendations and audit: the application's own output, never written to a foreign source


class InMemoryRecommendationRepository:
    """Fallback store when no internal database is configured (process lifetime only)."""

    def __init__(self) -> None:
        self._runs: dict[UUID, StoredRecommendation] = {}
        self._lock = threading.RLock()

    def save_run(self, run: RecommendationRunRecord, request_payload: dict[str, Any]) -> None:
        with self._lock:
            self._runs[run.id] = StoredRecommendation(
                id=run.id,
                company_id=run.company_id,
                status=run.status,
                task_id=run.task_id,
                generated_at=run.as_of,
                result_snapshot=run.result_snapshot,
                request_payload=request_payload,
                request_id=run.request_id,
                requested_by=run.requested_by,
                thread_id=run.thread_id,
                idempotency_key=run.idempotency_key,
                agent_mode=run.agent_mode,
                llm_provider=run.llm_provider,
                llm_model=run.llm_model,
                llm_calls=run.llm_calls,
                llm_prompt_tokens=run.llm_prompt_tokens,
                llm_completion_tokens=run.llm_completion_tokens,
                tool_calls_count=run.tool_calls_count,
                error_category=run.error_category,
                resumable=run.resumable,
                retry_of_id=run.retry_of_id,
            )

    def get_run(self, company_id: UUID, recommendation_id: UUID) -> StoredRecommendation | None:
        run = self._runs.get(recommendation_id)
        return run if run and run.company_id == company_id else None

    def find_by_idempotency_key(self, company_id: UUID, key: str) -> StoredRecommendation | None:
        return next(
            (r for r in self._runs.values() if r.company_id == company_id and r.idempotency_key == key),
            None,
        )

    def list_node_logs(self, company_id: UUID, recommendation_id: UUID) -> list[ExecutionTraceEntry]:
        return []

    def list_tool_executions(self, company_id: UUID, recommendation_id: UUID) -> list[ToolExecutionRecord]:
        return []

    def list_run_errors(self, company_id: UUID, recommendation_id: UUID) -> list[RunErrorRecord]:
        return []

    def list_run_ids_before(self, cutoff: datetime) -> list[UUID]:
        return []

    def save_task_analysis(
        self, company_id: UUID, task_id: UUID | None, analysis: dict[str, Any], analyzer_version: str
    ) -> UUID:
        return uuid5(NAMESPACE_URL, f"analysis/{company_id}/{task_id}/{analyzer_version}")


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(
        self,
        company_id: UUID | None,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.records.append(
            {
                "company_id": company_id,
                "actor": actor,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "metadata": metadata or {},
            }
        )

# ===========================================================================
# Adaptive Unit of Work
# ===========================================================================

class AdaptiveUnitOfWork:
    def __init__(
        self,
        engine: Engine,
        mapping: SchemaMapping,
        registry: IdRegistry,
        *,
        read_only: bool = True,
        statement_timeout_ms: int | None = None,
        default_timezone: str = "UTC",
        assume_default_schedule: bool = True,
        default_daily_hours: float = 8.0,
        internal_factory: UnitOfWorkFactory | None = None,
        company_code: str = "default",
    ) -> None:
        self._engine = engine
        self._mapping = mapping
        self._registry = registry
        self._read_only = read_only
        self._timeout_ms = statement_timeout_ms
        self._tz = default_timezone
        self._assume_schedule = assume_default_schedule
        self._daily_hours = default_daily_hours
        self._internal_factory = internal_factory
        self._company_code = company_code
        self._conn: Connection | None = None
        self._internal: Any = None

    # Declared for the Protocol; assigned in __enter__.
    companies: CompanyRepository
    employees: EmployeeRepository
    tasks: TaskRepository
    workforce: WorkforceRepository
    performance: PerformanceRepository
    recommendations: RecommendationRepository
    audit: AuditRepository

    @property
    def connection(self) -> Connection | None:
        return self._conn

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def mapping(self) -> SchemaMapping:
        return self._mapping

    @property
    def is_read_only(self) -> bool:
        return True

    def __enter__(self) -> Self:
        conn = self._engine.connect()
        self._conn = conn
        try:
            self._apply_guards()
        except Exception:
            conn.close()
            self._conn = None
            raise
        kwargs: dict[str, Any] = {
            "dialect": dialect_of(self._engine),
            "company_code": self._company_code,
            "default_timezone": self._tz,
            "assume_default_schedule": self._assume_schedule,
            "default_daily_hours": self._daily_hours,
        }
        self.companies = AdaptiveCompanyRepository(conn, self._mapping, self._registry, **kwargs)
        self.employees = AdaptiveEmployeeRepository(conn, self._mapping, self._registry, **kwargs)
        self.tasks = AdaptiveTaskRepository(conn, self._mapping, self._registry, **kwargs)
        self.workforce = AdaptiveWorkforceRepository(conn, self._mapping, self._registry, **kwargs)
        self.performance = AdaptivePerformanceRepository(conn, self._mapping, self._registry, **kwargs)

        if self._internal_factory is not None:
            self._internal = self._internal_factory(read_only=False)
            self._internal.__enter__()
            self.recommendations = self._internal.recommendations
            self.audit = self._internal.audit
        else:
            self.recommendations = InMemoryRecommendationRepository()
            self.audit = InMemoryAuditRepository()
        return self

    def _apply_guards(self) -> None:
        assert self._conn is not None
        dialect = dialect_of(self._engine)
        if self._read_only and dialect == "postgresql":
            # Enforced by the server: any write in this transaction fails.
            self._conn.execute(text("SET TRANSACTION READ ONLY"))
        if self._timeout_ms:
            ms = int(self._timeout_ms)
            if dialect == "postgresql":
                self._conn.execute(text(f"SET LOCAL statement_timeout = {ms}"))
            elif dialect == "sqlite":
                self._conn.execute(text(f"PRAGMA busy_timeout = {ms}"))

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._internal is not None:
            try:
                self._internal.__exit__(exc_type, exc, tb)
            except Exception:
                pass
        if self._conn is not None:
            try:
                self._conn.rollback()  # a read-only transaction has nothing to commit
            except Exception:
                pass
            finally:
                self._conn.close()
                self._conn = None

    def commit(self) -> None:
        if self._internal is not None:
            self._internal.commit()

    def rollback(self) -> None:
        if self._internal is not None:
            self._internal.rollback()

    def ping(self) -> None:
        assert self._conn is not None
        self._conn.execute(text("SELECT 1"))


class AdaptiveUnitOfWorkFactory:
    """Callable matching ``app.domain.interfaces.UnitOfWorkFactory``."""

    def __init__(
        self,
        engine: Engine,
        mapping: SchemaMapping,
        *,
        default_timezone: str = "UTC",
        assume_default_schedule: bool = True,
        default_daily_hours: float = 8.0,
        internal_factory: UnitOfWorkFactory | None = None,
        company_code: str = "default",
    ) -> None:
        if not mapping.is_usable():
            from app.infrastructure.discovery import MappingError

            raise MappingError(
                "The discovered schema has no employees table; review schema_mapping.json "
                "or connect a database that stores employees"
            )
        self.engine = engine
        self.mapping = mapping
        self.registry = IdRegistry()
        self._tz = default_timezone
        self._assume_schedule = assume_default_schedule
        self._daily_hours = default_daily_hours
        self._internal_factory = internal_factory
        self._company_code = company_code

    def __call__(
        self, *, read_only: bool = False, statement_timeout_ms: int | None = None
    ) -> UnitOfWork:
        return AdaptiveUnitOfWork(
            self.engine,
            self.mapping,
            self.registry,
            read_only=read_only or True,  # the foreign source is always opened read-only
            statement_timeout_ms=statement_timeout_ms,
            default_timezone=self._tz,
            assume_default_schedule=self._assume_schedule,
            default_daily_hours=self._daily_hours,
            internal_factory=self._internal_factory,
            company_code=self._company_code,
        )

# ===========================================================================
# Data Source Resolver
# ===========================================================================

# Columns the application's ORM needs on its core tables. Present together they mean "our schema".
_NATIVE_MARKERS: dict[str, tuple[str, ...]] = {
    "employees": ("employee_code", "company_id", "employment_status"),
    "tasks": ("task_code", "company_id", "title"),
    "companies": ("code", "timezone"),
    "skills": ("name", "category"),
}

# A saved mapping is reused only while the tables it names still exist.
_MAPPING_FRESHNESS_TABLES = 3


@dataclass
class ResolvedDataSource:
    kind: str  # "native" | "adaptive"
    factory: UnitOfWorkFactory
    mapping: SchemaMapping | None = None
    reason: str = ""

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "reason": self.reason}
        if self.mapping is not None:
            concepts = self.mapping.available_concepts()
            out["discovered_concepts"] = concepts
            out["mapping_file"] = str(_mapping_path())
            out["concept_tables"] = {
                concept: {
                    "table": tm.actual_table,
                    "confidence": tm.confidence,
                    "mapped_columns": sum(1 for cm in tm.columns.values() if cm.actual_column is not None),
                }
                for concept in concepts
                if (tm := getattr(self.mapping, concept)) is not None
            }
        return out


def _mapping_path(settings: Settings | None = None) -> Path:
    if settings is not None:
        return Path(settings.schema_mapping_path)
    return Path("schema_mapping.json")


def looks_like_native_schema(engine: Engine) -> bool:
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        for table, required in _NATIVE_MARKERS.items():
            if table not in tables:
                return False
            columns = {c["name"].lower() for c in inspector.get_columns(table)}
            if not set(required) <= columns:
                return False
        return True
    except Exception as exc:
        logger.warning("native-schema probe failed: %s", exc)
        return False


def _saved_mapping_is_usable(
    mapping: SchemaMapping | None, engine: Engine, *, db_url_masked: str = ""
) -> bool:
    if mapping is None or not mapping.is_usable():
        return False
    if (
        db_url_masked
        and mapping.source_db_url_masked
        and mapping.source_db_url_masked != db_url_masked
    ):
        return False
    try:
        tables = set(inspect(engine).get_table_names())
    except Exception:
        return False
    if mapping.employees.actual_table not in tables:
        return False
    present = [getattr(mapping, concept).actual_table for concept in mapping.available_concepts()]
    required_count = min(len(present), _MAPPING_FRESHNESS_TABLES)
    return sum(1 for t in present if t in tables) >= max(1, required_count)


def resolve(
    engine: Engine,
    settings: Settings,
    *,
    db_url_masked: str = "",
    internal_factory: UnitOfWorkFactory | None = None,
    force: str | None = None,
    llm: LLMProvider | None = None,
) -> ResolvedDataSource:
    """Build the factory for ``engine`` according to ``settings.database_adapter``."""
    adapter = force or settings.database_adapter

    if adapter in ("native", "auto") and looks_like_native_schema(engine):
        logger.info("data source resolved as native (application schema)")
        return ResolvedDataSource(
            kind="native",
            factory=SqlAlchemyUnitOfWorkFactory(create_session_factory(engine)),
            reason="the database has the application's own schema",
        )
    if adapter == "native":
        logger.warning("database_adapter=native but the schema does not match; falling back to adaptive")

    loaded = load_mapping(_mapping_path(settings))
    if _saved_mapping_is_usable(loaded, engine, db_url_masked=db_url_masked) and loaded is not None:
        mapping = loaded
    else:
        try:
            mapping = SchemaDiscovery(engine, llm=llm).discover(db_url_masked=db_url_masked)
        except Exception as exc:
            raise MappingError(
                "Schema discovery failed for the connected database",
                details={"error": type(exc).__name__},
            ) from exc
        if mapping.is_usable():
            try:
                save_mapping(mapping, _mapping_path(settings))
            except OSError as exc:  # read-only filesystem: keep the in-memory mapping
                logger.warning("could not save schema mapping: %s", exc)
    if not mapping.is_usable():
        raise MappingError(
            "No employees table could be identified in the connected database. "
            f"Edit {_mapping_path(settings)} to map the right tables and retry."
        )

    factory = AdaptiveUnitOfWorkFactory(
        engine,
        mapping,
        default_timezone=settings.adaptive_default_timezone,
        assume_default_schedule=settings.adaptive_assume_default_schedule,
        default_daily_hours=settings.adaptive_default_daily_hours,
        internal_factory=internal_factory,
    )
    logger.info(
        "data source resolved as adaptive: %s",
        ", ".join(mapping.available_concepts()),
    )
    return ResolvedDataSource(
        kind="adaptive",
        factory=factory,
        mapping=mapping,
        reason="schema discovered from the connected database",
    )

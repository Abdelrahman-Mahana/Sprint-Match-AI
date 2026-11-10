"""Read-only repositories over a *discovered* foreign schema.

Each repository implements the same Protocol as the PostgreSQL one (``app.domain.interfaces``) but
addresses whatever tables/columns the schema mapping resolved. Design rules:

* **Read-only.** The connected external database is a system of record. Every write raises
  ``ReadOnlySourceError``. The agent's own output (runs, audit, checkpoints) is persisted by the
  application's internal store, never into the foreign database.
* **Degrade, never fail.** A missing table or column yields an empty result or a documented default;
  the deterministic pipeline then reports the data gap (e.g. ``AVAILABILITY_UNKNOWN``) instead of
  crashing the run.
* **Tolerant value handling.** Enum-like text from a foreign system is coerced by
  ``app.infrastructure.adaptive.coerce``; a malformed row is skipped, never fatal.
* **Bounded reads.** Employee-level reads are capped and filtered in Python when the source's value
  conventions are unknown (status text like "on hold", numeric story points, ...). Agent tool calls
  carry a server-side statement timeout, so a pathological table cannot hang a run.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from datetime import date, datetime, time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.engine import Connection

from app.domain.entities.employee import (
    Employee,
    EmployeeProfile,
    EmployeeSkill,
    ProjectMembership,
    Skill,
)
from app.domain.entities.organization import Company, Holiday, Sprint
from app.domain.entities.performance import (
    PerformanceEvaluation,
    PerformanceMetricSnapshot,
    TaskOutcomeRecord,
)
from app.domain.entities.task import RequiredSkill, TaskRecord
from app.domain.entities.workforce import (
    Assignment,
    AttendanceRecord,
    EmployeeWorkforceData,
    LeaveRecord,
    ScheduleDay,
)
from app.domain.enums import EmploymentStatus, LeaveStatus, RequirementSource
from app.domain.interfaces import (
    EvaluationSummary,
    ExecutionTraceEntry,
    NewTask,
    NewTaskOutcome,
    Page,
    RecommendationRunRecord,
    RunErrorRecord,
    StoredRecommendation,
    ToolExecutionRecord,
)
from app.infrastructure.adaptive import coerce
from app.infrastructure.adaptive.ids import IdRegistry
from app.infrastructure.adaptive.sql import (
    MappingError,
    ReadOnlySourceError,
    quote,
    run,
    run_one,
)
from app.infrastructure.schema_discovery.mapping import SchemaMapping, TableMapping

logger = logging.getLogger(__name__)

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

    # --- mapping helpers ---------------------------------------------------------------------
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
        return f'{quote(alias)}.{quote(column)}' if alias else quote(column)

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


# ---------------------------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------------------------


class AdaptiveCompanyRepository(AdaptiveRepository):
    def _rows(self) -> list[dict[str, Any]]:
        if not self._has("companies"):
            return []
        c = quote(self._table("companies"))
        alias = "c"
        id_col = self._col("companies", "id")
        rid = f'{alias}.{quote(id_col)}' if id_col else self._expr_or_null("companies", "code", alias)
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
            code=coerce.as_text(row.get("company_code"), 60) or fallback_code,
            name=coerce.as_text(row.get("company_name"), 200) or "Connected database",
            timezone=coerce.as_text(row.get("timezone"), 60) or self._tz,
            working_weekdays=self._weekdays,
        )

    def get_by_code(self, code: str) -> Company | None:
        rows = self._rows()
        for row in rows:
            if (coerce.as_text(row.get("company_code"), 60) or "").lower() == code.strip().lower():
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


# ---------------------------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------------------------


class AdaptiveEmployeeRepository(AdaptiveRepository):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cache: list[dict[str, Any]] | None = None

    # --- rows ---------------------------------------------------------------------------------
    def rows(self) -> list[dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        if not self._has("employees") or self._col("employees", "id") is None:
            self._cache = []
            return self._cache

        alias = "e"
        table = quote(self._table("employees"))
        id_col = self._col("employees", "id")
        select_parts = [f'{alias}.{quote(id_col or "id")} AS rid']
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
            ("team_id", "team_id"),
            ("skills_text", "skills_text"),
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
                select_parts.append(f'd.{quote(dept_name)} AS department_name')

        team = self._tm("teams")
        if self._has("teams") and self._col("employees", "team_id"):
            team_id, team_name = team.col("id"), team.col("name")
            if team_id and team_name:
                joins.append(
                    f"LEFT JOIN {quote(team.actual_table or '')} AS tm "
                    f"ON tm.{quote(team_id)} = {alias}.{quote(self._col('employees', 'team_id') or '')}"
                )
                select_parts.append(f'tm.{quote(team_name)} AS team_name')

        sql = (
            f"SELECT {', '.join(select_parts)} FROM {table} AS {alias} "
            f"{' '.join(joins)} LIMIT {MAX_ROWS}"
        )
        rows = run(self._c, sql)
        for row in rows:
            row["_uid"] = self._ids.remember("employees", row["rid"])
        self._cache = rows
        return rows

    # --- domain -------------------------------------------------------------------------------
    def _to_domain(self, row: dict[str, Any]) -> Employee | None:
        uid = row.get("_uid") or self._ids.remember("employees", row.get("rid"))
        if uid is None:
            return None
        code = coerce.as_text(row.get("employee_code"), 60) or coerce.as_text(row.get("rid"), 60) or str(uid)
        return Employee(
            id=uid,
            company_id=self._company_id(),
            employee_code=code,
            full_name=coerce.as_text(row.get("full_name"), 200) or code,
            email=coerce.as_text(row.get("email"), 254),
            job_title=coerce.as_text(row.get("job_title"), 120) or "Unspecified",
            department_id=self._ids.remember("departments", row.get("department_id"))
            if row.get("department_id") is not None
            else None,
            department_name=coerce.as_text(row.get("department_name"), 120),
            team_id=self._ids.remember("teams", row.get("team_id"))
            if row.get("team_id") is not None
            else None,
            team_name=coerce.as_text(row.get("team_name"), 120),
            hire_date=coerce.as_date(row.get("hire_date")),
            experience_years=coerce.as_float(row.get("experience_years")),
            employment_status=coerce.employment_status(row.get("employment_status")),
            timezone=coerce.as_text(row.get("timezone"), 60) or self._tz,
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

    # --- Protocol -----------------------------------------------------------------------------
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
        employees = self.filtered(
            status=status, department=department, team=team, job_title=job_title
        )
        for name in skill_names:
            holders = self._ids_with_skill(name)
            employees = [e for e in employees if e.id in holders]
        return Page[Employee](
            items=tuple(employees[:limit]), total=len(employees), limit=limit, offset=0
        )

    def update_employee(self, company_id: UUID, employee_id: UUID, **_: Any) -> Employee:
        raise ReadOnlySourceError(
            "The connected database is read-only: employee data cannot be changed here"
        )

    # --- skills -------------------------------------------------------------------------------
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
                for row in run(self._c, sql):
                    emp_uid = self._ids.remember("employees", row["emp"])
                    name = coerce.as_text(row.get("skill_name"), 100)
                    if emp_uid is None or not name:
                        continue
                    level = coerce.as_int(row.get("level"), DEFAULT_SKILL_LEVEL) or 0
                    result[emp_uid].append(
                        EmployeeSkill(
                            skill_id=self._ids.remember("skills", row["skill_rid"])
                            or self._skill_uuid(name),
                            skill_name=name,
                            proficiency_level=max(0, min(100, level)),
                            verification_status=coerce.skill_verification(row.get("verification")),
                        )
                    )
        if result:
            return dict(result)

        # Fallback: many HR systems keep skills as free text on the employee row.
        for row in self.rows():
            text = coerce.as_text(row.get("skills_text"), 2000)
            uid = row.get("_uid")
            if not text or uid is None:
                continue
            for token in _split_skill_text(text):
                result[uid].append(
                    EmployeeSkill(
                        skill_id=self._skill_uuid(token),
                        skill_name=token,
                        proficiency_level=DEFAULT_SKILL_LEVEL,
                        verification_status=coerce.skill_verification("SELF_DECLARED"),
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
                    project_name=coerce.as_text(row.get("project_name"), 200) or "Project",
                    role=coerce.as_text(row.get("role"), 80) or "member",
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


def _split_skill_text(text: str) -> list[str]:
    parts = re.split(r"[,;|/\n]+", text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) <= 100][:40]


# ---------------------------------------------------------------------------------------------
# Skills catalogue
# ---------------------------------------------------------------------------------------------


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
                    name = coerce.as_text(row.get("skill_name"), 100)
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
                            category=coerce.as_text(row.get("category"), 80) or "general",
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


# ---------------------------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------------------------


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
        select_parts = [f'{alias}.{quote(self._col("tasks", "id") or "id")} AS rid']
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
                select_parts.append(f'pr.{quote(project_name)} AS project_name')

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
            name = coerce.as_text(row.get("skill_name"), 100)
            if task_uid not in wanted or not name:
                continue
            result[task_uid].append(
                RequiredSkill(
                    skill_id=self._ids.remember("skills", row.get("skill_rid")),
                    skill_name=name,
                    minimum_level=max(0, min(100, coerce.as_int(row.get("min_level"), 50) or 50)),
                    mandatory=coerce.as_bool(row.get("mandatory"), default=False) or False,
                    weight=coerce.as_float(row.get("weight")) or 1.0,
                    source=RequirementSource.TASK_RECORD,
                )
            )
        return dict(result)

    def _to_domain(self, row: dict[str, Any]) -> TaskRecord | None:
        uid = row.get("_uid")
        title = coerce.as_text(row.get("title"), 300)
        if uid is None or not title:
            return None
        return TaskRecord(
            id=uid,
            company_id=self._company_id(),
            task_code=coerce.as_text(row.get("task_code"), 80) or str(row.get("rid")),
            project_id=self._ids.remember("projects", row.get("project_id"))
            if row.get("project_id") is not None
            else None,
            project_name=coerce.as_text(row.get("project_name"), 200),
            title=title,
            description=coerce.as_text(row.get("description"), 20_000),
            complexity=coerce.task_complexity(row.get("complexity")),
            priority=coerce.task_priority(row.get("priority")),
            status=coerce.task_status(row.get("status")),
            estimated_hours=coerce.as_float(row.get("estimated_hours")),
            remaining_estimated_hours=coerce.as_float(row.get("remaining_estimated_hours")),
            planned_start=coerce.as_datetime(row.get("planned_start")),
            deadline=coerce.as_datetime(row.get("deadline")),
            task_category=coerce.task_category(row.get("task_category")),
            acceptance_criteria=coerce.as_text(row.get("acceptance_criteria"), 10_000),
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
            code = (coerce.as_text(row.get("task_code"), 80) or str(row.get("rid"))).lower()
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
        raise ReadOnlySourceError(
            "The connected database is read-only: tasks cannot be created here"
        )

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
        start, end = coerce.as_date(row.get("start_date")), coerce.as_date(row.get("end_date"))
        if start is None or end is None:
            return None
        return Sprint(
            id=sprint_id,
            project_id=self._ids.remember("projects", row.get("project_id")) or UUID(int=0),
            name=coerce.as_text(row.get("sprint_name"), 200) or "Sprint",
            start_date=start,
            end_date=end,
            status=(coerce.as_text(row.get("sprint_status"), 40) or "ACTIVE").upper(),
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


# ---------------------------------------------------------------------------------------------
# Workforce
# ---------------------------------------------------------------------------------------------


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
                    day = coerce.as_int(row.get("weekday"))
                    if day is None or not 0 <= day <= 6:
                        continue
                    start = coerce.as_time(row.get("start_time")) or time(9, 0)
                    end = coerce.as_time(row.get("end_time")) or time(17, 0)
                    if end <= start:
                        continue
                    result[uid].append(
                        ScheduleDay(
                            weekday=day,
                            start_time=start,
                            end_time=end,
                            break_minutes=max(
                                0, min(240, coerce.as_int(row.get("break_minutes"), 0) or 0)
                            ),
                        )
                    )
                if result:
                    return dict(result)

        if not self._assume_schedule:
            return {}
        # Documented assumption for sources without a schedule table: a standard working week. The
        # engine reports the resulting certainty as ESTIMATED; disable with
        # ADAPTIVE_ASSUME_DEFAULT_SCHEDULE=false.
        per_day = max(1.0, min(16.0, self._daily_hours))
        start_hour = 9
        end_hour = min(23, start_hour + int(round(per_day)) + 1)
        for uid in employee_ids:
            for weekday in self._weekdays:
                result[uid].append(
                    ScheduleDay(
                        weekday=weekday,
                        start_time=time(start_hour, 0),
                        end_time=time(end_hour, 0),
                        break_minutes=60,
                    )
                )
        return dict(result)

    def _attendance(
        self, employee_ids: list[UUID], since: datetime
    ) -> dict[UUID, list[AttendanceRecord]]:
        result: dict[UUID, list[AttendanceRecord]] = defaultdict(list)
        if not self._has("attendance"):
            return {}
        at = self._tm("attendance")
        emp_col = at.col("employee_id")
        if not emp_col:
            return {}
        date_col, cin_col, cout_col = at.col("date"), at.col("check_in"), at.col("check_out")
        date_expr = _or_null(
            quote(date_col) if date_col else (quote(cin_col) if cin_col else None)
        )
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
            work_date = coerce.as_date(row.get("work_date"))
            if work_date is None or work_date < since.date():
                continue
            check_in = coerce.as_datetime(row.get("check_in"))
            try:
                result[uid].append(
                    AttendanceRecord(
                        work_date=work_date,
                        check_in=check_in,
                        check_out=coerce.as_datetime(row.get("check_out")),
                        status=coerce.attendance_status(
                            "PRESENT" if check_in is not None else "ABSENT"
                        ),
                    )
                )
            except Exception:  # malformed source row (e.g. check-out before check-in)
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
            start = coerce.as_datetime(row.get("start_date"))
            end = coerce.as_datetime(row.get("end_date")) or start
            if start is None or end is None or end < since:
                continue
            if end.date() == start.date():
                end = end.replace(hour=23, minute=59, second=59)
            status = coerce.leave_status(row.get("leave_status"))
            if status not in (LeaveStatus.APPROVED, LeaveStatus.REQUESTED):
                continue
            leave_uid = self._ids.remember(
                "leaves", row.get("rid") or f"{uid}:{start.isoformat()}"
            )
            if leave_uid is None:
                continue
            result[uid].append(
                LeaveRecord(
                    id=leave_uid,
                    leave_type=coerce.leave_type(row.get("leave_type")),
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
            hours = coerce.as_float(row.get("hours"))
            if task_uid and emp_uid and hours:
                totals[(task_uid, emp_uid)] += hours
        return dict(totals)

    def _assignments(self, employee_ids: list[UUID]) -> dict[UUID, list[Assignment]]:
        result: dict[UUID, list[Assignment]] = defaultdict(list)
        if not (self._has("task_assignments") and self._has("tasks")):
            return {}
        ta, tasks = self._tm("task_assignments"), self._tm("tasks")
        emp_col, task_fk = ta.col("employee_id"), ta.col("task_id")
        task_pk, title_col = tasks.col("id"), tasks.col("title")
        if not (emp_col and task_fk and task_pk and title_col):
            return {}

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

        wanted = set(employee_ids)
        for row in rows:
            emp_uid = self._ids.remember("employees", row["emp"])
            if emp_uid not in wanted:
                continue
            task_uid = self._ids.remember("tasks", row["task_rid"])
            if task_uid is None:
                continue
            status = coerce.assignment_status(row.get("assignment_status"))
            if not status.consumes_capacity:
                continue
            assignment_uid = self._ids.remember(
                "task_assignments", f"{row['task_rid']}|{row['emp']}"
            )
            if assignment_uid is None:
                continue
            result[emp_uid].append(
                Assignment(
                    id=assignment_uid,
                    task_id=task_uid,
                    task_code=coerce.as_text(row.get("task_code"), 80) or str(row["task_rid"]),
                    task_title=coerce.as_text(row.get("task_title"), 300) or "Task",
                    project_id=self._ids.remember("projects", row.get("project_id")),
                    assignment_status=status,
                    planned_start=coerce.as_datetime(row.get("planned_start"))
                    or coerce.as_datetime(row.get("assigned_at")),
                    planned_end=coerce.as_datetime(row.get("deadline")),
                    allocated_hours=coerce.as_float(row.get("allocated_hours")),
                    active_assignee_count=max(1, counts.get(task_uid, 1)),
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
        by_uid = {
            row["_uid"]: row
            for row in employees.rows()
            if row.get("_uid") in set(employee_ids)
        }
        schedules = self._schedule(employee_ids)
        attendance = self._attendance(employee_ids, attendance_since)
        leaves = self._leaves(employee_ids, attendance_since)
        assignments = self._assignments(employee_ids)
        logged = self._logged_hours()

        out: dict[UUID, EmployeeWorkforceData] = {}
        for eid in employee_ids:
            row = by_uid.get(eid)
            if row is None:
                continue
            employee_assignments = [
                assignment.model_copy(
                    update={"logged_hours": logged.get((assignment.task_id, eid), 0.0)}
                )
                for assignment in assignments.get(eid, ())
            ]
            out[eid] = EmployeeWorkforceData(
                employee_id=eid,
                timezone=coerce.as_text(row.get("timezone"), 60) or self._tz,
                employment_status=coerce.employment_status(row.get("employment_status")),
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
            day = coerce.as_date(row.get("holiday_date"))
            if day is not None and start <= day <= end:
                out.append(
                    Holiday(
                        holiday_date=day,
                        name=coerce.as_text(row.get("holiday_name"), 200) or "Holiday",
                    )
                )
        out.sort(key=lambda x: x.holiday_date)
        return out


# ---------------------------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------------------------


class AdaptivePerformanceRepository(AdaptiveRepository):
    def get_outcomes(
        self, company_id: UUID, employee_ids: list[UUID]
    ) -> dict[UUID, list[TaskOutcomeRecord]]:
        if not (employee_ids and self._has("task_outcomes") and self._has("tasks")):
            return {}
        outcomes, tasks = self._tm("task_outcomes"), self._tm("tasks")
        emp_col, task_fk = outcomes.col("employee_id"), outcomes.col("task_id")
        task_pk = tasks.col("id")
        if not (emp_col and task_fk and task_pk):
            return {}
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
        wanted = set(employee_ids)
        result: dict[UUID, list[TaskOutcomeRecord]] = defaultdict(list)
        for row in run(self._c, sql):
            emp_uid = self._ids.remember("employees", row["emp"])
            task_uid = self._ids.remember("tasks", row["task_rid"])
            if emp_uid not in wanted or task_uid is None:
                continue
            quality = coerce.as_float(row.get("quality"))
            result[emp_uid].append(
                TaskOutcomeRecord(
                    task_id=task_uid,
                    task_code=coerce.as_text(row.get("task_code"), 80) or str(row["task_rid"]),
                    project_id=self._ids.remember("projects", row.get("project_id")),
                    task_category=coerce.task_category(row.get("task_category")),
                    complexity=coerce.task_complexity(row.get("complexity")),
                    quality_score=max(0.0, min(100.0, quality)) if quality is not None else None,
                    accepted=coerce.as_bool(row.get("accepted")),
                    rework_count=max(0, coerce.as_int(row.get("rework"), 0) or 0),
                    on_time=coerce.as_bool(row.get("on_time")),
                    reviewed_at=coerce.as_datetime(row.get("reviewed_at")),
                )
            )
        return dict(result)

    def get_project_history(
        self, company_id: UUID, employee_ids: list[UUID]
    ) -> dict[UUID, dict[UUID, int]]:
        if not (employee_ids and self._has("task_assignments") and self._has("tasks")):
            return {}
        assignments, tasks = self._tm("task_assignments"), self._tm("tasks")
        emp_col, task_fk = assignments.col("employee_id"), assignments.col("task_id")
        task_pk, project_fk = tasks.col("id"), tasks.col("project_id")
        if not (emp_col and task_fk and task_pk and project_fk):
            return {}
        sql = (
            f"SELECT ta.{quote(emp_col)} AS emp, t.{quote(project_fk)} AS project_rid "
            f"FROM {quote(assignments.actual_table or '')} AS ta "
            f"JOIN {quote(tasks.actual_table or '')} AS t "
            f"ON t.{quote(task_pk)} = ta.{quote(task_fk)}"
        )
        wanted = set(employee_ids)
        out: dict[UUID, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))
        for row in run(self._c, sql):
            emp_uid = self._ids.remember("employees", row["emp"])
            project_uid = self._ids.remember("projects", row.get("project_rid"))
            if emp_uid in wanted and project_uid is not None:
                out[emp_uid][project_uid] += 1
        return {k: dict(v) for k, v in out.items()}

    def record_outcome(self, company_id: UUID, outcome: NewTaskOutcome) -> UUID:
        raise ReadOnlySourceError(
            "The connected database is read-only: outcomes cannot be recorded here"
        )

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


# ---------------------------------------------------------------------------------------------
# Recommendations and audit: the application's own output, never written to a foreign source
# ---------------------------------------------------------------------------------------------


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
            (
                r
                for r in self._runs.values()
                if r.company_id == company_id and r.idempotency_key == key
            ),
            None,
        )

    def list_node_logs(self, company_id: UUID, recommendation_id: UUID) -> list[ExecutionTraceEntry]:
        return []

    def list_tool_executions(
        self, company_id: UUID, recommendation_id: UUID
    ) -> list[ToolExecutionRecord]:
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

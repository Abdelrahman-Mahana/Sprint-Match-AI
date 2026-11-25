"""Native PostgreSQL SQLAlchemy repositories and Unit of Work."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import Select, delete, exists, func, select, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session, sessionmaker

from app.core import ConflictError, DataSourceUnavailableError, InputValidationError, NotFoundError
from app.domain import (
    Assignment,
    AssignmentStatus,
    AttendanceRecord,
    AuditRepository,
    Commitment,
    Company,
    CompanyRepository,
    Credential,
    Employee,
    EmployeeProfile,
    EmployeeRepository,
    EmployeeSkill,
    EmployeeWorkforceData,
    EmploymentStatus,
    EvaluationSummary,
    ExecutionTraceEntry,
    Experience,
    Holiday,
    LeaveRecord,
    LeaveStatus,
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
    SkillEvidence,
    Sprint,
    StoredRecommendation,
    TaskCategory,
    TaskComplexity,
    TaskDependencyRef,
    TaskOutcomeRecord,
    TaskPriority,
    TaskRecord,
    TaskRepository,
    TaskStatus,
    ToolExecutionRecord,
    WorkforceRepository,
)
from app.infrastructure.orm import (
    AgentExecutionLogModel,
    AgentRunErrorModel,
    AgentToolExecutionModel,
    AttendanceRecordModel,
    AuditEventModel,
    CalendarHolidayModel,
    CompanyModel,
    DepartmentModel,
    EmployeeCommitmentModel,
    EmployeeCredentialModel,
    EmployeeExperienceModel,
    EmployeeExperienceSkillModel,
    EmployeeModel,
    EmployeeSkillEvidenceModel,
    EmployeeSkillModel,
    LeaveRecordModel,
    PerformanceEvaluationModel,
    PerformanceMetricModel,
    ProjectMemberModel,
    ProjectModel,
    RecommendationCandidateModel,
    RecommendationRunModel,
    RecommendationScoreComponentModel,
    SkillModel,
    SprintModel,
    TaskAnalysisModel,
    TaskAssignmentModel,
    TaskDependencyModel,
    TaskModel,
    TaskOutcomeModel,
    TaskRequiredSkillModel,
    TeamModel,
    TimesheetEntryModel,
    WorkScheduleDayModel,
)

# ===========================================================================
# Base Helpers
# ===========================================================================

def to_float(value: Decimal | float | int | None) -> float | None:
    return float(value) if value is not None else None


@contextmanager
def translate_db_errors() -> Iterator[None]:
    """Map driver-level connectivity failures to the data-source-agnostic DataSourceUnavailableError."""
    try:
        yield
    except (sa_exc.OperationalError, sa_exc.InterfaceError) as exc:
        raise DataSourceUnavailableError(
            "The database is unavailable",
            details={"error": type(exc.orig).__name__ if exc.orig else "unknown"},
        ) from exc
    except sa_exc.DisconnectionError as exc:
        raise DataSourceUnavailableError("The database connection was lost") from exc


class SqlRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def _execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        with translate_db_errors():
            return self._s.execute(stmt, params or {})

# ===========================================================================
# Employee Repository
# ===========================================================================

class SqlCompanyRepository(SqlRepository):
    @staticmethod
    def _to_domain(m: CompanyModel) -> Company:
        return Company(
            id=m.id, code=m.code, name=m.name, timezone=m.timezone, working_weekdays=tuple(m.working_weekdays)
        )

    def get_by_code(self, code: str) -> Company | None:
        m = self._execute(select(CompanyModel).where(CompanyModel.code == code)).scalar_one_or_none()
        return self._to_domain(m) if m else None

    def get(self, company_id: UUID) -> Company | None:
        m = self._execute(select(CompanyModel).where(CompanyModel.id == company_id)).scalar_one_or_none()
        return self._to_domain(m) if m else None


class SqlEmployeeRepository(SqlRepository):
    def _base(self, company_id: UUID) -> Select[EmployeeModel, str, str]:
        return (
            select(EmployeeModel, DepartmentModel.name, TeamModel.name)
            .outerjoin(DepartmentModel, DepartmentModel.id == EmployeeModel.department_id)
            .outerjoin(TeamModel, TeamModel.id == EmployeeModel.team_id)
            .where(EmployeeModel.company_id == company_id)
        )

    @staticmethod
    def _to_domain(m: EmployeeModel, dept: str | None, team: str | None) -> Employee:
        return Employee(
            id=m.id,
            company_id=m.company_id,
            employee_code=m.employee_code,
            full_name=m.full_name,
            email=m.email,
            job_title=m.job_title,
            department_id=m.department_id,
            department_name=dept,
            team_id=m.team_id,
            team_name=team,
            hire_date=m.hire_date,
            experience_years=to_float(m.experience_years),
            employment_status=m.employment_status,
            timezone=m.timezone,
        )

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
        stmt = self._base(company_id)
        if status is not None:
            stmt = stmt.where(EmployeeModel.employment_status == status)
        if department_id is not None:
            stmt = stmt.where(EmployeeModel.department_id == department_id)
        if skill_name:
            stmt = stmt.where(
                exists()
                .where(EmployeeSkillModel.employee_id == EmployeeModel.id)
                .where(EmployeeSkillModel.skill_id == SkillModel.id)
                .where(func.lower(SkillModel.name) == skill_name.lower())
            )
        total = self._execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = self._execute(stmt.order_by(EmployeeModel.employee_code).limit(limit).offset(offset)).all()
        return Page[Employee](
            items=tuple(self._to_domain(*r) for r in rows), total=total, limit=limit, offset=offset
        )

    def list_employee_ids(self, company_id: UUID, *, active_only: bool = False) -> list[UUID]:
        stmt = select(EmployeeModel.id).where(EmployeeModel.company_id == company_id)
        if active_only:
            stmt = stmt.where(EmployeeModel.employment_status == EmploymentStatus.ACTIVE)
        return list(self._execute(stmt.order_by(EmployeeModel.employee_code)).scalars())

    def get_employee(self, company_id: UUID, employee_id: UUID) -> Employee | None:
        row = self._execute(self._base(company_id).where(EmployeeModel.id == employee_id)).one_or_none()
        return self._to_domain(*row) if row else None

    def update_employee(
        self,
        company_id: UUID,
        employee_id: UUID,
        *,
        full_name: str | None = None,
        job_title: str | None = None,
        experience_years: float | None = None,
        employment_status: EmploymentStatus | None = None,
        email: str | None = None,
    ) -> Employee:
        m = self._execute(
            select(EmployeeModel)
            .where(EmployeeModel.company_id == company_id)
            .where(EmployeeModel.id == employee_id)
        ).scalar_one_or_none()
        if m is None:
            raise NotFoundError("Employee not found")
        if full_name is not None:
            m.full_name = full_name.strip()
        if job_title is not None:
            m.job_title = job_title.strip()
        if experience_years is not None:
            m.experience_years = experience_years
        if employment_status is not None:
            m.employment_status = employment_status
        if email is not None:
            m.email = email.strip()
        self._s.flush()
        emp = self.get_employee(company_id, employee_id)
        assert emp is not None
        return emp

    def get_employee_by_code(self, company_id: UUID, employee_code: str) -> Employee | None:
        row = self._execute(
            self._base(company_id).where(func.upper(EmployeeModel.employee_code) == employee_code.upper())
        ).one_or_none()
        return self._to_domain(*row) if row else None

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
        """Case-insensitive filters; every listed skill must be held (any level, any verification)."""
        stmt = self._base(company_id)
        if department:
            stmt = stmt.where(func.lower(DepartmentModel.name) == department.strip().lower())
        if team:
            stmt = stmt.where(func.lower(TeamModel.name) == team.strip().lower())
        if job_title:
            stmt = stmt.where(EmployeeModel.job_title.ilike(f"%{job_title.strip()}%"))
        if status is not None:
            stmt = stmt.where(EmployeeModel.employment_status == status)
        for name in skill_names:
            stmt = stmt.where(
                exists()
                .where(EmployeeSkillModel.employee_id == EmployeeModel.id)
                .where(EmployeeSkillModel.skill_id == SkillModel.id)
                .where(func.lower(SkillModel.name) == name.strip().lower())
            )
        total = self._execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = self._execute(stmt.order_by(EmployeeModel.employee_code).limit(limit)).all()
        return Page[Employee](
            items=tuple(self._to_domain(*r) for r in rows), total=total, limit=limit, offset=0
        )

    def get_profiles(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, EmployeeProfile]:
        if not employee_ids:
            return {}
        rows = self._execute(self._base(company_id).where(EmployeeModel.id.in_(employee_ids))).all()
        employees = {r[0].id: self._to_domain(*r) for r in rows}
        ids = list(employees)
        if not ids:
            return {}

        skills: dict[UUID, list[EmployeeSkill]] = defaultdict(list)
        for es, name in self._execute(
            select(EmployeeSkillModel, SkillModel.name)
            .join(SkillModel, SkillModel.id == EmployeeSkillModel.skill_id)
            .where(EmployeeSkillModel.employee_id.in_(ids))
            .order_by(SkillModel.name)
        ).all():
            skills[es.employee_id].append(
                EmployeeSkill(
                    skill_id=es.skill_id,
                    skill_name=name,
                    proficiency_level=es.proficiency_level,
                    verification_status=es.verification_status,
                    verified_at=es.verified_at,
                    last_assessed_at=es.last_assessed_at,
                )
            )

        evidence: dict[UUID, list[SkillEvidence]] = defaultdict(list)
        for ev, emp_id, skill_id in self._execute(
            select(EmployeeSkillEvidenceModel, EmployeeSkillModel.employee_id, EmployeeSkillModel.skill_id)
            .join(EmployeeSkillModel, EmployeeSkillModel.id == EmployeeSkillEvidenceModel.employee_skill_id)
            .where(EmployeeSkillModel.employee_id.in_(ids))
            .order_by(EmployeeSkillEvidenceModel.created_at)
        ).all():
            evidence[emp_id].append(
                SkillEvidence(
                    skill_id=skill_id,
                    evidence_type=ev.evidence_type,
                    verification_status=ev.verification_status,
                    project_id=ev.project_id,
                    task_id=ev.task_id,
                    notes=ev.notes,
                    created_at=ev.created_at,
                )
            )

        exp_skills: dict[UUID, list[tuple[UUID, str]]] = defaultdict(list)
        for exp_id, skill_id, name in self._execute(
            select(EmployeeExperienceSkillModel.experience_id, SkillModel.id, SkillModel.name)
            .join(SkillModel, SkillModel.id == EmployeeExperienceSkillModel.skill_id)
            .join(
                EmployeeExperienceModel,
                EmployeeExperienceModel.id == EmployeeExperienceSkillModel.experience_id,
            )
            .where(EmployeeExperienceModel.employee_id.in_(ids))
            .order_by(SkillModel.name)
        ).all():
            exp_skills[exp_id].append((skill_id, name))
        experiences: dict[UUID, list[Experience]] = defaultdict(list)
        for ex in self._execute(
            select(EmployeeExperienceModel)
            .where(EmployeeExperienceModel.employee_id.in_(ids))
            .order_by(EmployeeExperienceModel.relevant_start_date)
        ).scalars():
            sk = exp_skills.get(ex.id, [])
            experiences[ex.employee_id].append(
                Experience(
                    id=ex.id,
                    organization_or_project=ex.organization_or_project,
                    role=ex.role,
                    description=ex.description,
                    start_date=ex.relevant_start_date,
                    end_date=ex.relevant_end_date,
                    skill_ids=tuple(s for s, _ in sk),
                    skill_names=tuple(n for _, n in sk),
                )
            )

        credentials: dict[UUID, list[Credential]] = defaultdict(list)
        for c in self._execute(
            select(EmployeeCredentialModel)
            .where(EmployeeCredentialModel.employee_id.in_(ids))
            .order_by(EmployeeCredentialModel.issued_at)
        ).scalars():
            credentials[c.employee_id].append(
                Credential(
                    name=c.name,
                    issuer=c.issuer,
                    credential_type=c.credential_type,
                    skill_id=c.skill_id,
                    issued_at=c.issued_at,
                    expires_at=c.expires_at,
                    verification_status=c.verification_status,
                )
            )

        memberships: dict[UUID, list[ProjectMembership]] = defaultdict(list)
        for pm, pname in self._execute(
            select(ProjectMemberModel, ProjectModel.name)
            .join(ProjectModel, ProjectModel.id == ProjectMemberModel.project_id)
            .where(ProjectMemberModel.employee_id.in_(ids))
            .order_by(ProjectModel.name)
        ).all():
            memberships[pm.employee_id].append(
                ProjectMembership(project_id=pm.project_id, project_name=pname, role=pm.role)
            )

        return {
            eid: EmployeeProfile(
                employee=emp,
                skills=tuple(skills.get(eid, [])),
                evidence=tuple(evidence.get(eid, [])),
                experiences=tuple(experiences.get(eid, [])),
                credentials=tuple(credentials.get(eid, [])),
                memberships=tuple(memberships.get(eid, [])),
            )
            for eid, emp in employees.items()
        }

# ===========================================================================
# Task Repository
# ===========================================================================

class SqlTaskRepository(SqlRepository):
    def _required_skills(self, task_ids: list[UUID]) -> dict[UUID, list[RequiredSkill]]:
        result: dict[UUID, list[RequiredSkill]] = defaultdict(list)
        if not task_ids:
            return result
        for rs, name in self._execute(
            select(TaskRequiredSkillModel, SkillModel.name)
            .join(SkillModel, SkillModel.id == TaskRequiredSkillModel.skill_id)
            .where(TaskRequiredSkillModel.task_id.in_(task_ids))
            .order_by(TaskRequiredSkillModel.is_mandatory.desc(), SkillModel.name)
        ).all():
            result[rs.task_id].append(
                RequiredSkill(
                    skill_id=rs.skill_id,
                    skill_name=name,
                    minimum_level=rs.minimum_level,
                    mandatory=rs.is_mandatory,
                    weight=float(rs.weight),
                    requires_verification=rs.requires_verification,
                    source=RequirementSource.TASK_RECORD,
                )
            )
        return result

    def _dependencies(self, task_ids: list[UUID]) -> dict[UUID, list[TaskDependencyRef]]:
        result: dict[UUID, list[TaskDependencyRef]] = defaultdict(list)
        if not task_ids:
            return result
        for dep, other in self._execute(
            select(TaskDependencyModel, TaskModel)
            .join(TaskModel, TaskModel.id == TaskDependencyModel.depends_on_task_id)
            .where(TaskDependencyModel.task_id.in_(task_ids))
            .order_by(TaskModel.task_code)
        ).all():
            result[dep.task_id].append(
                TaskDependencyRef(
                    task_id=other.id,
                    task_code=other.task_code,
                    title=other.title,
                    status=other.status,
                    dependency_type=dep.dependency_type,
                )
            )
        return result

    @staticmethod
    def _to_domain(
        m: TaskModel,
        project_name: str | None,
        skills: list[RequiredSkill],
        dependencies: list[TaskDependencyRef] | None = None,
    ) -> TaskRecord:
        return TaskRecord(
            id=m.id,
            company_id=m.company_id,
            task_code=m.task_code,
            project_id=m.project_id,
            project_name=project_name,
            sprint_id=m.sprint_id,
            title=m.title,
            description=m.description,
            complexity=m.complexity,
            priority=m.priority,
            status=m.status,
            estimated_hours=to_float(m.estimated_hours),
            remaining_estimated_hours=to_float(m.remaining_estimated_hours),
            planned_start=m.planned_start,
            deadline=m.deadline,
            task_category=m.task_category,
            restricted_to_team_id=m.restricted_to_team_id,
            requires_project_membership=m.requires_project_membership,
            acceptance_criteria=m.acceptance_criteria,
            required_skills=tuple(skills),
            required_certifications=tuple(m.required_certifications or ()),
            dependencies=tuple(dependencies or ()),
        )

    def _select(self, company_id: UUID) -> Select[TaskModel, str]:
        return (
            select(TaskModel, ProjectModel.name)
            .outerjoin(ProjectModel, ProjectModel.id == TaskModel.project_id)
            .where(TaskModel.company_id == company_id)
        )

    def _one(self, stmt: Select[TaskModel, str]) -> TaskRecord | None:
        row = self._execute(stmt).one_or_none()
        if row is None:
            return None
        task_id = row[0].id
        return self._to_domain(
            row[0],
            row[1],
            self._required_skills([task_id]).get(task_id, []),
            self._dependencies([task_id]).get(task_id, []),
        )

    def get_task(self, company_id: UUID, task_id: UUID) -> TaskRecord | None:
        return self._one(self._select(company_id).where(TaskModel.id == task_id))

    def get_task_by_code(self, company_id: UUID, task_code: str) -> TaskRecord | None:
        return self._one(self._select(company_id).where(TaskModel.task_code == task_code))

    def list_tasks(
        self,
        company_id: UUID,
        *,
        status: TaskStatus | None = None,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[TaskRecord]:
        stmt = self._select(company_id)
        if status is not None:
            stmt = stmt.where(TaskModel.status == status)
        if project_id is not None:
            stmt = stmt.where(TaskModel.project_id == project_id)
        if sprint_id is not None:
            stmt = stmt.where(TaskModel.sprint_id == sprint_id)
        total = self._execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = self._execute(stmt.order_by(TaskModel.task_code).limit(limit).offset(offset)).all()
        ids = [r[0].id for r in rows]
        skills, deps = self._required_skills(ids), self._dependencies(ids)
        return Page[TaskRecord](
            items=tuple(
                self._to_domain(r[0], r[1], skills.get(r[0].id, []), deps.get(r[0].id, [])) for r in rows
            ),
            total=total,
            limit=limit,
            offset=offset,
        )

    def create_task(self, company_id: UUID, task: NewTask) -> TaskRecord:
        model = TaskModel(
            company_id=company_id,
            task_code=task.task_code,
            project_id=task.project_id,
            sprint_id=task.sprint_id,
            title=task.title,
            description=task.description,
            acceptance_criteria=task.acceptance_criteria,
            complexity=TaskComplexity(task.complexity) if task.complexity else None,
            priority=TaskPriority(task.priority) if task.priority else None,
            status=TaskStatus.TODO,
            estimated_hours=task.estimated_hours,
            remaining_estimated_hours=task.estimated_hours,
            planned_start=task.planned_start,
            deadline=task.deadline,
            task_category=TaskCategory(task.task_category) if task.task_category else None,
            restricted_to_team_id=task.restricted_to_team_id,
            requires_project_membership=task.requires_project_membership,
            required_certifications=list(task.required_certifications),
        )
        try:
            with translate_db_errors():
                self._s.add(model)
                self._s.flush()
                for rs in task.required_skills:
                    self._s.add(
                        TaskRequiredSkillModel(
                            task_id=model.id,
                            skill_id=rs.skill_id,
                            minimum_level=rs.minimum_level,
                            is_mandatory=rs.mandatory,
                            requires_verification=rs.requires_verification,
                            weight=rs.weight,
                        )
                    )
                self._s.flush()
        except sa_exc.IntegrityError as exc:
            raise ConflictError(
                f"Task code {task.task_code!r} already exists or references invalid data"
            ) from exc
        created = self.get_task(company_id, model.id)
        assert created is not None
        return created

    def get_skill_catalog(self, company_id: UUID) -> list[Skill]:
        return [
            Skill(id=m.id, name=m.name, category=m.category, aliases=tuple(m.aliases or ()))
            for m in self._execute(
                select(SkillModel).where(SkillModel.company_id == company_id).order_by(SkillModel.name)
            ).scalars()
        ]

    def get_sprint(self, company_id: UUID, sprint_id: UUID) -> Sprint | None:
        m = self._execute(
            select(SprintModel)
            .join(ProjectModel, ProjectModel.id == SprintModel.project_id)
            .where(SprintModel.id == sprint_id, ProjectModel.company_id == company_id)
        ).scalar_one_or_none()
        if m is None:
            return None
        return Sprint(
            id=m.id,
            project_id=m.project_id,
            name=m.name,
            start_date=m.start_date,
            end_date=m.end_date,
            status=m.status.value,
        )

    def project_exists(self, company_id: UUID, project_id: UUID) -> bool:
        return (
            self._execute(
                select(func.count()).where(
                    ProjectModel.id == project_id, ProjectModel.company_id == company_id
                )
            ).scalar_one()
            > 0
        )

# ===========================================================================
# Workforce Repository
# ===========================================================================

_ACTIVE = (AssignmentStatus.PLANNED, AssignmentStatus.ACTIVE)


class SqlWorkforceRepository(SqlRepository):
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
        emps = self._execute(
            select(EmployeeModel.id, EmployeeModel.timezone, EmployeeModel.employment_status).where(
                EmployeeModel.company_id == company_id, EmployeeModel.id.in_(employee_ids)
            )
        ).all()
        ids = [e.id for e in emps]
        if not ids:
            return {}

        schedules: dict[UUID, list[ScheduleDay]] = defaultdict(list)
        for s in self._execute(
            select(WorkScheduleDayModel)
            .where(WorkScheduleDayModel.employee_id.in_(ids))
            .order_by(WorkScheduleDayModel.weekday)
        ).scalars():
            schedules[s.employee_id].append(
                ScheduleDay(
                    weekday=s.weekday,
                    start_time=s.start_time,
                    end_time=s.end_time,
                    break_minutes=s.break_minutes,
                )
            )

        attendance: dict[UUID, list[AttendanceRecord]] = defaultdict(list)
        for a in self._execute(
            select(AttendanceRecordModel)
            .where(
                AttendanceRecordModel.employee_id.in_(ids),
                AttendanceRecordModel.work_date >= attendance_since.date(),
            )
            .order_by(AttendanceRecordModel.work_date)
        ).scalars():
            attendance[a.employee_id].append(
                AttendanceRecord(
                    work_date=a.work_date,
                    check_in=a.check_in,
                    check_out=a.check_out,
                    status=a.attendance_status,
                )
            )

        # Leaves from attendance_since onward: needed for the window and for prorating assignment plans.
        leaves: dict[UUID, list[LeaveRecord]] = defaultdict(list)
        for lv in self._execute(
            select(LeaveRecordModel)
            .where(
                LeaveRecordModel.employee_id.in_(ids),
                LeaveRecordModel.end_at > attendance_since,
                LeaveRecordModel.status.in_((LeaveStatus.APPROVED, LeaveStatus.REQUESTED)),
            )
            .order_by(LeaveRecordModel.start_at)
        ).scalars():
            leaves[lv.employee_id].append(
                LeaveRecord(
                    id=lv.id,
                    leave_type=lv.leave_type,
                    start_at=lv.start_at,
                    end_at=lv.end_at,
                    status=lv.status,
                )
            )

        assignee_count = (
            select(TaskAssignmentModel.task_id, func.count().label("n"))
            .where(TaskAssignmentModel.assignment_status.in_(_ACTIVE))
            .group_by(TaskAssignmentModel.task_id)
            .subquery()
        )
        logged = (
            select(
                TimesheetEntryModel.task_id,
                TimesheetEntryModel.employee_id,
                func.sum(TimesheetEntryModel.hours).label("hours"),
            )
            .group_by(TimesheetEntryModel.task_id, TimesheetEntryModel.employee_id)
            .subquery()
        )
        assignments: dict[UUID, list[Assignment]] = defaultdict(list)
        for ta, task, n, hours in self._execute(
            select(TaskAssignmentModel, TaskModel, assignee_count.c.n, logged.c.hours)
            .join(TaskModel, TaskModel.id == TaskAssignmentModel.task_id)
            .outerjoin(assignee_count, assignee_count.c.task_id == TaskAssignmentModel.task_id)
            .outerjoin(
                logged,
                (logged.c.task_id == TaskAssignmentModel.task_id)
                & (logged.c.employee_id == TaskAssignmentModel.employee_id),
            )
            .where(
                TaskAssignmentModel.employee_id.in_(ids), TaskAssignmentModel.assignment_status.in_(_ACTIVE)
            )
            .order_by(TaskModel.task_code)
        ).all():
            assignments[ta.employee_id].append(
                Assignment(
                    id=ta.id,
                    task_id=task.id,
                    task_code=task.task_code,
                    task_title=task.title,
                    project_id=task.project_id,
                    assignment_status=ta.assignment_status,
                    planned_start=ta.planned_start,
                    planned_end=ta.planned_end,
                    allocated_hours=to_float(ta.allocated_hours),
                    remaining_estimated_hours=to_float(ta.remaining_estimated_hours),
                    task_remaining_estimated_hours=to_float(task.remaining_estimated_hours),
                    active_assignee_count=int(n or 1),
                    logged_hours=float(hours or 0),
                )
            )

        commitments: dict[UUID, list[Commitment]] = defaultdict(list)
        for c in self._execute(
            select(EmployeeCommitmentModel).where(
                EmployeeCommitmentModel.employee_id.in_(ids),
                EmployeeCommitmentModel.end_at > window_start,
                EmployeeCommitmentModel.start_at < window_end,
            )
        ).scalars():
            commitments[c.employee_id].append(
                Commitment(id=c.id, title=c.title, start_at=c.start_at, end_at=c.end_at, hours=float(c.hours))
            )

        return {
            e.id: EmployeeWorkforceData(
                employee_id=e.id,
                timezone=e.timezone,
                employment_status=e.employment_status,
                schedule=tuple(schedules.get(e.id, [])),
                attendance=tuple(attendance.get(e.id, [])),
                leaves=tuple(leaves.get(e.id, [])),
                assignments=tuple(assignments.get(e.id, [])),
                commitments=tuple(commitments.get(e.id, [])),
            )
            for e in emps
        }

    def get_holidays(self, company_id: UUID, start: date, end: date) -> list[Holiday]:
        return [
            Holiday(holiday_date=h.holiday_date, name=h.name)
            for h in self._execute(
                select(CalendarHolidayModel)
                .where(
                    CalendarHolidayModel.company_id == company_id,
                    CalendarHolidayModel.holiday_date >= start,
                    CalendarHolidayModel.holiday_date <= end,
                )
                .order_by(CalendarHolidayModel.holiday_date)
            ).scalars()
        ]

# ===========================================================================
# Performance Repository
# ===========================================================================

class SqlPerformanceRepository(SqlRepository):
    def get_outcomes(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, list[TaskOutcomeRecord]]:
        if not employee_ids:
            return {}
        rows = self._execute(
            select(TaskOutcomeModel, TaskModel)
            .join(TaskModel, TaskModel.id == TaskOutcomeModel.task_id)
            .where(TaskModel.company_id == company_id, TaskOutcomeModel.employee_id.in_(employee_ids))
            .order_by(TaskOutcomeModel.reviewed_at, TaskModel.task_code)
        ).all()
        task_ids = list({t.id for _, t in rows})
        skills: dict[UUID, list[UUID]] = defaultdict(list)
        if task_ids:
            for task_id, skill_id in self._execute(
                select(TaskRequiredSkillModel.task_id, TaskRequiredSkillModel.skill_id).where(
                    TaskRequiredSkillModel.task_id.in_(task_ids)
                )
            ).all():
                skills[task_id].append(skill_id)
        result: dict[UUID, list[TaskOutcomeRecord]] = defaultdict(list)
        for o, t in rows:
            result[o.employee_id].append(
                TaskOutcomeRecord(
                    task_id=t.id,
                    task_code=t.task_code,
                    project_id=t.project_id,
                    task_category=t.task_category,
                    complexity=t.complexity,
                    required_skill_ids=tuple(sorted(skills.get(t.id, []))),
                    quality_score=to_float(o.quality_score),
                    accepted=o.accepted,
                    rework_count=o.rework_count,
                    on_time=o.on_time,
                    delay_reason=o.delay_reason,
                    blocker_hours=float(o.blocker_hours or 0),
                    reviewed_at=o.reviewed_at,
                )
            )
        return dict(result)

    def get_project_history(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, dict[UUID, int]]:
        if not employee_ids:
            return {}
        result: dict[UUID, dict[UUID, int]] = defaultdict(dict)
        for emp_id, project_id, n in self._execute(
            select(TaskAssignmentModel.employee_id, TaskModel.project_id, func.count())
            .join(TaskModel, TaskModel.id == TaskAssignmentModel.task_id)
            .where(
                TaskModel.company_id == company_id,
                TaskModel.project_id.is_not(None),
                TaskAssignmentModel.employee_id.in_(employee_ids),
                TaskAssignmentModel.assignment_status == AssignmentStatus.COMPLETED,
            )
            .group_by(TaskAssignmentModel.employee_id, TaskModel.project_id)
        ).all():
            result[emp_id][project_id] = int(n)
        return dict(result)

    def record_outcome(self, company_id: UUID, outcome: NewTaskOutcome) -> UUID:
        task_status = self._execute(
            select(TaskModel.status).where(
                TaskModel.id == outcome.task_id, TaskModel.company_id == company_id
            )
        ).scalar_one_or_none()
        emp_ok = self._execute(
            select(func.count()).where(
                EmployeeModel.id == outcome.employee_id, EmployeeModel.company_id == company_id
            )
        ).scalar_one()
        if task_status is None or not emp_ok:
            raise NotFoundError("Task or employee not found")
        # Outcomes are task evidence: only for completed tasks, only for employees who worked on them.
        if task_status != TaskStatus.DONE:
            raise InputValidationError("Outcomes can only be recorded for DONE tasks")
        assigned = self._execute(
            select(func.count()).where(
                TaskAssignmentModel.task_id == outcome.task_id,
                TaskAssignmentModel.employee_id == outcome.employee_id,
            )
        ).scalar_one()
        if not assigned:
            raise InputValidationError("The employee was not assigned to this task")
        model = TaskOutcomeModel(**outcome.model_dump())
        try:
            with translate_db_errors():
                self._s.add(model)
                self._s.flush()
        except sa_exc.IntegrityError as exc:
            raise ConflictError("An outcome for this task and employee already exists") from exc
        return model.id

    def evaluation_summary(self, company_id: UUID) -> EvaluationSummary:
        by_status = {
            str(s.value): int(n)
            for s, n in self._execute(
                select(RecommendationRunModel.status, func.count())
                .where(RecommendationRunModel.company_id == company_id)
                .group_by(RecommendationRunModel.status)
            ).all()
        }
        avg_cov = self._execute(
            select(func.avg(RecommendationRunModel.data_coverage)).where(
                RecommendationRunModel.company_id == company_id,
                RecommendationRunModel.primary_employee_id.is_not(None),
            )
        ).scalar_one()
        # Latest recommendation per task, to compare against who actually delivered it.
        latest = (
            select(
                RecommendationRunModel.task_id,
                RecommendationRunModel.primary_employee_id,
                func.row_number()
                .over(
                    partition_by=RecommendationRunModel.task_id,
                    order_by=RecommendationRunModel.generated_at.desc(),
                )
                .label("rn"),
            )
            .where(
                RecommendationRunModel.company_id == company_id, RecommendationRunModel.task_id.is_not(None)
            )
            .subquery()
        )
        rows = self._execute(
            select(TaskOutcomeModel.employee_id, TaskOutcomeModel.quality_score, latest.c.primary_employee_id)
            .join(TaskModel, TaskModel.id == TaskOutcomeModel.task_id)
            .join(latest, (latest.c.task_id == TaskOutcomeModel.task_id) & (latest.c.rn == 1))
            .where(TaskModel.company_id == company_id)
        ).all()
        matched = [float(q) for emp, q, primary in rows if emp == primary and q is not None]
        others = [float(q) for emp, q, primary in rows if emp != primary and q is not None]
        total_outcomes = self._execute(
            select(func.count())
            .select_from(TaskOutcomeModel)
            .join(TaskModel, TaskModel.id == TaskOutcomeModel.task_id)
            .where(TaskModel.company_id == company_id)
        ).scalar_one()
        return EvaluationSummary(
            recommendations_by_status=by_status,
            average_primary_coverage=round(float(avg_cov), 4) if avg_cov is not None else None,
            outcomes_recorded=int(total_outcomes),
            outcomes_for_recommended_primary=len([1 for emp, _, primary in rows if emp == primary]),
            average_quality_recommended_primary=round(sum(matched) / len(matched), 2) if matched else None,
            average_quality_other_assignees=round(sum(others) / len(others), 2) if others else None,
        )

    def get_evaluations(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 12
    ) -> list[PerformanceEvaluation]:
        rows = self._execute(
            select(PerformanceEvaluationModel)
            .where(
                PerformanceEvaluationModel.company_id == company_id,
                PerformanceEvaluationModel.employee_id == employee_id,
            )
            .order_by(PerformanceEvaluationModel.period_end.desc())
            .limit(limit)
        ).scalars()
        return [
            PerformanceEvaluation(
                period_start=m.period_start,
                period_end=m.period_end,
                technical_quality=to_float(m.technical_quality),
                delivery=to_float(m.delivery),
                collaboration=to_float(m.collaboration),
                summary=m.summary,
            )
            for m in rows
        ]

    def get_metric_snapshots(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 60
    ) -> list[PerformanceMetricSnapshot]:
        rows = self._execute(
            select(PerformanceMetricModel)
            .where(
                PerformanceMetricModel.company_id == company_id,
                PerformanceMetricModel.employee_id == employee_id,
            )
            .order_by(PerformanceMetricModel.period_end.desc(), PerformanceMetricModel.metric)
            .limit(limit)
        ).scalars()
        return [
            PerformanceMetricSnapshot(
                metric=m.metric,
                period_start=m.period_start,
                period_end=m.period_end,
                value=to_float(m.value),
                sample_size=m.sample_size,
            )
            for m in rows
        ]

# ===========================================================================
# Recommendation Repository
# ===========================================================================

class SqlRecommendationRepository(SqlRepository):
    _RUN_FIELDS = (
        "task_id",
        "request_id",
        "status",
        "requested_by",
        "as_of",
        "window_start",
        "window_end",
        "task_analysis",
        "algorithm_version",
        "scoring_config_fingerprint",
        "prompt_version",
        "llm_provider",
        "llm_model",
        "primary_employee_id",
        "data_coverage",
        "explanation",
        "explanation_source",
        "revalidated_from_id",
        "result_snapshot",
        "execution_time_ms",
        "error_details",
        "thread_id",
        "idempotency_key",
        "agent_mode",
        "research_summary",
        "llm_calls",
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "tool_calls_count",
        "error_category",
        "resumable",
        "retry_of_id",
    )

    def save_run(self, run: RecommendationRunRecord, request_payload: dict[str, Any]) -> None:
        values: dict[str, Any] = {f: getattr(run, f) for f in self._RUN_FIELDS}
        values["warnings"] = list(run.warnings)
        with translate_db_errors():
            existing = self._s.get(RecommendationRunModel, run.id)
            if existing is not None:
                if existing.company_id != run.company_id:
                    raise ConflictError("Recommendation id belongs to another company")
                # A resumed/retried run replaces its earlier (failed) record and children atomically.
                for model in (
                    RecommendationCandidateModel,
                    AgentExecutionLogModel,
                    AgentToolExecutionModel,
                    AgentRunErrorModel,
                ):
                    self._s.execute(delete(model).where(model.recommendation_id == run.id))
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.request_payload = request_payload
            else:
                self._s.add(
                    RecommendationRunModel(
                        id=run.id, company_id=run.company_id, request_payload=request_payload, **values
                    )
                )
            self._s.flush()
            self._save_children(run)
            self._s.flush()

    def _save_children(self, run: RecommendationRunRecord) -> None:
        for cand in run.candidates:
            cm = RecommendationCandidateModel(
                recommendation_id=run.id,
                employee_id=cand.employee_id,
                rank=cand.rank,
                suitability_score=cand.suitability_score,
                eligible=cand.eligible,
                confidence=cand.confidence,
                data_coverage=cand.data_coverage,
                availability_status=cand.availability_status,
                available_capacity_hours=cand.available_capacity_hours,
                exclusion_reasons=[r.code.value for r in cand.exclusion_reasons],
            )
            self._s.add(cm)
            self._s.flush()
            for c in cand.criteria:
                self._s.add(
                    RecommendationScoreComponentModel(
                        candidate_id=cm.id,
                        criterion=c.criterion,
                        raw_score=c.raw_score,
                        score_used=c.score_used,
                        weight=c.weight,
                        weighted_score=round(c.weighted_score, 2),
                        has_evidence=c.has_evidence,
                        evidence_reference={"evidence": list(c.evidence), "details": c.details},
                    )
                )
        for seq, t in enumerate(run.trace, start=1):
            self._s.add(
                AgentExecutionLogModel(
                    recommendation_id=run.id,
                    sequence=seq,
                    node_name=t.node_name,
                    execution_status=t.execution_status,
                    duration_ms=t.duration_ms,
                    attempts=t.attempts,
                    sanitized_input_summary=t.input_summary,
                    sanitized_output_summary=t.output_summary,
                    error_message=t.error_message,
                    started_at=t.started_at,
                    finished_at=t.finished_at,
                    llm_prompt_tokens=t.llm_prompt_tokens,
                    llm_completion_tokens=t.llm_completion_tokens,
                )
            )
        for te in run.tool_executions:
            self._s.add(
                AgentToolExecutionModel(
                    recommendation_id=run.id,
                    sequence=te.sequence,
                    research_round=te.research_round,
                    tool_call_id=te.tool_call_id[:80],
                    tool_name=te.tool_name[:80],
                    status=te.status,
                    arguments_summary=te.arguments_summary,
                    result_summary=te.result_summary,
                    error_code=te.error_code,
                    duration_ms=te.duration_ms,
                )
            )
        for err in run.errors:
            self._s.add(
                AgentRunErrorModel(
                    recommendation_id=run.id,
                    node_name=err.node_name,
                    code=err.code[:60],
                    category=err.category,
                    message=err.message,
                    retryable=err.retryable,
                )
            )

    @staticmethod
    def _stored(m: RecommendationRunModel) -> StoredRecommendation:
        return StoredRecommendation(
            id=m.id,
            company_id=m.company_id,
            status=m.status,
            task_id=m.task_id,
            generated_at=m.generated_at,
            result_snapshot=m.result_snapshot,
            request_payload=m.request_payload,
            request_id=m.request_id,
            requested_by=m.requested_by,
            updated_at=m.updated_at,
            execution_time_ms=m.execution_time_ms,
            thread_id=m.thread_id,
            idempotency_key=m.idempotency_key,
            agent_mode=m.agent_mode,
            llm_provider=m.llm_provider,
            llm_model=m.llm_model,
            llm_calls=m.llm_calls,
            llm_prompt_tokens=m.llm_prompt_tokens,
            llm_completion_tokens=m.llm_completion_tokens,
            tool_calls_count=m.tool_calls_count,
            error_category=m.error_category,
            resumable=m.resumable,
            retry_of_id=m.retry_of_id,
        )

    def get_run(self, company_id: UUID, recommendation_id: UUID) -> StoredRecommendation | None:
        m = self._execute(
            select(RecommendationRunModel).where(
                RecommendationRunModel.id == recommendation_id,
                RecommendationRunModel.company_id == company_id,
            )
        ).scalar_one_or_none()
        return self._stored(m) if m else None

    def find_by_idempotency_key(self, company_id: UUID, key: str) -> StoredRecommendation | None:
        m = self._execute(
            select(RecommendationRunModel).where(
                RecommendationRunModel.company_id == company_id,
                RecommendationRunModel.idempotency_key == key,
            )
        ).scalar_one_or_none()
        return self._stored(m) if m else None

    @staticmethod
    def _owned(company_id: UUID, recommendation_id: UUID) -> Select[UUID]:
        return select(RecommendationRunModel.id).where(
            RecommendationRunModel.id == recommendation_id, RecommendationRunModel.company_id == company_id
        )

    def list_node_logs(self, company_id: UUID, recommendation_id: UUID) -> list[ExecutionTraceEntry]:
        rows = self._execute(
            select(AgentExecutionLogModel)
            .where(AgentExecutionLogModel.recommendation_id.in_(self._owned(company_id, recommendation_id)))
            .order_by(AgentExecutionLogModel.sequence)
        ).scalars()
        return [
            ExecutionTraceEntry(
                node_name=m.node_name,
                execution_status=m.execution_status,
                duration_ms=m.duration_ms,
                attempts=m.attempts,
                input_summary=m.sanitized_input_summary,
                output_summary=m.sanitized_output_summary,
                error_message=m.error_message,
                started_at=m.started_at,
                finished_at=m.finished_at,
                llm_prompt_tokens=m.llm_prompt_tokens,
                llm_completion_tokens=m.llm_completion_tokens,
            )
            for m in rows
        ]

    def list_tool_executions(self, company_id: UUID, recommendation_id: UUID) -> list[ToolExecutionRecord]:
        rows = self._execute(
            select(AgentToolExecutionModel)
            .where(AgentToolExecutionModel.recommendation_id.in_(self._owned(company_id, recommendation_id)))
            .order_by(AgentToolExecutionModel.sequence)
        ).scalars()
        return [
            ToolExecutionRecord(
                sequence=m.sequence,
                research_round=m.research_round,
                tool_call_id=m.tool_call_id,
                tool_name=m.tool_name,
                status=m.status,
                arguments_summary=m.arguments_summary,
                result_summary=m.result_summary,
                error_code=m.error_code,
                duration_ms=m.duration_ms,
            )
            for m in rows
        ]

    def list_run_errors(self, company_id: UUID, recommendation_id: UUID) -> list[RunErrorRecord]:
        rows = self._execute(
            select(AgentRunErrorModel)
            .where(AgentRunErrorModel.recommendation_id.in_(self._owned(company_id, recommendation_id)))
            .order_by(AgentRunErrorModel.created_at, AgentRunErrorModel.code)
        ).scalars()
        return [
            RunErrorRecord(
                node_name=m.node_name,
                code=m.code,
                category=m.category,
                message=m.message,
                retryable=m.retryable,
            )
            for m in rows
        ]

    def list_run_ids_before(self, cutoff: datetime) -> list[UUID]:
        return list(
            self._execute(
                select(RecommendationRunModel.id).where(RecommendationRunModel.updated_at < cutoff)
            ).scalars()
        )

    def save_task_analysis(
        self, company_id: UUID, task_id: UUID | None, analysis: dict[str, Any], analyzer_version: str
    ) -> UUID:
        m = TaskAnalysisModel(
            company_id=company_id, task_id=task_id, analysis=analysis, analyzer_version=analyzer_version
        )
        with translate_db_errors():
            self._s.add(m)
            self._s.flush()
        return m.id


class SqlAuditRepository(SqlRepository):
    def record(
        self,
        company_id: UUID | None,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with translate_db_errors():
            self._s.add(
                AuditEventModel(
                    company_id=company_id,
                    actor=actor,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    event_metadata=metadata or {},
                )
            )
            self._s.flush()

# ===========================================================================
# SQLAlchemy Unit of Work
# ===========================================================================

class SqlAlchemyUnitOfWork:
    """PostgreSQL implementation of ``app.domain.interfaces.UnitOfWork``. Rolls back unless committed.

    ``read_only`` opens the transaction with ``SET TRANSACTION READ ONLY`` (PostgreSQL rejects any write), and
    ``statement_timeout_ms`` bounds every statement server-side. Agent tools always use both.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        read_only: bool = False,
        statement_timeout_ms: int | None = None,
    ) -> None:
        self._factory = session_factory
        self.read_only = read_only
        self._statement_timeout_ms = statement_timeout_ms

    companies: CompanyRepository
    employees: EmployeeRepository
    tasks: TaskRepository
    workforce: WorkforceRepository
    performance: PerformanceRepository
    recommendations: RecommendationRepository
    audit: AuditRepository

    def __enter__(self) -> Self:
        self.session = self._factory()
        self.companies = SqlCompanyRepository(self.session)
        self.employees = SqlEmployeeRepository(self.session)
        self.tasks = SqlTaskRepository(self.session)
        self.workforce = SqlWorkforceRepository(self.session)
        self.performance = SqlPerformanceRepository(self.session)
        self.recommendations = SqlRecommendationRepository(self.session)
        self.audit = SqlAuditRepository(self.session)
        if self.read_only or self._statement_timeout_ms:
            try:
                with translate_db_errors():
                    if self.read_only:
                        self.session.execute(text("SET TRANSACTION READ ONLY"))
                    if self._statement_timeout_ms:
                        ms = int(self._statement_timeout_ms)
                        self.session.execute(text(f"SET LOCAL statement_timeout = {ms}"))
            except Exception:
                self.session.close()
                raise
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            self.session.rollback()  # no-op after a successful commit
        except Exception:  # noqa: S110 - closing must never mask the original error
            pass
        finally:
            self.session.close()

    def commit(self) -> None:
        with translate_db_errors():
            self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def ping(self) -> None:
        with translate_db_errors():
            self.session.execute(text("SELECT 1"))


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __call__(
        self, *, read_only: bool = False, statement_timeout_ms: int | None = None
    ) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            self._session_factory, read_only=read_only, statement_timeout_ms=statement_timeout_ms
        )

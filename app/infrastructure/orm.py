"""SQLAlchemy engine/session factories and relational ORM models."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Engine,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    create_engine,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core import Settings
from app.domain import (
    AgentMode,
    AssignmentStatus,
    AttendanceStatus,
    AvailabilityStatus,
    CredentialType,
    Criterion,
    DelayReason,
    DependencyType,
    EmploymentStatus,
    ErrorCategory,
    EvidenceType,
    LeaveStatus,
    LeaveType,
    ProjectStatus,
    RecommendationStatus,
    SkillVerificationStatus,
    SprintStatus,
    TaskCategory,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
    ToolExecutionStatus,
)

# ===========================================================================
# Engine & Session Factory
# ===========================================================================

def create_db_engine(settings: Settings, *, url: str | None = None) -> Engine:
    return create_engine(
        url or settings.database_url.get_secret_value(),
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# ===========================================================================
# ORM Base
# ===========================================================================

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def enum_column(enum_cls: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint storing enum *values* (portable, migration-friendly)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

# ===========================================================================
# Organization Models
# ===========================================================================

class CompanyModel(TimestampMixin, Base):
    """Tenant boundary. Every top-level entity references a company."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    working_weekdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))

    __table_args__ = (
        CheckConstraint("cardinality(working_weekdays) > 0", name="working_weekdays_not_empty"),
    )


class DepartmentModel(Base):
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))

    __table_args__ = (UniqueConstraint("company_id", "name"),)


class TeamModel(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = uuid_pk()
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))

    __table_args__ = (UniqueConstraint("department_id", "name"),)


class CalendarHolidayModel(Base):
    """Company-wide non-working days."""

    __tablename__ = "calendar_holidays"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    holiday_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(120))

    __table_args__ = (UniqueConstraint("company_id", "holiday_date"),)

# ===========================================================================
# Employee & Skill Models
# ===========================================================================

class EmployeeModel(TimestampMixin, Base):
    """Professional profile only. No protected/sensitive personal attributes are stored by design.

    Weekly capacity is *derived* from ``work_schedule_days`` (single source of truth), not stored here.
    """

    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    employee_code: Mapped[str] = mapped_column(String(32))
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    job_title: Mapped[str] = mapped_column(String(120))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), index=True)
    hire_date: Mapped[date | None] = mapped_column(Date)
    experience_years: Mapped[float | None] = mapped_column(Numeric(4, 1))
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        enum_column(EmploymentStatus, "employment_status")
    )
    timezone: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("company_id", "employee_code"),
        UniqueConstraint("company_id", "email"),
        CheckConstraint(
            "experience_years IS NULL OR experience_years >= 0", name="experience_years_non_negative"
        ),
        Index("ix_employees_company_status", "company_id", "employment_status"),
        Index("ix_employees_department", "department_id"),
    )


class SkillModel(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")

    __table_args__ = (UniqueConstraint("company_id", "name"),)


class EmployeeSkillModel(Base):
    __tablename__ = "employee_skills"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    proficiency_level: Mapped[int] = mapped_column(SmallInteger)
    verification_status: Mapped[SkillVerificationStatus] = mapped_column(
        enum_column(SkillVerificationStatus, "skill_verification_status")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("employee_id", "skill_id"),
        CheckConstraint("proficiency_level BETWEEN 0 AND 100", name="proficiency_range"),
        CheckConstraint(
            "verification_status <> 'VERIFIED' OR verified_at IS NOT NULL", name="verified_requires_timestamp"
        ),
    )


class EmployeeSkillEvidenceModel(Base):
    __tablename__ = "employee_skill_evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee_skills.id", ondelete="CASCADE"), index=True
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(enum_column(EvidenceType, "evidence_type"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    verification_status: Mapped[SkillVerificationStatus] = mapped_column(
        enum_column(SkillVerificationStatus, "evidence_verification_status")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeeExperienceModel(Base):
    __tablename__ = "employee_experiences"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    organization_or_project: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    relevant_start_date: Mapped[date] = mapped_column(Date)
    relevant_end_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint(
            "relevant_end_date IS NULL OR relevant_end_date >= relevant_start_date", name="experience_dates"
        ),
    )


class EmployeeExperienceSkillModel(Base):
    """Normalized 'technologies' of an experience record."""

    __tablename__ = "employee_experience_skills"

    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee_experiences.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)


class EmployeeCredentialModel(Base):
    """Certifications and trainings."""

    __tablename__ = "employee_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str | None] = mapped_column(String(200))
    credential_type: Mapped[CredentialType] = mapped_column(enum_column(CredentialType, "credential_type"))
    skill_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"))
    issued_at: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[date | None] = mapped_column(Date)
    verification_status: Mapped[SkillVerificationStatus] = mapped_column(
        enum_column(SkillVerificationStatus, "credential_verification_status")
    )

    __table_args__ = (
        CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at > issued_at", name="credential_dates"
        ),
    )

# ===========================================================================
# Project & Task Models
# ===========================================================================

class ProjectModel(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProjectStatus] = mapped_column(enum_column(ProjectStatus, "project_status"))
    project_manager_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL")
    )

    __table_args__ = (UniqueConstraint("company_id", "code"),)


class ProjectMemberModel(Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(60))


class SprintModel(Base):
    __tablename__ = "sprints"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    goal: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[SprintStatus] = mapped_column(enum_column(SprintStatus, "sprint_status"))

    __table_args__ = (
        UniqueConstraint("project_id", "name"),
        CheckConstraint("end_date >= start_date", name="sprint_dates"),
    )


class TaskModel(TimestampMixin, Base):
    """A Scrum task.

    ``remaining_estimated_hours`` is the task-level aggregate. Per-employee remaining effort lives on
    ``task_assignments`` and is authoritative for workload (see docs/SCORING.md, workload rules).
    """

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    task_code: Mapped[str] = mapped_column(String(32))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sprints.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    complexity: Mapped[TaskComplexity | None] = mapped_column(enum_column(TaskComplexity, "task_complexity"))
    priority: Mapped[TaskPriority | None] = mapped_column(enum_column(TaskPriority, "task_priority"))
    status: Mapped[TaskStatus] = mapped_column(enum_column(TaskStatus, "task_status"))
    estimated_hours: Mapped[float | None] = mapped_column(Numeric(7, 2))
    remaining_estimated_hours: Mapped[float | None] = mapped_column(Numeric(7, 2))
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_category: Mapped[TaskCategory | None] = mapped_column(
        enum_column(TaskCategory, "task_category"), nullable=True
    )
    restricted_to_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL")
    )
    requires_project_membership: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Hard constraint: names of certifications an assignee must hold (valid, not rejected) - see eligibility.
    required_certifications: Mapped[list[str]] = mapped_column(ARRAY(String(120)), server_default="{}")

    __table_args__ = (
        UniqueConstraint("company_id", "task_code"),
        CheckConstraint("estimated_hours IS NULL OR estimated_hours > 0", name="estimated_hours_positive"),
        CheckConstraint(
            "remaining_estimated_hours IS NULL OR remaining_estimated_hours >= 0",
            name="remaining_non_negative",
        ),
        CheckConstraint(
            "deadline IS NULL OR planned_start IS NULL OR deadline > planned_start", name="task_dates"
        ),
        Index("ix_tasks_company_status", "company_id", "status"),
        Index("ix_tasks_project", "project_id"),
    )


class TaskRequiredSkillModel(Base):
    __tablename__ = "task_required_skills"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    minimum_level: Mapped[int] = mapped_column(SmallInteger)
    is_mandatory: Mapped[bool] = mapped_column(Boolean)
    requires_verification: Mapped[bool] = mapped_column(Boolean, server_default=false())
    weight: Mapped[float] = mapped_column(Numeric(4, 2), server_default="1.0")

    __table_args__ = (
        UniqueConstraint("task_id", "skill_id"),
        CheckConstraint("minimum_level BETWEEN 0 AND 100", name="minimum_level_range"),
        CheckConstraint("weight > 0", name="weight_positive"),
    )


class TaskDependencyModel(Base):
    """``task_id`` depends on ``depends_on_task_id`` (informational for recommendations, never a score input)."""

    __tablename__ = "task_dependencies"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    dependency_type: Mapped[DependencyType] = mapped_column(enum_column(DependencyType, "dependency_type"))

    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id"),
        CheckConstraint("task_id <> depends_on_task_id", name="no_self_dependency"),
    )

# ===========================================================================
# Workforce & Schedule Models
# ===========================================================================

class WorkScheduleDayModel(Base):
    """Recurring weekly schedule. Times are interpreted in the employee's timezone (employees.timezone)."""

    __tablename__ = "work_schedule_days"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(SmallInteger)  # Monday=0 ... Sunday=6
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    break_minutes: Mapped[int] = mapped_column(SmallInteger, server_default="0")

    __table_args__ = (
        UniqueConstraint("employee_id", "weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("break_minutes BETWEEN 0 AND 240", name="break_range"),
        CheckConstraint("start_time <> end_time", name="non_empty_shift"),
    )


class AttendanceRecordModel(Base):
    __tablename__ = "attendance_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    work_date: Mapped[date] = mapped_column(Date)
    check_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attendance_status: Mapped[AttendanceStatus] = mapped_column(
        enum_column(AttendanceStatus, "attendance_status")
    )

    __table_args__ = (
        UniqueConstraint("employee_id", "work_date"),
        CheckConstraint("check_out IS NULL OR check_in IS NOT NULL", name="checkout_requires_checkin"),
        CheckConstraint("check_out IS NULL OR check_out > check_in", name="checkout_after_checkin"),
        CheckConstraint(
            "attendance_status NOT IN ('PRESENT','REMOTE') OR check_in IS NOT NULL",
            name="present_requires_checkin",
        ),
    )


class LeaveRecordModel(Base):
    __tablename__ = "leave_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    leave_type: Mapped[LeaveType] = mapped_column(enum_column(LeaveType, "leave_type"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[LeaveStatus] = mapped_column(enum_column(LeaveStatus, "leave_status"))

    __table_args__ = (
        CheckConstraint("end_at > start_at", name="leave_dates"),
        Index("ix_leave_records_employee_period", "employee_id", "start_at", "end_at"),
    )


class TaskAssignmentModel(Base):
    """Employee-level allocation of a task. ``remaining_estimated_hours`` here is per employee."""

    __tablename__ = "task_assignments"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    assignment_status: Mapped[AssignmentStatus] = mapped_column(
        enum_column(AssignmentStatus, "assignment_status")
    )
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocated_hours: Mapped[float | None] = mapped_column(Numeric(7, 2))
    remaining_estimated_hours: Mapped[float | None] = mapped_column(Numeric(7, 2))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("task_id", "employee_id"),
        CheckConstraint("allocated_hours IS NULL OR allocated_hours >= 0", name="allocated_non_negative"),
        CheckConstraint(
            "remaining_estimated_hours IS NULL OR remaining_estimated_hours >= 0",
            name="remaining_non_negative",
        ),
        CheckConstraint(
            "planned_end IS NULL OR planned_start IS NULL OR planned_end > planned_start",
            name="assignment_dates",
        ),
        Index("ix_task_assignments_employee_status", "employee_id", "assignment_status"),
    )


class TimesheetEntryModel(Base):
    """Historical time consumption. Never interpreted as remaining work."""

    __tablename__ = "timesheet_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    entry_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[float] = mapped_column(Numeric(5, 2))
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("hours > 0 AND hours <= 24", name="hours_range"),
        Index("ix_timesheet_entries_employee_task", "employee_id", "task_id"),
    )


class EmployeeCommitmentModel(Base):
    """Other known commitments that consume capacity (support rotation, release duty, training)."""

    __tablename__ = "employee_commitments"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hours: Mapped[float] = mapped_column(Numeric(6, 2))

    __table_args__ = (
        CheckConstraint("end_at > start_at", name="commitment_dates"),
        CheckConstraint("hours >= 0", name="commitment_hours_non_negative"),
        Index("ix_employee_commitments_employee_period", "employee_id", "start_at", "end_at"),
    )

# ===========================================================================
# Performance Models
# ===========================================================================

class TaskOutcomeModel(Base):
    """Reviewed, task-related evidence of performance. The only source for quality/reliability scores."""

    __tablename__ = "task_outcomes"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    quality_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    rework_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    on_time: Mapped[bool | None] = mapped_column(Boolean)
    delay_reason: Mapped[DelayReason | None] = mapped_column(
        enum_column(DelayReason, "delay_reason"), nullable=True
    )
    blocker_hours: Mapped[float] = mapped_column(Numeric(6, 2), server_default="0")
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("task_id", "employee_id"),
        CheckConstraint("quality_score IS NULL OR quality_score BETWEEN 0 AND 100", name="quality_range"),
        CheckConstraint("rework_count >= 0", name="rework_non_negative"),
        CheckConstraint("blocker_hours >= 0", name="blocker_non_negative"),
        CheckConstraint("reviewer_id IS NULL OR reviewer_id <> employee_id", name="no_self_review"),
    )


class PerformanceEvaluationModel(Base):
    """Periodic, work-related technical review of an employee (e.g. per sprint), written by a reviewer.

    Informational context for the Employee 360 view and the research agent. Not a scoring input: scores use
    reviewed ``task_outcomes`` only (task-specific evidence). Contains no HR/compensation data.
    """

    __tablename__ = "performance_evaluations"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    technical_quality: Mapped[float | None] = mapped_column(Numeric(5, 2))
    delivery: Mapped[float | None] = mapped_column(Numeric(5, 2))
    collaboration: Mapped[float | None] = mapped_column(Numeric(5, 2))
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("employee_id", "period_start", "period_end"),
        CheckConstraint("period_end >= period_start", name="period_order"),
        CheckConstraint(
            "(technical_quality IS NULL OR technical_quality BETWEEN 0 AND 100)"
            " AND (delivery IS NULL OR delivery BETWEEN 0 AND 100)"
            " AND (collaboration IS NULL OR collaboration BETWEEN 0 AND 100)",
            name="ratings_range",
        ),
        CheckConstraint("reviewer_id IS NULL OR reviewer_id <> employee_id", name="no_self_evaluation"),
    )


class PerformanceMetricModel(Base):
    """Historical snapshot of a delivery metric for one employee and period (trend reporting).

    Snapshots record what a metric *was* at the end of a period; the recommendation engine always recomputes
    from ``task_outcomes`` and never reads these rows. ``sample_size`` is the number of reviewed outcomes.
    """

    __tablename__ = "performance_metrics"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(40))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Numeric(7, 2))
    sample_size: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("employee_id", "metric", "period_start", "period_end"),
        CheckConstraint(
            "metric IN ('avg_quality', 'on_time_rate', 'acceptance_rate', 'rework_rate', 'tasks_completed')",
            name="metric_name",
        ),
        CheckConstraint("sample_size >= 0", name="sample_non_negative"),
        CheckConstraint("period_end >= period_start", name="period_order"),
        Index("ix_performance_metrics_employee_period", "employee_id", "period_end"),
    )

# ===========================================================================
# AI Agent & Recommendation Models
# ===========================================================================

class TaskAnalysisModel(Base):
    __tablename__ = "task_analyses"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    analyzer_version: Mapped[str] = mapped_column(String(40))
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecommendationRunModel(Base):
    """One execution of the recommendation workflow.

    ``result_snapshot`` is the immutable API response as returned at generation time (audit artifact);
    candidates and score components are additionally normalized for querying and evaluation.
    """

    __tablename__ = "recommendation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[RecommendationStatus] = mapped_column(
        enum_column(RecommendationStatus, "recommendation_status")
    )
    requested_by: Mapped[str] = mapped_column(String(120))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    task_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    algorithm_version: Mapped[str] = mapped_column(String(40))
    scoring_config_fingerprint: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    llm_provider: Mapped[str | None] = mapped_column(String(40))
    llm_model: Mapped[str | None] = mapped_column(String(120))
    primary_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL")
    )
    data_coverage: Mapped[float | None] = mapped_column(Numeric(5, 4))
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_source: Mapped[str | None] = mapped_column(String(20))
    warnings: Mapped[list[str]] = mapped_column(ARRAY(String(80)), server_default="{}")
    revalidated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="SET NULL")
    )
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    execution_time_ms: Mapped[int] = mapped_column(Integer)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # agent execution metadata
    thread_id: Mapped[str | None] = mapped_column(String(80), index=True)  # LangGraph checkpoint thread
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    agent_mode: Mapped[AgentMode | None] = mapped_column(enum_column(AgentMode, "agent_mode"), nullable=True)
    research_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    llm_calls: Mapped[int] = mapped_column(Integer, server_default="0")
    llm_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    tool_calls_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_category: Mapped[ErrorCategory | None] = mapped_column(
        enum_column(ErrorCategory, "error_category"), nullable=True
    )
    resumable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    retry_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_recommendation_runs_company_task", "company_id", "task_id", "generated_at"),
        CheckConstraint("data_coverage IS NULL OR data_coverage BETWEEN 0 AND 1", name="coverage_range"),
        UniqueConstraint("company_id", "idempotency_key", name="uq_recommendation_runs_idempotency"),
        CheckConstraint("status <> 'FAILED' OR error_category IS NOT NULL", name="failed_has_category"),
        CheckConstraint("NOT resumable OR status = 'FAILED'", name="only_failed_resumable"),
    )


class RecommendationCandidateModel(Base):
    __tablename__ = "recommendation_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    suitability_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    eligible: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4))
    data_coverage: Mapped[float] = mapped_column(Numeric(5, 4))
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        enum_column(AvailabilityStatus, "availability_status")
    )
    available_capacity_hours: Mapped[float] = mapped_column(Numeric(7, 2))
    exclusion_reasons: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")

    __table_args__ = (
        CheckConstraint("rank IS NULL OR rank >= 1", name="rank_positive"),
        CheckConstraint("eligible OR rank IS NULL", name="only_eligible_ranked"),
    )


class RecommendationScoreComponentModel(Base):
    __tablename__ = "recommendation_score_components"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_candidates.id", ondelete="CASCADE"), index=True
    )
    criterion: Mapped[Criterion] = mapped_column(enum_column(Criterion, "criterion"))
    raw_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    score_used: Mapped[float] = mapped_column(Numeric(6, 2))
    weight: Mapped[float] = mapped_column(Numeric(5, 4))
    weighted_score: Mapped[float] = mapped_column(Numeric(6, 2))
    has_evidence: Mapped[bool] = mapped_column(Boolean)
    evidence_reference: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AgentExecutionLogModel(Base):
    __tablename__ = "agent_execution_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(SmallInteger)
    node_name: Mapped[str] = mapped_column(String(60))
    execution_status: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    sanitized_input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sanitized_output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    llm_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentToolExecutionModel(Base):
    """One tool call requested by the research agent and handled by the LangGraph ToolNode.

    Arguments and results are stored as sanitized summaries (identifiers, counts, statuses), never as full
    employee records.
    """

    __tablename__ = "agent_tool_executions"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(SmallInteger)
    research_round: Mapped[int] = mapped_column(SmallInteger)
    tool_call_id: Mapped[str] = mapped_column(String(80))
    tool_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[ToolExecutionStatus] = mapped_column(enum_column(ToolExecutionStatus, "tool_status"))
    arguments_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(60))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("recommendation_id", "sequence"),)


class AgentRunErrorModel(Base):
    __tablename__ = "agent_run_errors"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    node_name: Mapped[str | None] = mapped_column(String(60))
    code: Mapped[str] = mapped_column(String(60))
    category: Mapped[ErrorCategory] = mapped_column(enum_column(ErrorCategory, "error_category"))
    message: Mapped[str] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_events_entity", "entity_type", "entity_id"),)

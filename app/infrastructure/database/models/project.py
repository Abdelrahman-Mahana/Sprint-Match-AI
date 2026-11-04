from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    DependencyType,
    ProjectStatus,
    SprintStatus,
    TaskCategory,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
)
from app.infrastructure.database.models.base import Base, TimestampMixin, enum_column, uuid_pk


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

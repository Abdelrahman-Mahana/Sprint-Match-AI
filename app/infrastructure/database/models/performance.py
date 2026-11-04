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
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DelayReason
from app.infrastructure.database.models.base import Base, enum_column, uuid_pk


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

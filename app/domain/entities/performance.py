from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from app.domain.entities.base import DomainModel
from app.domain.enums import DelayReason, TaskCategory, TaskComplexity


class TaskOutcomeRecord(DomainModel):
    """Reviewed outcome of a completed task for one employee, with the task context needed for similarity."""

    task_id: UUID
    task_code: str
    project_id: UUID | None = None
    task_category: TaskCategory | None = None
    complexity: TaskComplexity | None = None
    required_skill_ids: tuple[UUID, ...] = ()
    quality_score: float | None = Field(default=None, ge=0, le=100)
    accepted: bool | None = None
    rework_count: int = Field(default=0, ge=0)
    on_time: bool | None = None
    delay_reason: DelayReason | None = None
    blocker_hours: float = Field(default=0, ge=0)
    reviewed_at: datetime | None = None


class PerformanceSummary(DomainModel):
    """Task-related performance evidence. Raw values are None when no evidence exists (never zero)."""

    employee_id: UUID
    outcome_count: int
    quality_sample_count: int
    raw_quality_mean: float | None
    acceptance_rate: float | None
    average_rework: float | None
    reliability_sample_count: int
    on_time_rate: float | None
    excused_delays: int
    attributable_delays: int
    similar_task_count: int = 0


class PerformanceEvaluation(DomainModel):
    """Periodic work-related review (informational; never a scoring input)."""

    period_start: date
    period_end: date
    technical_quality: float | None = Field(default=None, ge=0, le=100)
    delivery: float | None = Field(default=None, ge=0, le=100)
    collaboration: float | None = Field(default=None, ge=0, le=100)
    summary: str | None = None


class PerformanceMetricSnapshot(DomainModel):
    """Value of a delivery metric at the end of a period (trend reporting; never a scoring input)."""

    metric: str
    period_start: date
    period_end: date
    value: float | None
    sample_size: int

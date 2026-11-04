from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.employees import Employee360
from app.domain.entities.employee import Employee
from app.domain.entities.performance import PerformanceSummary
from app.domain.entities.task import ExecutionWindow, TaskRecord
from app.domain.entities.workforce import AvailabilityResult
from app.domain.enums import DelayReason


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class EmployeePage(BaseModel):
    items: list[Employee]
    total: int
    limit: int
    offset: int


class TaskPage(BaseModel):
    items: list[TaskRecord]
    total: int
    limit: int
    offset: int


class Employee360Out(BaseModel):
    """Employee 360: professional profile, effective skills, evidence-based performance and current capacity."""

    data: Employee360
    window: ExecutionWindow


class AvailabilityOut(BaseModel):
    employee_id: UUID
    employee_code: str | None = None
    full_name: str | None = None
    availability: AvailabilityResult


class WorkforceAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_ids: list[UUID] | None = Field(default=None, max_length=500)
    as_of: datetime | None = None
    window_start: datetime | None = None
    window_deadline: datetime | None = None
    working_days: int | None = Field(default=None, ge=1, le=60)


class WorkforceAnalysisOut(BaseModel):
    window: ExecutionWindow
    warnings: list[str]
    employees: list[AvailabilityOut]
    status_counts: dict[str, int]


class PerformanceOut(BaseModel):
    summary: PerformanceSummary
    outcome_count: int


class TaskOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    employee_id: UUID
    quality_score: float | None = Field(default=None, ge=0, le=100)
    accepted: bool | None = None
    rework_count: int = Field(default=0, ge=0, le=100)
    on_time: bool | None = None
    delay_reason: DelayReason | None = None
    blocker_hours: float = Field(default=0, ge=0, le=1000)
    reviewed_at: datetime | None = None


class CreatedOut(BaseModel):
    id: UUID

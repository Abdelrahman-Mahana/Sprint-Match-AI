from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.entities.base import DomainModel
from app.domain.entities.task import ExecutionWindow
from app.domain.enums import (
    AssignmentStatus,
    AttendanceStatus,
    AvailabilityStatus,
    EmploymentStatus,
    LeaveStatus,
    LeaveType,
    PresenceStatus,
    RemainingEffortBasis,
    WorkloadCertainty,
)


class ScheduleDay(DomainModel):
    weekday: int = Field(ge=0, le=6)  # Monday=0
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0, le=240)


class AttendanceRecord(DomainModel):
    work_date: date
    check_in: datetime | None = None
    check_out: datetime | None = None
    status: AttendanceStatus

    @model_validator(mode="after")
    def _check(self) -> AttendanceRecord:
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class LeaveRecord(DomainModel):
    id: UUID
    leave_type: LeaveType
    start_at: datetime
    end_at: datetime
    status: LeaveStatus


class Assignment(DomainModel):
    id: UUID
    task_id: UUID
    task_code: str
    task_title: str
    project_id: UUID | None = None
    assignment_status: AssignmentStatus
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    allocated_hours: float | None = Field(default=None, ge=0)
    remaining_estimated_hours: float | None = Field(default=None, ge=0)
    # Context needed for the documented remaining-effort fallbacks:
    task_remaining_estimated_hours: float | None = Field(default=None, ge=0)
    active_assignee_count: int = Field(default=1, ge=1)
    logged_hours: float = Field(default=0.0, ge=0)


class Commitment(DomainModel):
    """Other known, non-task commitments (support rotation, release duty, training...)."""

    id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    hours: float = Field(ge=0)


class EmployeeWorkforceData(DomainModel):
    employee_id: UUID
    timezone: str
    employment_status: EmploymentStatus
    schedule: tuple[ScheduleDay, ...] = ()
    attendance: tuple[AttendanceRecord, ...] = ()
    leaves: tuple[LeaveRecord, ...] = ()
    assignments: tuple[Assignment, ...] = ()
    commitments: tuple[Commitment, ...] = ()


class WorkloadItem(DomainModel):
    assignment_id: UUID
    task_code: str
    remaining_hours: float | None
    basis: RemainingEffortBasis
    committed_hours_in_window: float
    certainty: WorkloadCertainty
    notes: tuple[str, ...] = ()


class AvailabilityResult(DomainModel):
    """Deterministic capacity picture of one employee over one execution window.

    available_working_hours = scheduled_hours - holiday_hours - leave_hours
    available_capacity_hours = max(0, available_working_hours - committed_work_hours - other_commitment_hours)
    """

    employee_id: UUID
    window: ExecutionWindow
    presence: PresenceStatus
    scheduled_hours: float
    holiday_hours: float
    leave_hours: float
    pending_leave_hours: float
    available_working_hours: float
    committed_work_hours: float
    other_commitment_hours: float
    available_capacity_hours: float
    overcommitted_hours: float
    utilization: float | None
    status: AvailabilityStatus
    workload_certainty: WorkloadCertainty
    fully_on_leave: bool
    has_schedule: bool
    items: tuple[WorkloadItem, ...] = ()
    warnings: tuple[str, ...] = ()

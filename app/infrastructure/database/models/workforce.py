from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AssignmentStatus, AttendanceStatus, LeaveStatus, LeaveType
from app.infrastructure.database.models.base import Base, enum_column, uuid_pk


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

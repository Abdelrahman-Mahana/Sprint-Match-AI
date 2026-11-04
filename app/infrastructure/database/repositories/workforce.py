from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select

from app.domain.entities.organization import Holiday
from app.domain.entities.workforce import (
    Assignment,
    AttendanceRecord,
    Commitment,
    EmployeeWorkforceData,
    LeaveRecord,
    ScheduleDay,
)
from app.domain.enums import AssignmentStatus, LeaveStatus
from app.infrastructure.database.models import (
    AttendanceRecordModel,
    CalendarHolidayModel,
    EmployeeCommitmentModel,
    EmployeeModel,
    LeaveRecordModel,
    TaskAssignmentModel,
    TaskModel,
    TimesheetEntryModel,
    WorkScheduleDayModel,
)
from app.infrastructure.database.repositories.base import SqlRepository, to_float

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

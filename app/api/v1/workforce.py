from __future__ import annotations

import logging
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, date, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from app.api.deps import AsOfDep, Manager, ServicesDep, Viewer, WindowDep
from app.application.container import Services
from app.application.context import RequestContext
from app.core.config import get_settings
from app.infrastructure.database.session import create_db_engine, create_session_factory
from app.domain.enums import (
    AssignmentStatus,
    EmploymentStatus,
    AttendanceStatus,
    LeaveStatus,
    LeaveType,
)
from app.infrastructure.database.models.workforce import (
    WorkScheduleDayModel,
    AttendanceRecordModel,
    TimesheetEntryModel,
    LeaveRecordModel,
    TaskAssignmentModel,
    EmployeeCommitmentModel,
)
from app.infrastructure.database.models.project import TaskModel
from app.infrastructure.database.models.performance import TaskOutcomeModel
from app.infrastructure.database.models.employee import EmployeeModel
from app.infrastructure.database.models.organization import DepartmentModel, TeamModel
from app.schemas.common import AvailabilityOut, WorkforceAnalysisOut, WorkforceAnalysisRequest
from app.schemas.recommendations import ExecutionWindowRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workforce", tags=["workforce"])


class EmployeeAttendanceUpdate(BaseModel):
    full_name: str | None = None
    job_title: str | None = None
    email: str | None = None
    experience_years: float | None = None
    employment_status: str | None = None
    timezone: str | None = None
    shift_start: str | None = None
    shift_end: str | None = None
    break_minutes: int | None = None
    weekly_scheduled_hours: float | None = None
    availability_status: str | None = None
    monthly_days_present: int | None = None
    monthly_hours_worked: float | None = None
    monthly_leave_days: int | None = None


def _analyze(
    ctx: RequestContext,
    svc: Services,
    window_req: ExecutionWindowRequest | None,
    as_of: datetime,
    employee_ids: list | None,
) -> WorkforceAnalysisOut:
    window, warnings = svc.workforce.resolve_window(ctx.company, as_of, window_req)
    ids = employee_ids or svc.uow.employees.list_employee_ids(ctx.company.id, active_only=True)
    profiles = svc.uow.employees.get_profiles(ctx.company.id, ids)
    results = svc.workforce.availability(ctx.company, list(profiles), window, as_of)
    rows = sorted(
        (
            AvailabilityOut(
                employee_id=eid,
                employee_code=profiles[eid].employee.employee_code,
                full_name=profiles[eid].employee.full_name,
                availability=a,
            )
            for eid, a in results.items()
        ),
        key=lambda r: r.employee_code or "",
    )
    return WorkforceAnalysisOut(
        window=window,
        warnings=warnings,
        employees=rows,
        status_counts=dict(Counter(r.availability.status.value for r in rows)),
    )


@router.post("/analyze", response_model=WorkforceAnalysisOut)
def analyze_workforce(body: WorkforceAnalysisRequest, ctx: Viewer, svc: ServicesDep) -> WorkforceAnalysisOut:
    window = (
        ExecutionWindowRequest(start=body.window_start, deadline=body.window_deadline)
        if body.window_deadline
        else ExecutionWindowRequest(preset="NEXT_WORKING_DAYS", working_days=body.working_days)
    )
    return _analyze(ctx, svc, window, body.as_of or datetime.now(UTC), body.employee_ids)


@router.get("/availability", response_model=WorkforceAnalysisOut)
def workforce_availability(
    ctx: Viewer, svc: ServicesDep, window: WindowDep, as_of: AsOfDep
) -> WorkforceAnalysisOut:
    return _analyze(ctx, svc, window, as_of, None)


@router.get("/attendance-stats")
def get_workforce_attendance_stats(ctx: Viewer, svc: ServicesDep) -> list[dict]:
    settings = get_settings()
    engine = create_db_engine(settings)
    SessionLocal = create_session_factory(engine)
    with SessionLocal() as s:
        emp_rows = s.execute(
            select(
                EmployeeModel,
                DepartmentModel.name.label("dept_name"),
                TeamModel.name.label("team_name"),
            )
            .outerjoin(DepartmentModel, DepartmentModel.id == EmployeeModel.department_id)
            .outerjoin(TeamModel, TeamModel.id == EmployeeModel.team_id)
            .where(EmployeeModel.company_id == ctx.company.id)
            .order_by(EmployeeModel.employee_code)
        ).all()

        emp_ids = [e.id for e, _, _ in emp_rows]
        if not emp_ids:
            return []

        now = datetime.now(UTC)

        sched_map: dict[UUID, list[WorkScheduleDayModel]] = defaultdict(list)
        for sc in s.execute(
            select(WorkScheduleDayModel).where(WorkScheduleDayModel.employee_id.in_(emp_ids))
        ).scalars():
            sched_map[sc.employee_id].append(sc)

        att_map = dict(
            s.execute(
                select(
                    AttendanceRecordModel.employee_id,
                    func.count(AttendanceRecordModel.id).label("cnt"),
                )
                .where(AttendanceRecordModel.employee_id.in_(emp_ids))
                .group_by(AttendanceRecordModel.employee_id)
            ).all()
        )

        ts_map = dict(
            s.execute(
                select(
                    TimesheetEntryModel.employee_id,
                    func.sum(TimesheetEntryModel.hours).label("hrs"),
                )
                .where(TimesheetEntryModel.employee_id.in_(emp_ids))
                .group_by(TimesheetEntryModel.employee_id)
            ).all()
        )

        leave_map = dict(
            s.execute(
                select(
                    LeaveRecordModel.employee_id,
                    func.count(LeaveRecordModel.id).label("l_cnt"),
                )
                .where(LeaveRecordModel.employee_id.in_(emp_ids))
                .group_by(LeaveRecordModel.employee_id)
            ).all()
        )

        active_leaves_set = set(
            s.execute(
                select(LeaveRecordModel.employee_id)
                .where(
                    LeaveRecordModel.employee_id.in_(emp_ids),
                    LeaveRecordModel.status == LeaveStatus.APPROVED,
                    LeaveRecordModel.end_at >= now,
                    LeaveRecordModel.start_at <= now + timedelta(days=30),
                )
            ).scalars().all()
        )

        commit_map = dict(
            s.execute(
                select(
                    TaskAssignmentModel.employee_id,
                    func.coalesce(func.sum(TaskAssignmentModel.remaining_estimated_hours), 0.0).label("comm"),
                )
                .where(
                    TaskAssignmentModel.employee_id.in_(emp_ids),
                    TaskAssignmentModel.assignment_status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED]),
                )
                .group_by(TaskAssignmentModel.employee_id)
            ).all()
        )

        extra_commit_map = dict(
            s.execute(
                select(
                    EmployeeCommitmentModel.employee_id,
                    func.coalesce(func.sum(EmployeeCommitmentModel.hours), 0.0).label("extra_comm"),
                )
                .where(
                    EmployeeCommitmentModel.employee_id.in_(emp_ids),
                    EmployeeCommitmentModel.end_at >= now,
                )
                .group_by(EmployeeCommitmentModel.employee_id)
            ).all()
        )

        outcomes_map = dict(
            s.execute(
                select(
                    TaskOutcomeModel.employee_id,
                    func.count(TaskOutcomeModel.id).label("task_cnt"),
                )
                .where(TaskOutcomeModel.employee_id.in_(emp_ids))
                .group_by(TaskOutcomeModel.employee_id)
            ).all()
        )

        quality_map = dict(
            s.execute(
                select(
                    TaskOutcomeModel.employee_id,
                    func.round(func.avg(TaskOutcomeModel.quality_score), 1).label("qual"),
                )
                .where(
                    TaskOutcomeModel.employee_id.in_(emp_ids),
                    TaskOutcomeModel.quality_score.isnot(None),
                )
                .group_by(TaskOutcomeModel.employee_id)
            ).all()
        )

        out = []
        total_workdays_month = 22
        for emp, dept_name, team_name in emp_rows:
            sc_list = sched_map.get(emp.id, [])
            if sc_list:
                sample_sc = sc_list[0]
                st = sample_sc.start_time.strftime("%H:%M")
                et = sample_sc.end_time.strftime("%H:%M")
                brk = sample_sc.break_minutes
                workdays_count = len(sc_list)
                st_minutes = sample_sc.start_time.hour * 60 + sample_sc.start_time.minute
                et_minutes = sample_sc.end_time.hour * 60 + sample_sc.end_time.minute
                shift_duration = max(0.0, float((et_minutes - st_minutes) - brk) / 60.0)
                weekly_hours = round(shift_duration * workdays_count, 1)
            else:
                st = "09:00"
                et = "17:00"
                brk = 60
                shift_duration = 7.0
                workdays_count = 5
                weekly_hours = 35.0

            # Direct database values from attendance_records and timesheet_entries
            days_present = int(att_map.get(emp.id, 0))
            hours_worked = round(float(ts_map.get(emp.id, 0.0) or 0.0), 1)

            expected_monthly_hours = round(total_workdays_month * shift_duration, 1)
            leaves_count = leave_map.get(emp.id, 0)
            task_committed = float(commit_map.get(emp.id, 0.0) or 0.0)
            extra_committed = float(extra_commit_map.get(emp.id, 0.0) or 0.0)
            total_committed = round(task_committed + extra_committed, 1)

            if emp.employment_status.value in ("INACTIVE", "SUSPENDED"):
                status_val = "INACTIVE"
                available_capacity = 0.0
            elif emp.id in active_leaves_set:
                status_val = "ON_LEAVE"
                available_capacity = 0.0
            elif total_committed >= weekly_hours * 0.8:
                status_val = "BUSY"
                available_capacity = 0.0
            elif total_committed > 0:
                status_val = "PARTIALLY_AVAILABLE"
                available_capacity = max(0.0, round(weekly_hours - total_committed, 1))
            else:
                status_val = "AVAILABLE"
                available_capacity = weekly_hours

            out.append(
                {
                    "employee_id": str(emp.id),
                    "employee_code": emp.employee_code,
                    "full_name": emp.full_name,
                    "email": emp.email or f"{emp.employee_code.lower()}@nds.example",
                    "job_title": emp.job_title,
                    "department_name": dept_name or "Engineering",
                    "team_name": team_name or "Core Team",
                    "experience_years": float(emp.experience_years or 5.0),
                    "employment_status": emp.employment_status.value,
                    "timezone": emp.timezone or "Africa/Cairo",
                    "shift_start": st,
                    "shift_end": et,
                    "break_minutes": brk,
                    "daily_working_hours": shift_duration,
                    "weekly_working_days": workdays_count,
                    "weekly_scheduled_hours": weekly_hours,
                    "availability_status": status_val,
                    "available_capacity_hours": available_capacity,
                    "committed_work_hours": total_committed,
                    "utilization_rate": round(total_committed / weekly_hours, 2) if weekly_hours > 0 else 0.0,
                    "monthly_days_present": days_present,
                    "monthly_total_working_days": total_workdays_month,
                    "monthly_hours_worked": hours_worked,
                    "monthly_expected_hours": expected_monthly_hours,
                    "monthly_leave_days": leaves_count,
                    "attendance_rate": round(days_present / total_workdays_month, 2),
                    "completed_tasks_count": int(outcomes_map.get(emp.id, 0)),
                    "average_quality_score": float(quality_map[emp.id]) if emp.id in quality_map and quality_map[emp.id] is not None else None,
                }
            )

        return out


@router.put("/employees/{employee_id}/attendance-profile")
def update_employee_attendance_profile(
    employee_id: UUID,
    body: EmployeeAttendanceUpdate,
    ctx: Manager,
    svc: ServicesDep,
) -> dict:
    settings = get_settings()
    engine = create_db_engine(settings)
    SessionLocal = create_session_factory(engine)
    with SessionLocal() as s:
        emp = s.query(EmployeeModel).filter_by(id=employee_id, company_id=ctx.company.id).first()
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found")

        if body.full_name is not None and body.full_name.strip():
            emp.full_name = body.full_name.strip()
        if body.job_title is not None and body.job_title.strip():
            emp.job_title = body.job_title.strip()
        if body.email is not None and body.email.strip():
            emp.email = body.email.strip()
        if body.experience_years is not None:
            emp.experience_years = body.experience_years
        if body.timezone is not None and body.timezone.strip():
            emp.timezone = body.timezone.strip()

        now = datetime.now(UTC)
        today = date.today()

        # Handle Availability Status
        target_status = (body.availability_status or "").upper().strip()
        if target_status:
            if target_status == "INACTIVE":
                emp.employment_status = EmploymentStatus.INACTIVE
                att = s.query(AttendanceRecordModel).filter_by(employee_id=employee_id, work_date=today).first()
                if att:
                    att.attendance_status = AttendanceStatus.ABSENT
                else:
                    s.add(
                        AttendanceRecordModel(
                            id=uuid.uuid4(),
                            employee_id=employee_id,
                            work_date=today,
                            attendance_status=AttendanceStatus.ABSENT,
                        )
                    )

            elif target_status == "ON_LEAVE":
                emp.employment_status = EmploymentStatus.ACTIVE
                # Remove busy commitments
                s.query(EmployeeCommitmentModel).filter(
                    EmployeeCommitmentModel.employee_id == employee_id,
                    (
                        EmployeeCommitmentModel.title.like("%Dedicated%")
                        | EmployeeCommitmentModel.title.like("%Sprint Overload%")
                    ),
                ).delete()

                # Add / ensure active approved leave record covering the current and upcoming window
                s.query(LeaveRecordModel).filter(
                    LeaveRecordModel.employee_id == employee_id,
                    LeaveRecordModel.end_at >= now,
                ).delete()
                s.add(
                    LeaveRecordModel(
                        id=uuid.uuid4(),
                        employee_id=employee_id,
                        leave_type=LeaveType.ANNUAL,
                        start_at=now - timedelta(days=2),
                        end_at=now + timedelta(days=60),
                        status=LeaveStatus.APPROVED,
                    )
                )

                att = s.query(AttendanceRecordModel).filter_by(employee_id=employee_id, work_date=today).first()
                if att:
                    att.attendance_status = AttendanceStatus.ON_LEAVE
                else:
                    s.add(
                        AttendanceRecordModel(
                            id=uuid.uuid4(),
                            employee_id=employee_id,
                            work_date=today,
                            attendance_status=AttendanceStatus.ON_LEAVE,
                        )
                    )

            elif target_status == "BUSY":
                emp.employment_status = EmploymentStatus.ACTIVE
                # Remove active leaves
                s.query(LeaveRecordModel).filter(
                    LeaveRecordModel.employee_id == employee_id,
                    LeaveRecordModel.end_at >= now,
                ).delete()

                # Insert high commitment that consumes available capacity
                s.query(EmployeeCommitmentModel).filter(
                    EmployeeCommitmentModel.employee_id == employee_id,
                    EmployeeCommitmentModel.title.like("%Sprint Overload%"),
                ).delete()
                s.add(
                    EmployeeCommitmentModel(
                        id=uuid.uuid4(),
                        employee_id=employee_id,
                        title="Sprint Overload & Dedicated Engagements",
                        start_at=now - timedelta(days=2),
                        end_at=now + timedelta(days=60),
                        hours=250.0,
                    )
                )

                att = s.query(AttendanceRecordModel).filter_by(employee_id=employee_id, work_date=today).first()
                if att:
                    att.attendance_status = AttendanceStatus.PRESENT
                    if not att.check_in:
                        att.check_in = now.replace(hour=9, minute=0, second=0)
                else:
                    s.add(
                        AttendanceRecordModel(
                            id=uuid.uuid4(),
                            employee_id=employee_id,
                            work_date=today,
                            check_in=now.replace(hour=9, minute=0, second=0),
                            attendance_status=AttendanceStatus.PRESENT,
                        )
                    )

            elif target_status == "PARTIALLY_AVAILABLE":
                emp.employment_status = EmploymentStatus.ACTIVE
                s.query(LeaveRecordModel).filter(
                    LeaveRecordModel.employee_id == employee_id,
                    LeaveRecordModel.end_at >= now,
                ).delete()

                s.query(EmployeeCommitmentModel).filter(
                    EmployeeCommitmentModel.employee_id == employee_id,
                    EmployeeCommitmentModel.title.like("%Sprint Overload%"),
                ).delete()
                s.add(
                    EmployeeCommitmentModel(
                        id=uuid.uuid4(),
                        employee_id=employee_id,
                        title="Sprint Overload & Dedicated Engagements",
                        start_at=now - timedelta(days=2),
                        end_at=now + timedelta(days=60),
                        hours=18.0,
                    )
                )

                att = s.query(AttendanceRecordModel).filter_by(employee_id=employee_id, work_date=today).first()
                if att:
                    att.attendance_status = AttendanceStatus.PRESENT
                    if not att.check_in:
                        att.check_in = now.replace(hour=9, minute=0, second=0)
                else:
                    s.add(
                        AttendanceRecordModel(
                            id=uuid.uuid4(),
                            employee_id=employee_id,
                            work_date=today,
                            check_in=now.replace(hour=9, minute=0, second=0),
                            attendance_status=AttendanceStatus.PRESENT,
                        )
                    )

            elif target_status == "AVAILABLE":
                emp.employment_status = EmploymentStatus.ACTIVE
                # Clear all blocking leaves and commitments
                s.query(LeaveRecordModel).filter(
                    LeaveRecordModel.employee_id == employee_id,
                    LeaveRecordModel.end_at >= now,
                ).delete()
                s.query(EmployeeCommitmentModel).filter(
                    EmployeeCommitmentModel.employee_id == employee_id,
                    (
                        EmployeeCommitmentModel.title.like("%Sprint Overload%")
                        | EmployeeCommitmentModel.title.like("%Dedicated%")
                    ),
                ).delete()

                att = s.query(AttendanceRecordModel).filter_by(employee_id=employee_id, work_date=today).first()
                if att:
                    att.attendance_status = AttendanceStatus.PRESENT
                    if not att.check_in:
                        att.check_in = now.replace(hour=9, minute=0, second=0)
                else:
                    s.add(
                        AttendanceRecordModel(
                            id=uuid.uuid4(),
                            employee_id=employee_id,
                            work_date=today,
                            check_in=now.replace(hour=9, minute=0, second=0),
                            attendance_status=AttendanceStatus.PRESENT,
                        )
                    )
        elif body.employment_status is not None:
            try:
                emp.employment_status = EmploymentStatus(body.employment_status.upper())
            except ValueError:
                pass

        # Handle Monthly Days Present Update in attendance_records
        if body.monthly_days_present is not None and body.monthly_days_present >= 0:
            target_days = min(31, int(body.monthly_days_present))
            start_month = today.replace(day=1)
            s.query(AttendanceRecordModel).filter(
                AttendanceRecordModel.employee_id == employee_id,
                AttendanceRecordModel.work_date >= start_month,
            ).delete()
            cur_day = start_month
            added = 0
            while added < target_days and cur_day <= today:
                cin = datetime.combine(cur_day, datetime.min.time(), tzinfo=UTC).replace(hour=9)
                cout = datetime.combine(cur_day, datetime.min.time(), tzinfo=UTC).replace(hour=17)
                s.add(
                    AttendanceRecordModel(
                        id=uuid.uuid4(),
                        employee_id=employee_id,
                        work_date=cur_day,
                        check_in=cin,
                        check_out=cout,
                        attendance_status=AttendanceStatus.PRESENT,
                    )
                )
                added += 1
                cur_day += timedelta(days=1)

        # Handle Monthly Hours Worked Update in timesheet_entries
        if body.monthly_hours_worked is not None and body.monthly_hours_worked >= 0:
            target_hours = float(body.monthly_hours_worked)
            start_month = today.replace(day=1)
            sample_task = s.execute(select(TaskModel)).scalars().first()
            if sample_task:
                s.query(TimesheetEntryModel).filter(
                    TimesheetEntryModel.employee_id == employee_id,
                    TimesheetEntryModel.entry_date >= start_month,
                ).delete()
                remaining = target_hours
                cur_day = today
                while remaining > 0 and cur_day >= start_month:
                    chunk = min(8.0, remaining)
                    s.add(
                        TimesheetEntryModel(
                            id=uuid.uuid4(),
                            task_id=sample_task.id,
                            employee_id=employee_id,
                            entry_date=cur_day,
                            hours=chunk,
                            description="Monthly timesheet record",
                        )
                    )
                    remaining -= chunk
                    cur_day -= timedelta(days=1)

        # Handle Work Schedule (shift_start, shift_end, break_minutes)
        if body.shift_start and body.shift_end:
            try:
                st = datetime.strptime(body.shift_start[:5], "%H:%M").time()
                et = datetime.strptime(body.shift_end[:5], "%H:%M").time()
                brk = body.break_minutes if body.break_minutes is not None else 60

                schedules = s.query(WorkScheduleDayModel).filter_by(employee_id=employee_id).all()
                if schedules:
                    for sc in schedules:
                        sc.start_time = st
                        sc.end_time = et
                        sc.break_minutes = brk
                else:
                    for wd in [0, 1, 2, 3, 6]:
                        s.add(
                            WorkScheduleDayModel(
                                employee_id=employee_id,
                                weekday=wd,
                                start_time=st,
                                end_time=et,
                                break_minutes=brk,
                            )
                        )
            except Exception as e:
                logger.warning("Failed to parse or update schedule: %s", e)

        s.commit()
        s.refresh(emp)

        return {
            "status": "success",
            "message": f"Updated attendance and profile for {emp.full_name}",
            "employee_id": str(emp.id),
            "full_name": emp.full_name,
            "job_title": emp.job_title,
            "employment_status": emp.employment_status.value,
        }

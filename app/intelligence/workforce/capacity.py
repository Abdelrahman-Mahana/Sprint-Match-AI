"""Deterministic workload, capacity, presence and availability classification.

Definitions (see docs/SCORING.md, "Workforce availability"):

    scheduled_hours          = net scheduled working time inside the window (breaks prorated)
    holiday_hours            = part of scheduled_hours falling on company holidays
    leave_hours              = part of the remaining scheduled time covered by APPROVED leave
    available_working_hours  = scheduled_hours - holiday_hours - leave_hours
    committed_work_hours     = sum of remaining effort of active assignments, prorated into the window
    other_commitment_hours   = other known commitments, prorated into the window
    available_capacity_hours = max(0, available_working_hours - committed_work_hours - other_commitment_hours)

Timesheet hours are historical consumption and are never subtracted from capacity. They are used in exactly
one place: estimating remaining effort as ``allocated - logged`` when no remaining estimate exists, and that
estimate is explicitly marked ESTIMATED.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.domain.entities.task import ExecutionWindow
from app.domain.entities.workforce import (
    Assignment,
    AvailabilityResult,
    EmployeeWorkforceData,
    WorkloadItem,
)
from app.domain.enums import (
    AttendanceStatus,
    AvailabilityStatus,
    EmploymentStatus,
    LeaveStatus,
    PresenceStatus,
    RemainingEffortBasis,
    WorkloadCertainty,
)
from app.domain.policies import AvailabilityPolicy
from app.intelligence.workforce.calendar import (
    Interval,
    build_work_blocks,
    merge_intervals,
    overlap_fraction,
)

_EPS = 1e-6
HoursFn = Callable[[datetime, datetime], float]


def _r(value: float) -> float:
    return round(value + 0.0, 2)


def resolve_remaining_effort(a: Assignment) -> tuple[float | None, RemainingEffortBasis, WorkloadCertainty]:
    """Documented precedence for the remaining effort of one employee-level assignment."""
    if a.remaining_estimated_hours is not None:
        return (
            a.remaining_estimated_hours,
            RemainingEffortBasis.ASSIGNMENT_REMAINING_ESTIMATE,
            WorkloadCertainty.CERTAIN,
        )
    if a.active_assignee_count == 1 and a.task_remaining_estimated_hours is not None:
        return (
            a.task_remaining_estimated_hours,
            RemainingEffortBasis.TASK_REMAINING_SOLE_ASSIGNEE,
            WorkloadCertainty.CERTAIN,
        )
    if a.allocated_hours is not None:
        return (
            max(a.allocated_hours - a.logged_hours, 0.0),
            RemainingEffortBasis.ALLOCATION_MINUS_LOGGED,
            WorkloadCertainty.ESTIMATED,
        )
    # Multi-assignee task without employee-level split, or no estimate at all: never guess.
    return None, RemainingEffortBasis.UNKNOWN, WorkloadCertainty.UNCERTAIN


class WorkforceCalculator:
    def __init__(self, policy: AvailabilityPolicy | None = None) -> None:
        self.policy = policy or AvailabilityPolicy()

    # ------------------------------------------------------------------------------------------
    def calculate(
        self,
        data: EmployeeWorkforceData,
        window: ExecutionWindow,
        *,
        as_of: datetime,
        holidays: set[date] | frozenset[date] = frozenset(),
    ) -> AvailabilityResult:
        warnings: list[str] = []
        has_schedule = bool(data.schedule)
        if not has_schedule:
            warnings.append("NO_WORK_SCHEDULE")

        approved = merge_intervals(
            (lv.start_at, lv.end_at) for lv in data.leaves if lv.status == LeaveStatus.APPROVED
        )
        pending = merge_intervals(
            (lv.start_at, lv.end_at) for lv in data.leaves if lv.status == LeaveStatus.REQUESTED
        )

        blocks = build_work_blocks(data.schedule, data.timezone, window.start, window.end, holidays)
        scheduled = sum(b.net_hours for b in blocks)
        holiday_hours = sum(b.net_hours for b in blocks if b.holiday)
        working_blocks = [b for b in blocks if not b.holiday]
        leave_hours = sum(b.net_hours_covered_by(approved) for b in working_blocks)
        pending_hours = sum(b.net_hours_covered_by(pending) for b in working_blocks)
        if pending_hours > _EPS:
            warnings.append("PENDING_LEAVE_IN_WINDOW")

        available_working = max(scheduled - holiday_hours - leave_hours, 0.0)
        non_holiday = scheduled - holiday_hours
        fully_on_leave = non_holiday > _EPS and leave_hours >= non_holiday - _EPS

        def effective_hours(start: datetime, end: datetime) -> float:
            """Employee working time in [start, end) excluding holidays and approved leave."""
            if end <= start:
                return 0.0
            bs = build_work_blocks(data.schedule, data.timezone, start, end, holidays)
            return sum(b.net_hours - b.net_hours_covered_by(approved) for b in bs if not b.holiday)

        items: list[WorkloadItem] = []
        for assignment in data.assignments:
            if not assignment.assignment_status.consumes_capacity:
                continue
            item = self._workload_item(assignment, window, as_of, effective_hours, has_schedule)
            items.append(item)
            if item.basis == RemainingEffortBasis.UNKNOWN:
                warnings.append(f"REMAINING_EFFORT_UNKNOWN:{assignment.task_code}")
            elif item.basis == RemainingEffortBasis.ALLOCATION_MINUS_LOGGED:
                warnings.append(f"REMAINING_EFFORT_ESTIMATED:{assignment.task_code}")
            for note in item.notes:
                if note == "OVERDUE_FULLY_ALLOCATED":
                    warnings.append(f"OVERDUE_ASSIGNMENT:{assignment.task_code}")

        committed = sum(i.committed_hours_in_window for i in items)
        other = sum(
            c.hours * overlap_fraction(c.start_at, c.end_at, window.start, window.end)
            for c in data.commitments
        )
        raw_capacity = available_working - committed - other
        capacity = max(raw_capacity, 0.0)
        overcommitted = max(-raw_capacity, 0.0)
        utilization = (committed + other) / available_working if available_working > _EPS else None

        certainties = {i.certainty for i in items}
        if WorkloadCertainty.UNCERTAIN in certainties:
            certainty = WorkloadCertainty.UNCERTAIN
        elif WorkloadCertainty.ESTIMATED in certainties:
            certainty = WorkloadCertainty.ESTIMATED
        else:
            certainty = WorkloadCertainty.CERTAIN

        presence = self.determine_presence(data, as_of, holidays, approved)
        tz = ZoneInfo(data.timezone)
        if window.start.astimezone(tz).date() == as_of.astimezone(tz).date() and presence in (
            PresenceStatus.NO_RECORD,
            PresenceStatus.ABSENT,
        ):
            warnings.append("NOT_CHECKED_IN_TODAY")

        status = self.classify(
            employment_status=data.employment_status,
            has_schedule=has_schedule,
            available_working_hours=available_working,
            capacity=capacity,
            certainty=certainty,
        )
        return AvailabilityResult(
            employee_id=data.employee_id,
            window=window,
            presence=presence,
            scheduled_hours=_r(scheduled),
            holiday_hours=_r(holiday_hours),
            leave_hours=_r(leave_hours),
            pending_leave_hours=_r(pending_hours),
            available_working_hours=_r(available_working),
            committed_work_hours=_r(committed),
            other_commitment_hours=_r(other),
            available_capacity_hours=_r(capacity),
            overcommitted_hours=_r(overcommitted),
            utilization=round(utilization, 4) if utilization is not None else None,
            status=status,
            workload_certainty=certainty,
            fully_on_leave=fully_on_leave,
            has_schedule=has_schedule,
            items=tuple(items),
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------------------------------
    def _workload_item(
        self,
        a: Assignment,
        window: ExecutionWindow,
        as_of: datetime,
        effective_hours: HoursFn,
        has_schedule: bool,
    ) -> WorkloadItem:
        remaining, basis, certainty = resolve_remaining_effort(a)
        notes: list[str] = []
        committed = 0.0
        if remaining is not None and remaining > 0:
            committed, notes = self._prorate(remaining, a, window, as_of, effective_hours, has_schedule)
            if notes and certainty == WorkloadCertainty.CERTAIN and "NO_PLANNED_END_FULLY_ALLOCATED" in notes:
                certainty = WorkloadCertainty.ESTIMATED
        return WorkloadItem(
            assignment_id=a.id,
            task_code=a.task_code,
            remaining_hours=_r(remaining) if remaining is not None else None,
            basis=basis,
            committed_hours_in_window=_r(committed),
            certainty=certainty,
            notes=tuple(notes),
        )

    @staticmethod
    def _prorate(
        remaining: float,
        a: Assignment,
        window: ExecutionWindow,
        as_of: datetime,
        effective_hours: HoursFn,
        has_schedule: bool,
    ) -> tuple[float, list[str]]:
        """Share of ``remaining`` that falls into the window, assuming even spread over the planned period."""
        if a.planned_end is None:
            return remaining, ["NO_PLANNED_END_FULLY_ALLOCATED"]
        if a.planned_end <= as_of:
            return remaining, ["OVERDUE_FULLY_ALLOCATED"]
        period_start = max(as_of, a.planned_start) if a.planned_start else as_of
        if a.planned_end <= window.start or period_start >= window.end:
            return 0.0, []
        if not has_schedule:
            return remaining * overlap_fraction(period_start, a.planned_end, window.start, window.end), [
                "PRORATED_BY_CALENDAR_TIME"
            ]
        total = effective_hours(period_start, a.planned_end)
        if total <= _EPS:
            return remaining, ["PLAN_PERIOD_WITHOUT_WORKING_TIME"]
        in_window = effective_hours(max(period_start, window.start), min(a.planned_end, window.end))
        return remaining * min(in_window / total, 1.0), []

    # ------------------------------------------------------------------------------------------
    @staticmethod
    def determine_presence(
        data: EmployeeWorkforceData,
        as_of: datetime,
        holidays: set[date] | frozenset[date],
        approved_leaves: list[Interval] | None = None,
    ) -> PresenceStatus:
        """Current operational indicator only. Presence is never used as a proxy for future availability."""
        if approved_leaves is None:
            approved_leaves = merge_intervals(
                (lv.start_at, lv.end_at) for lv in data.leaves if lv.status == LeaveStatus.APPROVED
            )
        if any(s <= as_of < e for s, e in approved_leaves):
            return PresenceStatus.ON_LEAVE
        today = as_of.astimezone(ZoneInfo(data.timezone)).date()
        record = next((r for r in data.attendance if r.work_date == today), None)
        if record is not None:
            if record.status == AttendanceStatus.ON_LEAVE:
                return PresenceStatus.ON_LEAVE
            if record.status == AttendanceStatus.ABSENT:
                return PresenceStatus.ABSENT
            if record.check_in is not None and record.check_out is None:
                return PresenceStatus.PRESENT
            if record.check_out is not None:
                return PresenceStatus.CHECKED_OUT
        scheduled_today = any(d.weekday == today.weekday() for d in data.schedule)
        if not scheduled_today or today in holidays:
            return PresenceStatus.NOT_SCHEDULED
        return PresenceStatus.NO_RECORD

    def classify(
        self,
        *,
        employment_status: EmploymentStatus,
        has_schedule: bool,
        available_working_hours: float,
        capacity: float,
        certainty: WorkloadCertainty,
    ) -> AvailabilityStatus:
        """Rules are evaluated in order; see docs/SCORING.md for the table."""
        p = self.policy
        if employment_status != EmploymentStatus.ACTIVE:
            return AvailabilityStatus.UNAVAILABLE
        if not has_schedule:
            return AvailabilityStatus.UNKNOWN
        if available_working_hours <= _EPS:
            return AvailabilityStatus.UNAVAILABLE
        free_ratio = capacity / available_working_hours
        if free_ratio <= p.busy_max_free_ratio or (
            capacity < p.busy_max_free_hours and free_ratio < p.available_min_free_ratio
        ):
            return AvailabilityStatus.BUSY
        if certainty == WorkloadCertainty.UNCERTAIN:
            return AvailabilityStatus.UNKNOWN
        if free_ratio >= p.available_min_free_ratio:
            return AvailabilityStatus.AVAILABLE
        return AvailabilityStatus.PARTIALLY_AVAILABLE

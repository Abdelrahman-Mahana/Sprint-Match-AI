"""Deterministic workforce intelligence: skill decay assessment, Bayesian performance summary, work calendars, execution windows, and capacity calculation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core import InputValidationError
from app.domain import (
    Assignment,
    AttendanceStatus,
    AvailabilityPolicy,
    AvailabilityResult,
    AvailabilityStatus,
    Company,
    EmployeeSkill,
    EmployeeWorkforceData,
    EmploymentStatus,
    ExecutionWindow,
    LeaveStatus,
    PerformancePolicy,
    PerformanceSummary,
    PresenceStatus,
    RemainingEffortBasis,
    ScheduleDay,
    SkillAssessment,
    SkillPolicy,
    Sprint,
    TaskOutcomeRecord,
    TaskRequirements,
    WindowPreset,
    WorkloadCertainty,
    WorkloadItem,
)

# ===========================================================================
# Skill Proficiency & Decay Assessment
# ===========================================================================

def _months_between(earlier: datetime, later: datetime) -> int:
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return max(months, 0)


def assess_skill(skill: EmployeeSkill, as_of: datetime, policy: SkillPolicy) -> SkillAssessment:
    verification_factor = policy.verification_factors.get(skill.verification_status, 0.0)
    assessed = skill.last_assessed_at or skill.verified_at
    months = _months_between(assessed, as_of) if assessed else None
    recency_factor = 1.0
    if months is not None:
        for min_months, factor in sorted(policy.staleness_steps, key=lambda s: -s[0]):
            if months >= min_months:
                recency_factor = factor
                break
    effective = round(skill.proficiency_level * verification_factor * recency_factor, 2)
    return SkillAssessment(
        skill_id=skill.skill_id,
        skill_name=skill.skill_name,
        proficiency_level=skill.proficiency_level,
        verification_status=skill.verification_status,
        verification_factor=verification_factor,
        recency_factor=recency_factor,
        effective_level=effective,
        months_since_assessment=months,
        stale=recency_factor < 1.0,
    )

# ===========================================================================
# Performance Metrics & Bayesian Shrinkage
# ===========================================================================

def task_similarity(outcome: TaskOutcomeRecord, requirements: TaskRequirements) -> float:
    """0..1: weighted share of the new task's required skills that the past task also required (80%),
    plus category match (20%)."""
    reqs = [s for s in requirements.required_skills if s.skill_id is not None]
    category_match = (
        1.0 if outcome.task_category and outcome.task_category == requirements.task_category else 0.0
    )
    if not reqs:
        return category_match
    past = set(outcome.required_skill_ids)
    total = sum(s.weight for s in reqs)
    shared = sum(s.weight for s in reqs if s.skill_id in past)
    return round(0.8 * (shared / total) + 0.2 * category_match, 4)


def outcome_quality(outcome: TaskOutcomeRecord, policy: PerformancePolicy) -> float | None:
    if outcome.quality_score is None:
        return None
    q = outcome.quality_score - policy.rework_penalty * outcome.rework_count
    if outcome.accepted is False:
        q = min(q, policy.rejected_quality_cap)
    return max(min(q, 100.0), 0.0)


def summarize_performance(
    employee_id: UUID,
    outcomes: Sequence[TaskOutcomeRecord],
    policy: PerformancePolicy,
    requirements: TaskRequirements | None = None,
) -> PerformanceSummary:
    weighted_sum = 0.0
    weight_total = 0.0
    quality_samples = 0
    similar = 0
    for o in outcomes:
        is_similar = (
            requirements is not None and task_similarity(o, requirements) >= policy.similarity_threshold
        )
        similar += int(is_similar)
        q = outcome_quality(o, policy)
        if q is None:
            continue
        w = policy.similar_task_weight if is_similar else 1.0
        weighted_sum += w * q
        weight_total += w
        quality_samples += 1

    accepted_known = [o.accepted for o in outcomes if o.accepted is not None]
    on_time = 0
    attributable = 0
    excused = 0
    for o in outcomes:
        if o.on_time is None:
            continue
        if o.on_time:
            on_time += 1
        elif o.delay_reason is None or o.delay_reason.attributable_to_employee:
            attributable += 1
        else:
            excused += 1
    reliability_samples = on_time + attributable

    return PerformanceSummary(
        employee_id=employee_id,
        outcome_count=len(outcomes),
        quality_sample_count=quality_samples,
        raw_quality_mean=round(weighted_sum / weight_total, 2) if weight_total else None,
        acceptance_rate=round(sum(accepted_known) / len(accepted_known), 4) if accepted_known else None,
        average_rework=round(sum(o.rework_count for o in outcomes) / len(outcomes), 2) if outcomes else None,
        reliability_sample_count=reliability_samples,
        on_time_rate=round(on_time / reliability_samples, 4) if reliability_samples else None,
        excused_delays=excused,
        attributable_delays=attributable,
        similar_task_count=similar,
    )


def shrink(value: float, samples: int, prior: float, pseudo_count: int) -> float:
    """Bayesian shrinkage toward the prior: small samples cannot produce extreme scores."""
    return (samples * value + pseudo_count * prior) / (samples + pseudo_count)

# ===========================================================================
# Working-Time Calendar Algebra
# ===========================================================================

Interval = tuple[datetime, datetime]


def hours_between(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds(), 0.0) / 3600.0


@dataclass(frozen=True, slots=True)
class WorkBlock:
    start: datetime  # UTC
    end: datetime  # UTC
    net_factor: float
    local_date: date
    holiday: bool

    @property
    def net_hours(self) -> float:
        return hours_between(self.start, self.end) * self.net_factor

    def net_hours_within(self, start: datetime, end: datetime) -> float:
        s, e = max(self.start, start), min(self.end, end)
        return hours_between(s, e) * self.net_factor if e > s else 0.0

    def net_hours_covered_by(self, intervals: Sequence[Interval]) -> float:
        return sum(self.net_hours_within(s, e) for s, e in intervals)


def build_work_blocks(
    schedule: Sequence[ScheduleDay],
    timezone: str,
    start: datetime,
    end: datetime,
    holidays: set[date] | frozenset[date] = frozenset(),
) -> list[WorkBlock]:
    """Expand a weekly schedule into concrete working blocks clipped to [start, end)."""
    if end <= start or not schedule:
        return []
    tz = ZoneInfo(timezone)
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    by_weekday = {d.weekday: d for d in schedule}
    day = start.astimezone(tz).date() - timedelta(days=1)  # shifts may cross midnight
    last_day = end.astimezone(tz).date()
    blocks: list[WorkBlock] = []
    while day <= last_day:
        shift = by_weekday.get(day.weekday())
        if shift is not None:
            s = datetime.combine(day, shift.start_time, tzinfo=tz).astimezone(UTC)
            e_day = day if shift.end_time > shift.start_time else day + timedelta(days=1)
            e = datetime.combine(e_day, shift.end_time, tzinfo=tz).astimezone(UTC)
            gross = hours_between(s, e)
            if gross > 0:
                factor = max(gross - shift.break_minutes / 60.0, 0.0) / gross
                cs, ce = max(s, start_utc), min(e, end_utc)
                if ce > cs:
                    blocks.append(WorkBlock(cs, ce, factor, day, day in holidays))
        day += timedelta(days=1)
    return blocks


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    ordered = sorted((s.astimezone(UTC), e.astimezone(UTC)) for s, e in intervals if e > s)
    merged: list[Interval] = []
    for s, e in ordered:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def overlap_fraction(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> float:
    total = hours_between(start, end)
    if total <= 0:
        return 0.0
    return hours_between(max(start, window_start), min(end, window_end)) / total


def is_working_day(day: date, working_weekdays: Iterable[int], holidays: set[date] | frozenset[date]) -> bool:
    return day.weekday() in set(working_weekdays) and day not in holidays


def next_working_days(
    after: date, count: int, working_weekdays: Iterable[int], holidays: set[date] | frozenset[date]
) -> list[date]:
    """The next ``count`` working days strictly after ``after``."""
    weekdays = set(working_weekdays)
    if not weekdays:
        raise ValueError("working_weekdays must not be empty")
    days: list[date] = []
    day = after
    while len(days) < count:
        day += timedelta(days=1)
        if is_working_day(day, weekdays, holidays):
            days.append(day)
    return days

# ===========================================================================
# Execution Window Resolver
# ===========================================================================

def _day_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def resolve_window(
    preset: WindowPreset,
    *,
    as_of: datetime,
    company: Company,
    holidays: set[date] | frozenset[date] = frozenset(),
    working_days: int | None = None,
    sprint: Sprint | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[ExecutionWindow, list[str]]:
    if as_of.tzinfo is None:
        raise InputValidationError("as_of must be timezone-aware")
    tz = ZoneInfo(company.timezone)
    today = as_of.astimezone(tz).date()
    warnings: list[str] = []

    if preset == WindowPreset.TODAY:
        w_start, w_end = _day_start(today, tz), _day_start(today + timedelta(days=1), tz)
    elif preset == WindowPreset.REMAINING_DAY:
        w_start, w_end = as_of, _day_start(today + timedelta(days=1), tz)
    elif preset in (WindowPreset.NEXT_WORKING_DAY, WindowPreset.NEXT_WORKING_DAYS):
        count = 1 if preset == WindowPreset.NEXT_WORKING_DAY else (working_days or 0)
        if count < 1:
            raise InputValidationError("working_days must be >= 1 for NEXT_WORKING_DAYS")
        days = next_working_days(today, count, company.working_weekdays, holidays)
        w_start, w_end = _day_start(days[0], tz), _day_start(days[-1] + timedelta(days=1), tz)
    elif preset == WindowPreset.CURRENT_SPRINT:
        if sprint is None:
            raise InputValidationError("CURRENT_SPRINT requires a task that belongs to a sprint")
        w_start = _day_start(sprint.start_date, tz)
        w_end = _day_start(sprint.end_date + timedelta(days=1), tz)
    elif preset in (WindowPreset.EXPLICIT, WindowPreset.TASK_PLANNED):
        if end is None:
            raise InputValidationError("an explicit window requires a deadline/end")
        if end.tzinfo is None or (start is not None and start.tzinfo is None):
            raise InputValidationError("window datetimes must include a timezone offset")
        w_start, w_end = (start or as_of), end
    else:  # pragma: no cover - exhaustive enum
        raise InputValidationError(f"unsupported window preset {preset}")

    if w_end <= as_of:
        raise InputValidationError(
            "execution window lies entirely in the past",
            details={"window_end": w_end.isoformat(), "as_of": as_of.isoformat()},
        )
    if w_start < as_of:
        if preset in (WindowPreset.EXPLICIT, WindowPreset.TASK_PLANNED):
            warnings.append("WINDOW_START_CLAMPED_TO_NOW")
        w_start = as_of
    if w_end <= w_start:
        raise InputValidationError("execution window end must be after its start")
    return ExecutionWindow(start=w_start, end=w_end, preset=preset), warnings

# ===========================================================================
# Workforce Availability & Capacity Calculator
# ===========================================================================

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

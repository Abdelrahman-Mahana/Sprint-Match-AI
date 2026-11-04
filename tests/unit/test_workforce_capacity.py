from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.domain.entities.workforce import AttendanceRecord, ScheduleDay
from app.domain.enums import (
    AssignmentStatus,
    AttendanceStatus,
    AvailabilityStatus,
    EmploymentStatus,
    LeaveStatus,
    PresenceStatus,
    RemainingEffortBasis,
    WorkloadCertainty,
)
from app.intelligence.workforce.calendar import build_work_blocks, next_working_days
from app.intelligence.workforce.capacity import WorkforceCalculator, resolve_remaining_effort
from tests.fixtures.builders import (
    AS_OF,
    SUN_THU,
    assignment,
    checked_in_today,
    commitment,
    leave,
    local,
    standard_schedule,
    window,
    workforce,
)

WED, THU, SUN = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 8)
# Wed 00:00 -> Mon 00:00 covers Wed, Thu, (Fri, Sat weekend), Sun = 3 working days x 7h net.
W = window(local(WED), local(date(2026, 11, 9)))
calc = WorkforceCalculator()


def test_scheduled_hours_prorate_breaks_and_skip_weekend() -> None:
    r = calc.calculate(workforce(), W, as_of=AS_OF)
    assert r.scheduled_hours == 21.0
    assert r.available_working_hours == 21.0
    assert r.available_capacity_hours == 21.0
    assert r.status == AvailabilityStatus.AVAILABLE
    assert r.workload_certainty == WorkloadCertainty.CERTAIN


def test_partial_shift_prorates_break() -> None:
    r = calc.calculate(workforce(), window(local(WED, 13), local(WED, 17)), as_of=AS_OF)
    assert r.scheduled_hours == 3.5  # 4 gross hours * 7/8


def test_holidays_are_subtracted_from_available_hours() -> None:
    r = calc.calculate(workforce(), W, as_of=AS_OF, holidays={THU})
    assert r.scheduled_hours == 21.0
    assert r.holiday_hours == 7.0
    assert r.available_working_hours == 14.0


def test_approved_leave_subtracted_pending_leave_only_warned() -> None:
    data = workforce(
        leaves=(
            leave(local(WED), local(THU)),
            leave(local(SUN), local(date(2026, 11, 9)), LeaveStatus.REQUESTED),
            leave(local(THU), local(SUN), LeaveStatus.REJECTED),
        )
    )
    r = calc.calculate(data, W, as_of=AS_OF)
    assert r.leave_hours == 7.0
    assert r.pending_leave_hours == 7.0
    assert r.available_working_hours == 14.0
    assert "PENDING_LEAVE_IN_WINDOW" in r.warnings
    assert not r.fully_on_leave


def test_overlapping_leaves_are_not_double_counted() -> None:
    data = workforce(leaves=(leave(local(WED), local(THU, 12)), leave(local(WED, 12), local(THU))))
    r = calc.calculate(data, W, as_of=AS_OF)
    assert r.leave_hours == pytest.approx(7.0 + 3 * 7 / 8, abs=0.01)  # Wed + Thu 09-12, merged once


def test_leave_covering_whole_window_marks_fully_on_leave_unavailable() -> None:
    data = workforce(leaves=(leave(local(WED), local(date(2026, 11, 10))),))
    r = calc.calculate(data, W, as_of=AS_OF)
    assert r.fully_on_leave
    assert r.available_working_hours == 0
    assert r.status == AvailabilityStatus.UNAVAILABLE


def test_committed_work_uses_remaining_estimate_not_logged_time() -> None:
    a = assignment(
        remaining=10, planned_start=local(WED, 9), planned_end=local(SUN, 17), logged=30, allocated=40
    )
    r = calc.calculate(workforce(assignments=(a,)), W, as_of=AS_OF)
    assert r.committed_work_hours == 10.0  # logged hours are never subtracted
    assert r.available_capacity_hours == 11.0
    assert r.items[0].basis == RemainingEffortBasis.ASSIGNMENT_REMAINING_ESTIMATE


def test_remaining_effort_precedence() -> None:
    assert resolve_remaining_effort(assignment(remaining=5, task_remaining=9))[0] == 5
    v, basis, cert = resolve_remaining_effort(assignment(task_remaining=9))
    assert (v, basis, cert) == (
        9,
        RemainingEffortBasis.TASK_REMAINING_SOLE_ASSIGNEE,
        WorkloadCertainty.CERTAIN,
    )
    # Multi-assignee task: the task-level remaining must NOT be charged in full to every assignee.
    v, basis, cert = resolve_remaining_effort(assignment(task_remaining=9, assignees=2))
    assert (v, basis, cert) == (None, RemainingEffortBasis.UNKNOWN, WorkloadCertainty.UNCERTAIN)
    v, basis, cert = resolve_remaining_effort(assignment(allocated=16, logged=6, assignees=2))
    assert (v, basis, cert) == (10, RemainingEffortBasis.ALLOCATION_MINUS_LOGGED, WorkloadCertainty.ESTIMATED)
    assert resolve_remaining_effort(assignment(allocated=4, logged=6))[0] == 0


def test_unknown_remaining_effort_makes_availability_unknown_not_zero_workload() -> None:
    a = assignment(task_remaining=20, assignees=2, planned_start=local(WED), planned_end=local(SUN, 17))
    r = calc.calculate(workforce(assignments=(a,)), W, as_of=AS_OF)
    assert r.workload_certainty == WorkloadCertainty.UNCERTAIN
    assert r.status == AvailabilityStatus.UNKNOWN
    assert any(w.startswith("REMAINING_EFFORT_UNKNOWN") for w in r.warnings)


def test_assignment_spanning_beyond_window_is_prorated_by_working_time() -> None:
    # Planned Wed 09:00 -> next Wed 17:00: Wed, Thu, Sun, Mon, Tue, Wed = 6 working days (42h).
    a = assignment(remaining=42, planned_start=local(WED, 9), planned_end=local(date(2026, 11, 11), 17))
    r = calc.calculate(workforce(assignments=(a,)), W, as_of=AS_OF)
    assert r.committed_work_hours == 21.0  # 3 of 6 working days fall in the window


def test_assignment_outside_window_contributes_nothing_and_cancelled_ignored() -> None:
    later = assignment(
        remaining=30, planned_start=local(date(2026, 11, 15), 9), planned_end=local(date(2026, 11, 16), 17)
    )
    cancelled = assignment(remaining=30, planned_end=local(SUN, 17), status=AssignmentStatus.CANCELLED)
    r = calc.calculate(workforce(assignments=(later, cancelled)), W, as_of=AS_OF)
    assert r.committed_work_hours == 0


def test_overdue_assignment_is_fully_committed() -> None:
    a = assignment(
        remaining=6, planned_start=local(date(2026, 11, 1), 9), planned_end=local(date(2026, 11, 2), 17)
    )
    r = calc.calculate(workforce(assignments=(a,)), W, as_of=AS_OF)
    assert r.committed_work_hours == 6
    assert "OVERDUE_ASSIGNMENT:T-1" in r.warnings


def test_leave_compresses_assignment_into_remaining_working_days() -> None:
    # Employee is on leave Thu; assignment Wed..Sun (3 working days, one on leave) -> all 10h in window.
    a = assignment(remaining=10, planned_start=local(WED, 9), planned_end=local(SUN, 17))
    data = workforce(assignments=(a,), leaves=(leave(local(THU), local(date(2026, 11, 6))),))
    r = calc.calculate(data, W, as_of=AS_OF)
    assert r.committed_work_hours == 10
    assert r.available_capacity_hours == 4


def test_other_commitments_prorated_into_window() -> None:
    c = commitment(local(SUN), local(date(2026, 11, 13)), hours=10)  # 5 days, 1 inside the window
    r = calc.calculate(workforce(commitments=(c,)), W, as_of=AS_OF)
    assert r.other_commitment_hours == 2.0
    assert r.available_capacity_hours == 19.0


def test_present_but_fully_occupied_is_busy() -> None:
    a = assignment(remaining=30, planned_start=local(WED, 9), planned_end=local(SUN, 17))
    data = workforce(assignments=(a,), attendance=(checked_in_today(),))
    r = calc.calculate(data, W, as_of=AS_OF)
    assert r.presence == PresenceStatus.PRESENT
    assert r.status == AvailabilityStatus.BUSY
    assert r.available_capacity_hours == 0
    assert r.overcommitted_hours == 9.0


def test_absent_today_but_available_in_future_window() -> None:
    today_leave = leave(local(AS_OF.date()), local(WED))
    data = workforce(leaves=(today_leave,))
    today = calc.calculate(data, window(AS_OF, local(WED)), as_of=AS_OF)
    future = calc.calculate(data, W, as_of=AS_OF)
    assert today.presence == PresenceStatus.ON_LEAVE
    assert today.status == AvailabilityStatus.UNAVAILABLE
    assert future.presence == PresenceStatus.ON_LEAVE  # presence describes *now* only
    assert future.status == AvailabilityStatus.AVAILABLE


def test_partially_available_classification() -> None:
    a = assignment(remaining=14, planned_start=local(WED, 9), planned_end=local(SUN, 17))
    r = calc.calculate(workforce(assignments=(a,)), W, as_of=AS_OF)
    assert r.available_capacity_hours == 7
    assert r.status == AvailabilityStatus.PARTIALLY_AVAILABLE


def test_missing_schedule_is_unknown_and_inactive_is_unavailable() -> None:
    assert calc.calculate(workforce(schedule=()), W, as_of=AS_OF).status == AvailabilityStatus.UNKNOWN
    inactive = workforce(status=EmploymentStatus.INACTIVE)
    assert calc.calculate(inactive, W, as_of=AS_OF).status == AvailabilityStatus.UNAVAILABLE


def test_presence_states() -> None:
    def presence(**kw: object) -> PresenceStatus:
        return WorkforceCalculator.determine_presence(workforce(**kw), AS_OF, set())  # type: ignore[arg-type]

    assert presence() == PresenceStatus.NO_RECORD
    assert presence(attendance=(checked_in_today(),)) == PresenceStatus.PRESENT
    done = AttendanceRecord(
        work_date=AS_OF.date(),
        check_in=local(AS_OF.date(), 7),
        check_out=local(AS_OF.date(), 9, 30),
        status=AttendanceStatus.PRESENT,
    )
    assert presence(attendance=(done,)) == PresenceStatus.CHECKED_OUT
    absent = AttendanceRecord(work_date=AS_OF.date(), status=AttendanceStatus.ABSENT)
    assert presence(attendance=(absent,)) == PresenceStatus.ABSENT
    assert presence(schedule=standard_schedule((0,))) == PresenceStatus.NOT_SCHEDULED  # Tuesday not scheduled


def test_dst_transition_keeps_shift_length() -> None:
    # Europe/Berlin switches from CEST to CET on 2026-10-25 (a Sunday); Monday shift must stay 8h gross.
    sched = (ScheduleDay(weekday=0, start_time=time(9), end_time=time(17), break_minutes=0),)
    blocks = build_work_blocks(
        sched,
        "Europe/Berlin",
        datetime.fromisoformat("2026-10-24T00:00+02:00"),
        datetime.fromisoformat("2026-10-27T00:00+01:00"),
    )
    assert [round(b.net_hours, 4) for b in blocks] == [8.0]


def test_next_working_days_skip_weekend_and_holidays() -> None:
    days = next_working_days(WED, 3, SUN_THU, {SUN})
    assert days == [THU, date(2026, 11, 9), date(2026, 11, 10)]

"""Working-time arithmetic.

All arithmetic is performed in UTC to stay correct across DST transitions; schedules are expanded in the
employee's local timezone. Breaks are prorated: a block covering part of a shift contributes
``overlap_hours * (net_shift_hours / gross_shift_hours)``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.entities.workforce import ScheduleDay

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

"""Resolution of execution-window presets into concrete, timezone-aware intervals.

Day-based presets use the company calendar (timezone, working weekdays, holidays) and span whole local days;
actual working hours inside them come from each employee's schedule. Time that has already passed cannot be
used, so window starts earlier than ``as_of`` are clamped to ``as_of``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.exceptions import InputValidationError
from app.domain.entities.organization import Company, Sprint
from app.domain.entities.task import ExecutionWindow
from app.domain.enums import WindowPreset
from app.intelligence.workforce.calendar import next_working_days


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

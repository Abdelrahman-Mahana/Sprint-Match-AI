"""Faithful-to-foreign-source value coercion.

Values coming from an arbitrary database are strings, ints, bools or datetimes with conventions we
do not control ("Active", "ACTIVE", 1, "true", "2024-01-15", "15/01/2024"...). These helpers map them
onto the domain enums with documented, conservative defaults: an unmappable value becomes the safest
member (usually the one that does not grant capacity or capability) instead of raising.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

from app.domain.enums import (
    AssignmentStatus,
    AttendanceStatus,
    EmploymentStatus,
    LeaveStatus,
    LeaveType,
    SkillVerificationStatus,
    TaskCategory,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
)

E = TypeVar("E")

_TRUE = {"1", "true", "t", "yes", "y", "on", "active", "enabled"}


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any, default: int | None = None) -> int | None:
    f = as_float(value)
    return int(round(f)) if f is not None else default


def as_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in {"0", "false", "f", "no", "n", "off", "inactive", "disabled"}:
        return False
    return default


def as_text(value: Any, max_len: int | None = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len] if max_len else text


# ---------------------------------------------------------------------------
# Datetime handling (naive values are assumed to be UTC, which is safe for ordering)
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%Y%m%d",
    "%d.%m.%Y",
)

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M",
    "%Y/%m/%d %H:%M:%S",
)


def as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(text, fmt).date()
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    dt = as_datetime(value)
    return dt.date() if dt else None


def as_time(value: Any) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    text = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Enum coercion
# ---------------------------------------------------------------------------


def _enum_by_value(enum_cls: type[E], value: str) -> E | None:
    try:
        return enum_cls(value)  # type: ignore[call-arg]
    except ValueError:
        return None


def employment_status(value: Any) -> EmploymentStatus:
    text = (as_text(value, 40) or "").upper()
    if text in EmploymentStatus.__members__:
        return EmploymentStatus[text]
    low = text.lower()
    if low in {"active", "employed", "1", "true", "on"} or as_bool(value):
        return EmploymentStatus.ACTIVE
    if low in {"suspended", "on_hold", "hold", "leave", "on_leave", "maternity", "parental"}:
        return EmploymentStatus.SUSPENDED
    if low in {"inactive", "terminated", "left", "resigned", "dismissed", "retired", "archived"}:
        return EmploymentStatus.INACTIVE
    # Unknown text: never grant capacity on an interpretation we cannot justify.
    return EmploymentStatus.INACTIVE


_TASK_STATUS_ALIASES: dict[str, TaskStatus] = {
    "new": TaskStatus.BACKLOG,
    "open": TaskStatus.TODO,
    "to_do": TaskStatus.TODO,
    "todo": TaskStatus.TODO,
    "to do": TaskStatus.TODO,
    "ready": TaskStatus.TODO,
    "planned": TaskStatus.TODO,
    "in_progress": TaskStatus.IN_PROGRESS,
    "in progress": TaskStatus.IN_PROGRESS,
    "doing": TaskStatus.IN_PROGRESS,
    "in_review": TaskStatus.IN_REVIEW,
    "review": TaskStatus.IN_REVIEW,
    "testing": TaskStatus.IN_REVIEW,
    "done": TaskStatus.DONE,
    "closed": TaskStatus.DONE,
    "completed": TaskStatus.DONE,
    "finished": TaskStatus.DONE,
    "resolved": TaskStatus.DONE,
    "cancelled": TaskStatus.CANCELLED,
    "canceled": TaskStatus.CANCELLED,
    "rejected": TaskStatus.CANCELLED,
}


def task_status(value: Any) -> TaskStatus:
    text = (as_text(value, 40) or "").upper()
    if text in TaskStatus.__members__:
        return TaskStatus[text]
    return _TASK_STATUS_ALIASES.get(text.lower().replace("-", "_"), TaskStatus.TODO)


def task_priority(value: Any) -> TaskPriority | None:
    text = (as_text(value, 40) or "").lower()
    if not text:
        return None
    aliases = {
        "normal": TaskPriority.MEDIUM,
        "medium": TaskPriority.MEDIUM,
        "moderate": TaskPriority.MEDIUM,
        "high": TaskPriority.HIGH,
        "important": TaskPriority.HIGH,
        "urgent": TaskPriority.URGENT,
        "critical": TaskPriority.URGENT,
        "blocker": TaskPriority.URGENT,
        "highest": TaskPriority.URGENT,
        "low": TaskPriority.LOW,
        "minor": TaskPriority.LOW,
        "lowest": TaskPriority.LOW,
        "trivial": TaskPriority.LOW,
    }
    by_value = _enum_by_value(TaskPriority, text)
    return by_value or aliases.get(text)


def task_complexity(value: Any) -> TaskComplexity | None:
    text = (as_text(value, 40) or "").lower()
    if not text:
        return None
    # Numeric story points map onto the documented four levels.
    points = as_float(value)
    if points is not None and not any(c.isalpha() for c in text):
        if points <= 2:
            return TaskComplexity.LOW
        if points <= 5:
            return TaskComplexity.MEDIUM
        if points <= 13:
            return TaskComplexity.HIGH
        return TaskComplexity.CRITICAL
    aliases = {
        "trivial": TaskComplexity.LOW,
        "easy": TaskComplexity.LOW,
        "simple": TaskComplexity.LOW,
        "medium": TaskComplexity.MEDIUM,
        "normal": TaskComplexity.MEDIUM,
        "moderate": TaskComplexity.MEDIUM,
        "hard": TaskComplexity.HIGH,
        "high": TaskComplexity.HIGH,
        "complex": TaskComplexity.HIGH,
        "critical": TaskComplexity.CRITICAL,
        "very_high": TaskComplexity.CRITICAL,
        "blocker": TaskComplexity.CRITICAL,
    }
    return _enum_by_value(TaskComplexity, text) or aliases.get(text)


def task_category(value: Any) -> TaskCategory | None:
    text = (as_text(value, 60) or "").lower().replace("-", "_").replace(" ", "_")
    return _enum_by_value(TaskCategory, text)


def assignment_status(value: Any) -> AssignmentStatus:
    text = (as_text(value, 40) or "").upper()
    if text in AssignmentStatus.__members__:
        return AssignmentStatus[text]
    low = text.lower()
    if low in {"active", "in_progress", "doing", "started", "running", "open"}:
        return AssignmentStatus.ACTIVE
    if low in {"completed", "done", "closed", "finished", "delivered"}:
        return AssignmentStatus.COMPLETED
    if low in {"cancelled", "canceled", "rejected", "dropped"}:
        return AssignmentStatus.CANCELLED
    return AssignmentStatus.PLANNED


def leave_status(value: Any) -> LeaveStatus:
    text = (as_text(value, 40) or "").upper()
    if text in LeaveStatus.__members__:
        return LeaveStatus[text]
    low = text.lower()
    if low in {"approved", "accepted", "confirmed", "validate"}:
        return LeaveStatus.APPROVED
    if low in {"rejected", "refused", "denied"}:
        return LeaveStatus.REJECTED
    if low in {"cancelled", "canceled"}:
        return LeaveStatus.CANCELLED
    return LeaveStatus.REQUESTED


def leave_type(value: Any) -> LeaveType:
    text = (as_text(value, 40) or "").upper()
    if text in LeaveType.__members__:
        return LeaveType[text]
    low = text.lower()
    aliases = {
        "vacation": LeaveType.ANNUAL,
        "annual": LeaveType.ANNUAL,
        "holiday": LeaveType.ANNUAL,
        "paid": LeaveType.ANNUAL,
        "pto": LeaveType.ANNUAL,
        "sick": LeaveType.SICK,
        "sick_leave": LeaveType.SICK,
        "illness": LeaveType.SICK,
        "casual": LeaveType.CASUAL,
        "personal": LeaveType.CASUAL,
        "training": LeaveType.TRAINING,
        "education": LeaveType.TRAINING,
    }
    return aliases.get(low, LeaveType.OTHER)


def attendance_status(value: Any) -> AttendanceStatus:
    text = (as_text(value, 40) or "").upper()
    if text in AttendanceStatus.__members__:
        return AttendanceStatus[text]
    low = text.lower()
    if low in {"present", "in", "checked_in", "office"}:
        return AttendanceStatus.PRESENT
    if low in {"remote", "wfh", "telework", "home"}:
        return AttendanceStatus.REMOTE
    if low in {"leave", "on_leave", "vacation"}:
        return AttendanceStatus.ON_LEAVE
    return AttendanceStatus.ABSENT


def skill_verification(value: Any) -> SkillVerificationStatus:
    text = (as_text(value, 40) or "").upper()
    if text in SkillVerificationStatus.__members__:
        return SkillVerificationStatus[text]
    low = text.lower()
    if low in {"verified", "validated", "certified"}:
        return SkillVerificationStatus.VERIFIED
    if low in {"rejected", "invalid"}:
        return SkillVerificationStatus.REJECTED
    if low in {"pending", "pending_review", "in_review", "to_review"}:
        return SkillVerificationStatus.PENDING_REVIEW
    return SkillVerificationStatus.SELF_DECLARED


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

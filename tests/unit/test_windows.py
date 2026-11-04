from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.exceptions import InputValidationError
from app.domain.entities.organization import Sprint
from app.domain.enums import WindowPreset
from app.intelligence.workforce.windows import resolve_window
from tests.fixtures.builders import AS_OF, COMPANY, local


def test_today_and_remaining_day_start_now() -> None:
    w, warnings = resolve_window(WindowPreset.TODAY, as_of=AS_OF, company=COMPANY)
    assert w.start == AS_OF and w.end == local(date(2026, 11, 4))
    assert warnings == []
    w2, _ = resolve_window(WindowPreset.REMAINING_DAY, as_of=AS_OF, company=COMPANY)
    assert (w2.start, w2.end) == (w.start, w.end)


def test_next_working_day_skips_company_weekend() -> None:
    thursday = local(date(2026, 11, 5), 10)
    w, _ = resolve_window(WindowPreset.NEXT_WORKING_DAY, as_of=thursday, company=COMPANY)
    assert w.start == local(date(2026, 11, 8))  # Sunday (Fri/Sat off)
    assert w.end == local(date(2026, 11, 9))


def test_next_working_days_respects_holidays() -> None:
    w, _ = resolve_window(
        WindowPreset.NEXT_WORKING_DAYS,
        as_of=AS_OF,
        company=COMPANY,
        working_days=3,
        holidays={date(2026, 11, 5)},
    )
    # Wed 4, (Thu 5 holiday), (Fri/Sat), Sun 8, Mon 9
    assert w.start == local(date(2026, 11, 4)) and w.end == local(date(2026, 11, 10))


def test_current_sprint_clamped_to_now() -> None:
    sprint = Sprint(
        id=COMPANY.id,
        project_id=COMPANY.id,
        name="S",
        start_date=date(2026, 10, 27),
        end_date=date(2026, 11, 9),
        status="ACTIVE",
    )
    w, warnings = resolve_window(WindowPreset.CURRENT_SPRINT, as_of=AS_OF, company=COMPANY, sprint=sprint)
    assert w.start == AS_OF and w.end == local(date(2026, 11, 10))
    assert warnings == []


def test_explicit_window_validation() -> None:
    with pytest.raises(InputValidationError):
        resolve_window(WindowPreset.EXPLICIT, as_of=AS_OF, company=COMPANY, end=AS_OF - timedelta(hours=1))
    with pytest.raises(InputValidationError):
        resolve_window(WindowPreset.CURRENT_SPRINT, as_of=AS_OF, company=COMPANY)
    w, warnings = resolve_window(
        WindowPreset.EXPLICIT,
        as_of=AS_OF,
        company=COMPANY,
        start=AS_OF - timedelta(days=1),
        end=AS_OF + timedelta(days=2),
    )
    assert w.start == AS_OF and "WINDOW_START_CLAMPED_TO_NOW" in warnings

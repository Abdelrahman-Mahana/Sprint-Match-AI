from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from app.domain.entities.organization import Company
from app.domain.entities.task import ExecutionWindow
from app.domain.entities.workforce import AvailabilityResult, EmployeeWorkforceData
from app.domain.enums import WindowPreset
from app.domain.interfaces import UnitOfWork
from app.intelligence.workforce.capacity import WorkforceCalculator
from app.intelligence.workforce.windows import resolve_window
from app.schemas.recommendations import ExecutionWindowRequest

ATTENDANCE_LOOKBACK = timedelta(days=14)


class WorkforceService:
    def __init__(
        self, uow: UnitOfWork, calculator: WorkforceCalculator, default_horizon_days: int = 5
    ) -> None:
        self.uow = uow
        self.calculator = calculator
        self.default_horizon_days = default_horizon_days

    def holidays(self, company: Company, start: date, end: date) -> set[date]:
        return {h.holiday_date for h in self.uow.workforce.get_holidays(company.id, start, end)}

    def resolve_window(
        self,
        company: Company,
        as_of: datetime,
        request: ExecutionWindowRequest | None = None,
        *,
        planned_start: datetime | None = None,
        deadline: datetime | None = None,
        sprint_id: UUID | None = None,
    ) -> tuple[ExecutionWindow, list[str]]:
        """Window precedence: explicit request > requested preset > task plan > task sprint > default horizon."""
        holidays = self.holidays(
            company, as_of.date() - timedelta(days=1), as_of.date() + timedelta(days=400)
        )
        sprint = self.uow.tasks.get_sprint(company.id, sprint_id) if sprint_id else None
        if (
            request is not None
            and (request.start or request.deadline)
            and request.preset in (None, WindowPreset.EXPLICIT)
        ):
            return resolve_window(
                WindowPreset.EXPLICIT,
                as_of=as_of,
                company=company,
                holidays=holidays,
                start=request.start,
                end=request.deadline,
            )
        if request is not None and request.preset is not None:
            return resolve_window(
                request.preset,
                as_of=as_of,
                company=company,
                holidays=holidays,
                working_days=request.working_days or self.default_horizon_days,
                sprint=sprint,
                start=request.start or planned_start,
                end=request.deadline or deadline,
            )
        if deadline is not None:
            return resolve_window(
                WindowPreset.TASK_PLANNED,
                as_of=as_of,
                company=company,
                holidays=holidays,
                start=planned_start,
                end=deadline,
            )
        if sprint is not None:
            return resolve_window(
                WindowPreset.CURRENT_SPRINT, as_of=as_of, company=company, holidays=holidays, sprint=sprint
            )
        return resolve_window(
            WindowPreset.NEXT_WORKING_DAYS,
            as_of=as_of,
            company=company,
            holidays=holidays,
            working_days=self.default_horizon_days,
        )

    def workforce_data(
        self, company: Company, employee_ids: list[UUID], window: ExecutionWindow, as_of: datetime
    ) -> tuple[dict[UUID, EmployeeWorkforceData], set[date]]:
        data = self.uow.workforce.get_workforce_data(
            company.id,
            employee_ids,
            window_start=window.start,
            window_end=window.end,
            attendance_since=as_of - ATTENDANCE_LOOKBACK,
        )
        horizon = max(
            [window.end, *(a.planned_end for d in data.values() for a in d.assignments if a.planned_end)]
        )
        holidays = self.holidays(
            company, (as_of - ATTENDANCE_LOOKBACK).date(), horizon.date() + timedelta(days=1)
        )
        return data, holidays

    def availability(
        self, company: Company, employee_ids: list[UUID], window: ExecutionWindow, as_of: datetime
    ) -> dict[UUID, AvailabilityResult]:
        data, holidays = self.workforce_data(company, employee_ids, window, as_of)
        return self.calculate(data, window, as_of, holidays)

    def calculate(
        self,
        data: dict[UUID, EmployeeWorkforceData],
        window: ExecutionWindow,
        as_of: datetime,
        holidays: set[date],
    ) -> dict[UUID, AvailabilityResult]:
        return {
            eid: self.calculator.calculate(d, window, as_of=as_of, holidays=holidays)
            for eid, d in sorted(data.items(), key=lambda kv: str(kv[0]))
        }

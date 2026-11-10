from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.application.workforce import WorkforceService
from app.core.exceptions import NotFoundError
from app.domain.entities.base import DomainModel
from app.domain.entities.employee import Employee, EmployeeProfile
from app.domain.entities.organization import Company
from app.domain.entities.performance import PerformanceSummary, TaskOutcomeRecord
from app.domain.entities.task import ExecutionWindow
from app.domain.entities.workforce import Assignment, AvailabilityResult
from app.domain.enums import EmploymentStatus
from app.domain.interfaces import Page, UnitOfWork
from app.domain.policies import ScoringConfig
from app.intelligence.employee_profiles.skills import SkillAssessment, assess_skill
from app.intelligence.performance.metrics import summarize_performance
from app.intelligence.workforce.calendar import hours_between


class Employee360(DomainModel):
    """Task-independent 360 view. Contains no global ranking: suitability is always task-specific."""

    profile: EmployeeProfile
    skill_assessments: tuple[SkillAssessment, ...]
    performance: PerformanceSummary
    project_history: dict[UUID, int]
    weekly_scheduled_hours: float | None
    current_assignments: tuple[Assignment, ...]
    availability: AvailabilityResult


class EmployeePerformance(DomainModel):
    summary: PerformanceSummary
    outcomes: tuple[TaskOutcomeRecord, ...]


class EmployeeService:
    def __init__(self, uow: UnitOfWork, workforce: WorkforceService, config: ScoringConfig) -> None:
        self.uow = uow
        self.workforce = workforce
        self.config = config

    def list_employees(
        self,
        company: Company,
        *,
        status: EmploymentStatus | None = None,
        department_id: UUID | None = None,
        skill: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Employee]:
        return self.uow.employees.list_employees(
            company.id,
            status=status,
            department_id=department_id,
            skill_name=skill,
            limit=limit,
            offset=offset,
        )

    def get(self, company: Company, employee_id: UUID) -> Employee:
        employee = self.uow.employees.get_employee(company.id, employee_id)
        if employee is None:
            raise NotFoundError("Employee not found")
        return employee

    def update_employee(
        self,
        company: Company,
        employee_id: UUID,
        *,
        full_name: str | None = None,
        job_title: str | None = None,
        experience_years: float | None = None,
        employment_status: EmploymentStatus | None = None,
        email: str | None = None,
    ) -> Employee:
        self.get(company, employee_id)
        emp = self.uow.employees.update_employee(
            company.id,
            employee_id,
            full_name=full_name,
            job_title=job_title,
            experience_years=experience_years,
            employment_status=employment_status,
            email=email,
        )
        self.uow.commit()
        return emp

    def performance(self, company: Company, employee_id: UUID) -> EmployeePerformance:
        self.get(company, employee_id)
        outcomes = self.uow.performance.get_outcomes(company.id, [employee_id]).get(employee_id, [])
        return EmployeePerformance(
            summary=summarize_performance(employee_id, outcomes, self.config.performance),
            outcomes=tuple(outcomes),
        )

    def availability(
        self, company: Company, employee_id: UUID, window: ExecutionWindow, as_of: datetime
    ) -> AvailabilityResult:
        self.get(company, employee_id)
        return self.workforce.availability(company, [employee_id], window, as_of)[employee_id]

    def get_360(
        self, company: Company, employee_id: UUID, *, as_of: datetime, window: ExecutionWindow
    ) -> Employee360:
        profile = self.uow.employees.get_profiles(company.id, [employee_id]).get(employee_id)
        if profile is None:
            raise NotFoundError("Employee not found")
        data, holidays = self.workforce.workforce_data(company, [employee_id], window, as_of)
        wf = data[employee_id]
        availability = self.workforce.calculate(data, window, as_of, holidays)[employee_id]
        outcomes = self.uow.performance.get_outcomes(company.id, [employee_id]).get(employee_id, [])
        weekly = (
            round(
                sum(
                    hours_between(
                        datetime.combine(as_of.date(), d.start_time),
                        datetime.combine(as_of.date(), d.end_time),
                    )
                    - d.break_minutes / 60
                    for d in wf.schedule
                ),
                2,
            )
            if wf.schedule
            else None
        )
        return Employee360(
            profile=profile,
            skill_assessments=tuple(assess_skill(s, as_of, self.config.skills) for s in profile.skills),
            performance=summarize_performance(employee_id, outcomes, self.config.performance),
            project_history=self.uow.performance.get_project_history(company.id, [employee_id]).get(
                employee_id, {}
            ),
            weekly_scheduled_hours=weekly,
            current_assignments=wf.assignments,
            availability=availability,
        )

"""The deterministic recommendation steps, shared by the LangGraph nodes and the agent's ranking tools.

Each step is a thin coordination of repositories and the pure engines; there are no formulas here. The graph
runs the steps as separate nodes; ``evaluate`` chains them for tools that need a full, official ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.application.container import Services
from app.domain.entities.employee import EmployeeProfile
from app.domain.entities.organization import Company
from app.domain.entities.performance import TaskOutcomeRecord
from app.domain.entities.recommendation import EligibilityResult, EngineResult
from app.domain.entities.task import ExecutionWindow, TaskRequirements
from app.domain.entities.workforce import AvailabilityResult, EmployeeWorkforceData
from app.domain.enums import EmploymentStatus
from app.intelligence.recommendation.engine import CandidateInput


@dataclass(frozen=True)
class PerformanceData:
    outcomes: dict[UUID, tuple[TaskOutcomeRecord, ...]]
    project_history: dict[UUID, dict[UUID, int]]


class RecommendationPipeline:
    def __init__(self, services: Services, company: Company) -> None:
        self.s = services
        self.company = company

    def employee_ids(self) -> list[UUID]:
        return self.s.uow.employees.list_employee_ids(self.company.id, active_only=True)

    def profiles(self, ids: list[UUID]) -> dict[UUID, EmployeeProfile]:
        return self.s.uow.employees.get_profiles(self.company.id, ids)

    def workforce(
        self, ids: list[UUID], window: ExecutionWindow, as_of: datetime
    ) -> tuple[dict[UUID, EmployeeWorkforceData], set[date]]:
        return self.s.workforce.workforce_data(self.company, ids, window, as_of)

    def availability(
        self,
        data: dict[UUID, EmployeeWorkforceData],
        window: ExecutionWindow,
        as_of: datetime,
        holidays: set[date],
    ) -> dict[UUID, AvailabilityResult]:
        return self.s.workforce.calculate(data, window, as_of, holidays)

    def performance(self, ids: list[UUID]) -> PerformanceData:
        outcomes = self.s.uow.performance.get_outcomes(self.company.id, ids)
        return PerformanceData(
            outcomes={k: tuple(v) for k, v in outcomes.items()},
            project_history=self.s.uow.performance.get_project_history(self.company.id, ids),
        )

    @staticmethod
    def candidates(
        ids: list[UUID],
        profiles: dict[UUID, EmployeeProfile],
        availability: dict[UUID, AvailabilityResult],
        performance: PerformanceData | None,
    ) -> tuple[CandidateInput, ...]:
        return tuple(
            CandidateInput(
                profile=profiles[eid],
                availability=availability[eid],
                outcomes=performance.outcomes.get(eid, ()) if performance else (),
                project_history=performance.project_history.get(eid, {}) if performance else {},
            )
            for eid in ids
            if eid in profiles and eid in availability
        )

    def eligibility(
        self, requirements: TaskRequirements, candidates: tuple[CandidateInput, ...], as_of: datetime
    ) -> dict[UUID, EligibilityResult]:
        return {
            c.profile.employee.id: self.s.engine.check_eligibility(requirements, c, as_of) for c in candidates
        }

    def rank(
        self,
        requirements: TaskRequirements,
        candidates: tuple[CandidateInput, ...],
        eligibility: dict[UUID, EligibilityResult],
        as_of: datetime,
    ) -> EngineResult:
        engine = self.s.engine
        evaluations = [
            engine.score_candidate(requirements, c, eligibility[c.profile.employee.id], as_of)
            for c in sorted(candidates, key=lambda c: c.profile.employee.employee_code)
        ]
        active = [
            c.availability
            for c in candidates
            if c.profile.employee.employment_status == EmploymentStatus.ACTIVE
        ]
        return engine.rank(requirements, evaluations, active)

    def evaluate(
        self, requirements: TaskRequirements, window: ExecutionWindow, as_of: datetime
    ) -> EngineResult:
        """The complete official evaluation (same steps, same order as the graph)."""
        ids = self.employee_ids()
        profiles = self.profiles(ids)
        data, holidays = self.workforce(ids, window, as_of)
        availability = self.availability(data, window, as_of, holidays)
        base = self.candidates(ids, profiles, availability, None)
        eligibility = self.eligibility(requirements, base, as_of)
        perf = self.performance(ids) if any(r.eligible for r in eligibility.values()) else None
        return self.rank(requirements, self.candidates(ids, profiles, availability, perf), eligibility, as_of)

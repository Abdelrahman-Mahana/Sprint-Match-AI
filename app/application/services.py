"""Application use-case services, request context, DI container, and shared deterministic evaluation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core import InputValidationError, NotFoundError, Settings
from app.domain import (
    AvailabilityResult,
    Company,
    EligibilityResult,
    Employee,
    Employee360,
    EmployeePerformance,
    EmployeeProfile,
    EmployeeWorkforceData,
    EmploymentStatus,
    EngineResult,
    ExecutionWindow,
    NewTask,
    Page,
    RequiredSkill,
    RequirementSource,
    Role,
    ScoringConfig,
    SkillRequirementInput,
    TaskAnalysis,
    TaskInput,
    TaskOutcomeRecord,
    TaskRecord,
    TaskRequirements,
    TaskStatus,
    UnitOfWork,
    UnitOfWorkFactory,
    WindowPreset,
)
from app.infrastructure.discovery import SchemaMapping
from app.infrastructure.llm import LLMProvider
from app.intelligence.analyzer import ANALYZER_VERSION, TaskAnalyzer
from app.intelligence.capacity import (
    WorkforceCalculator,
    assess_skill,
    hours_between,
    resolve_window,
    summarize_performance,
)
from app.intelligence.scoring import CandidateInput, RecommendationEngine
from app.schemas import ExecutionWindowRequest

# ===========================================================================
# Request Context
# ===========================================================================

@dataclass(frozen=True)
class RequestContext:
    """Who is asking, for which tenant. Every service call is scoped to ``company``."""

    company: Company
    actor: str
    role: Role
    request_id: str

# ===========================================================================
# Employee Service
# ===========================================================================

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

# ===========================================================================
# Task Service
# ===========================================================================

def task_input_from_record(task: TaskRecord) -> TaskInput:
    return TaskInput(
        task_id=task.id,
        title=task.title,
        description=task.description,
        project_id=task.project_id,
        sprint_id=task.sprint_id,
        priority=task.priority,
        complexity=task.complexity,
        estimated_hours=task.remaining_estimated_hours or task.estimated_hours,
        planned_start=task.planned_start,
        deadline=task.deadline,
        task_category=task.task_category,
        acceptance_criteria=task.acceptance_criteria,
        required_skills=tuple(
            SkillRequirementInput(
                skill_name=s.skill_name,
                minimum_level=s.minimum_level,
                mandatory=s.mandatory,
                weight=s.weight,
                requires_verification=s.requires_verification,
            )
            for s in task.required_skills
        ),
        required_certifications=task.required_certifications,
        dependencies=tuple(d.task_code for d in task.dependencies),
        restricted_to_team_id=task.restricted_to_team_id,
        requires_project_membership=task.requires_project_membership,
        requirements_source=RequirementSource.TASK_RECORD,
    )


class TaskService:
    def __init__(self, uow: UnitOfWork, analyzer: TaskAnalyzer) -> None:
        self.uow = uow
        self.analyzer = analyzer

    def get(
        self, company: Company, *, task_id: UUID | None = None, task_code: str | None = None
    ) -> TaskRecord:
        task = (
            self.uow.tasks.get_task(company.id, task_id)
            if task_id
            else self.uow.tasks.get_task_by_code(company.id, task_code or "")
        )
        if task is None:
            raise NotFoundError("Task not found")
        return task

    def list(
        self,
        company: Company,
        *,
        status: TaskStatus | None = None,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[TaskRecord]:
        return self.uow.tasks.list_tasks(
            company.id, status=status, project_id=project_id, sprint_id=sprint_id, limit=limit, offset=offset
        )

    def create(self, company: Company, task_code: str, spec: TaskInput) -> TaskRecord:
        if spec.project_id and not self.uow.tasks.project_exists(company.id, spec.project_id):
            raise InputValidationError("Unknown project_id")
        catalog = {s.name.lower(): s for s in self.uow.tasks.get_skill_catalog(company.id)}
        skills = []
        for req in spec.required_skills:
            skill = catalog.get(req.skill_name.lower())
            if skill is None:
                raise InputValidationError(
                    f"Unknown skill {req.skill_name!r}", details={"skill": req.skill_name}
                )
            skills.append(
                RequiredSkill(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    minimum_level=req.minimum_level,
                    mandatory=req.mandatory,
                    weight=req.weight or (1.0 if req.mandatory else 0.5),
                    requires_verification=req.requires_verification,
                )
            )
        return self.uow.tasks.create_task(
            company.id,
            NewTask(
                task_code=task_code,
                title=spec.title,
                description=spec.description,
                project_id=spec.project_id,
                sprint_id=spec.sprint_id,
                complexity=spec.complexity.value if spec.complexity else None,
                priority=spec.priority.value if spec.priority else None,
                estimated_hours=spec.estimated_hours,
                planned_start=spec.planned_start,
                deadline=spec.deadline,
                task_category=spec.task_category.value if spec.task_category else None,
                acceptance_criteria=spec.acceptance_criteria,
                required_skills=tuple(skills),
                required_certifications=spec.required_certifications,
            ),
        )

    def analyze(self, company: Company, task: TaskInput, *, persist: bool = True) -> TaskAnalysis:
        analysis = self.analyzer.analyze(task, self.uow.tasks.get_skill_catalog(company.id))
        if persist:
            self.uow.recommendations.save_task_analysis(
                company.id, task.task_id, analysis.model_dump(mode="json"), ANALYZER_VERSION
            )
        return analysis

# ===========================================================================
# Workforce Service
# ===========================================================================

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

# ===========================================================================
# Application DI Container
# ===========================================================================

@dataclass(frozen=True)
class Services:
    uow: UnitOfWork
    workforce: WorkforceService
    employees: EmployeeService
    tasks: TaskService
    engine: RecommendationEngine


class AppContainer:
    def __init__(
        self,
        settings: Settings,
        uow_factory: UnitOfWorkFactory,
        llm: LLMProvider | None,
        config: ScoringConfig | None = None,
        *,
        internal_uow_factory: UnitOfWorkFactory | None = None,
        adapter_kind: str = "native",
        schema_mapping: SchemaMapping | None = None,
    ) -> None:
        self.settings = settings
        # Business-data source (may be an adapted external system).
        self.uow_factory = uow_factory
        # The application's own store (runs, audit, checkpoints). Same as the source when native.
        self.internal_uow_factory = internal_uow_factory or uow_factory
        self.adapter_kind = adapter_kind
        self.schema_mapping = schema_mapping
        self.llm = llm
        self.config = config or ScoringConfig()
        self.engine = RecommendationEngine(self.config)
        self.calculator = WorkforceCalculator(self.config.availability)
        self.analyzer = TaskAnalyzer(
            self.config, llm, accept_llm_estimates=settings.accept_llm_effort_estimates
        )
        # Used inside agent tools: deterministic only, so a tool can never trigger a nested LLM call.
        self.deterministic_analyzer = TaskAnalyzer(self.config, None)
        # Set by the composition root (create_app / scripts / tests): app.agents.runtime.AgentRuntime.
        self.agent_runtime: Any = None

    def services(self, uow: UnitOfWork) -> Services:
        workforce = WorkforceService(uow, self.calculator, self.settings.default_horizon_working_days)
        return Services(
            uow=uow,
            workforce=workforce,
            employees=EmployeeService(uow, workforce, self.config),
            tasks=TaskService(uow, self.analyzer),
            engine=self.engine,
        )

# ===========================================================================
# Deterministic Recommendation Pipeline
# ===========================================================================

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

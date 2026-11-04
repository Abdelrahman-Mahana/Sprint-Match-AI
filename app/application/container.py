"""Composition root: wires settings, data source, LLM provider and policies into per-request services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.employees import EmployeeService
from app.application.tasks import TaskService
from app.application.workforce import WorkforceService
from app.core.config import Settings
from app.domain.interfaces import UnitOfWork, UnitOfWorkFactory
from app.domain.policies import ScoringConfig
from app.infrastructure.llm.base import LLMProvider
from app.intelligence.recommendation.engine import RecommendationEngine
from app.intelligence.task_analysis.analyzer import TaskAnalyzer
from app.intelligence.workforce.capacity import WorkforceCalculator


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
    ) -> None:
        self.settings = settings
        self.uow_factory = uow_factory
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

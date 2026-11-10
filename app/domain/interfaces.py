"""Data-source boundary. Business logic depends only on these Protocols.

The PostgreSQL implementation lives in ``app.infrastructure.database.repositories``. A future Odoo 17 adapter
implements the same Protocols (see docs/ODOO_INTEGRATION.md) without touching engines, scoring or the graph.
"""

from __future__ import annotations

from datetime import date, datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from pydantic import Field

from app.core.exceptions import ErrorCategory
from app.domain.entities.base import DomainModel
from app.domain.entities.employee import Employee, EmployeeProfile, Skill
from app.domain.entities.organization import Company, Holiday, Sprint
from app.domain.entities.performance import (
    PerformanceEvaluation,
    PerformanceMetricSnapshot,
    TaskOutcomeRecord,
)
from app.domain.entities.recommendation import CandidateEvaluation
from app.domain.entities.task import RequiredSkill, TaskRecord
from app.domain.entities.workforce import EmployeeWorkforceData
from app.domain.enums import (
    AgentMode,
    DelayReason,
    EmploymentStatus,
    RecommendationStatus,
    TaskStatus,
    ToolExecutionStatus,
)


class Page[T](DomainModel):
    items: tuple[T, ...]
    total: int
    limit: int
    offset: int


class NewTask(DomainModel):
    task_code: str
    title: str
    description: str | None = None
    project_id: UUID | None = None
    sprint_id: UUID | None = None
    complexity: str | None = None
    priority: str | None = None
    estimated_hours: float | None = None
    planned_start: datetime | None = None
    deadline: datetime | None = None
    task_category: str | None = None
    acceptance_criteria: str | None = None
    restricted_to_team_id: UUID | None = None
    requires_project_membership: bool = False
    required_skills: tuple[RequiredSkill, ...] = ()
    required_certifications: tuple[str, ...] = ()


class NewTaskOutcome(DomainModel):
    task_id: UUID
    employee_id: UUID
    quality_score: float | None = Field(default=None, ge=0, le=100)
    accepted: bool | None = None
    rework_count: int = Field(default=0, ge=0)
    on_time: bool | None = None
    delay_reason: DelayReason | None = None
    blocker_hours: float = Field(default=0, ge=0)
    reviewer_id: UUID | None = None
    reviewed_at: datetime


class ExecutionTraceEntry(DomainModel):
    node_name: str
    execution_status: str  # ok | skipped | error
    duration_ms: int
    attempts: int = 1
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    llm_prompt_tokens: int | None = None
    llm_completion_tokens: int | None = None


class ToolExecutionRecord(DomainModel):
    """Sanitized record of one research-agent tool call (identifiers and counts only)."""

    sequence: int
    research_round: int
    tool_call_id: str
    tool_name: str
    status: ToolExecutionStatus
    arguments_summary: dict[str, Any] = Field(default_factory=dict)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    duration_ms: int = 0


class RunErrorRecord(DomainModel):
    node_name: str | None = None
    code: str
    category: ErrorCategory
    message: str
    retryable: bool = False


class RecommendationRunRecord(DomainModel):
    id: UUID
    company_id: UUID
    request_id: str
    task_id: UUID | None
    status: RecommendationStatus
    requested_by: str
    as_of: datetime
    window_start: datetime | None
    window_end: datetime | None
    task_analysis: dict[str, Any] | None
    algorithm_version: str
    scoring_config_fingerprint: str | None
    prompt_version: str | None
    llm_provider: str | None
    llm_model: str | None
    execution_time_ms: int
    explanation: str | None
    explanation_source: str | None
    warnings: tuple[str, ...]
    data_coverage: float | None
    primary_employee_id: UUID | None
    revalidated_from_id: UUID | None = None
    error_details: dict[str, Any] | None = None
    result_snapshot: dict[str, Any]
    candidates: tuple[CandidateEvaluation, ...] = ()
    trace: tuple[ExecutionTraceEntry, ...] = ()
    # --- agent execution metadata ---------------------------------------------------------------
    thread_id: str | None = None
    idempotency_key: str | None = None
    agent_mode: AgentMode | None = None
    research_summary: dict[str, Any] | None = None
    llm_calls: int = 0
    llm_prompt_tokens: int | None = None
    llm_completion_tokens: int | None = None
    tool_calls_count: int = 0
    error_category: ErrorCategory | None = None
    resumable: bool = False
    retry_of_id: UUID | None = None
    tool_executions: tuple[ToolExecutionRecord, ...] = ()
    errors: tuple[RunErrorRecord, ...] = ()


class StoredRecommendation(DomainModel):
    id: UUID
    company_id: UUID
    status: RecommendationStatus
    task_id: UUID | None
    generated_at: datetime
    result_snapshot: dict[str, Any]
    request_payload: dict[str, Any]
    request_id: str = ""
    requested_by: str = ""
    updated_at: datetime | None = None
    execution_time_ms: int = 0
    thread_id: str | None = None
    idempotency_key: str | None = None
    agent_mode: AgentMode | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_calls: int = 0
    llm_prompt_tokens: int | None = None
    llm_completion_tokens: int | None = None
    tool_calls_count: int = 0
    error_category: ErrorCategory | None = None
    resumable: bool = False
    retry_of_id: UUID | None = None


class EvaluationSummary(DomainModel):
    recommendations_by_status: dict[str, int]
    average_primary_coverage: float | None
    outcomes_recorded: int
    outcomes_for_recommended_primary: int
    average_quality_recommended_primary: float | None
    average_quality_other_assignees: float | None


class CompanyRepository(Protocol):
    def get_by_code(self, code: str) -> Company | None: ...
    def get(self, company_id: UUID) -> Company | None: ...


class EmployeeRepository(Protocol):
    def list_employees(
        self,
        company_id: UUID,
        *,
        status: EmploymentStatus | None = None,
        department_id: UUID | None = None,
        skill_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Employee]: ...

    def list_employee_ids(self, company_id: UUID, *, active_only: bool = False) -> list[UUID]: ...
    def get_employee(self, company_id: UUID, employee_id: UUID) -> Employee | None: ...
    def get_employee_by_code(self, company_id: UUID, employee_code: str) -> Employee | None: ...
    def search_employees(
        self,
        company_id: UUID,
        *,
        department: str | None = None,
        team: str | None = None,
        job_title: str | None = None,
        skill_names: tuple[str, ...] = (),
        status: EmploymentStatus | None = None,
        limit: int = 25,
    ) -> Page[Employee]: ...
    def get_profiles(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, EmployeeProfile]: ...
    def update_employee(
        self,
        company_id: UUID,
        employee_id: UUID,
        *,
        full_name: str | None = None,
        job_title: str | None = None,
        experience_years: float | None = None,
        employment_status: EmploymentStatus | None = None,
        email: str | None = None,
    ) -> Employee: ...


class TaskRepository(Protocol):
    def get_task(self, company_id: UUID, task_id: UUID) -> TaskRecord | None: ...
    def get_task_by_code(self, company_id: UUID, task_code: str) -> TaskRecord | None: ...
    def list_tasks(
        self,
        company_id: UUID,
        *,
        status: TaskStatus | None = None,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[TaskRecord]: ...
    def create_task(self, company_id: UUID, task: NewTask) -> TaskRecord: ...
    def get_skill_catalog(self, company_id: UUID) -> list[Skill]: ...
    def get_sprint(self, company_id: UUID, sprint_id: UUID) -> Sprint | None: ...
    def project_exists(self, company_id: UUID, project_id: UUID) -> bool: ...


class WorkforceRepository(Protocol):
    def get_workforce_data(
        self,
        company_id: UUID,
        employee_ids: list[UUID],
        *,
        window_start: datetime,
        window_end: datetime,
        attendance_since: datetime,
    ) -> dict[UUID, EmployeeWorkforceData]: ...

    def get_holidays(self, company_id: UUID, start: date, end: date) -> list[Holiday]: ...


class PerformanceRepository(Protocol):
    def get_outcomes(
        self, company_id: UUID, employee_ids: list[UUID]
    ) -> dict[UUID, list[TaskOutcomeRecord]]: ...

    def get_project_history(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, dict[UUID, int]]:
        """employee_id -> {project_id: number of completed assignments}."""
        ...

    def record_outcome(self, company_id: UUID, outcome: NewTaskOutcome) -> UUID: ...
    def evaluation_summary(self, company_id: UUID) -> EvaluationSummary: ...
    def get_evaluations(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 12
    ) -> list[PerformanceEvaluation]: ...
    def get_metric_snapshots(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 60
    ) -> list[PerformanceMetricSnapshot]: ...


class RecommendationRepository(Protocol):
    def save_run(self, run: RecommendationRunRecord, request_payload: dict[str, Any]) -> None:
        """Insert, or replace the stored run with the same id (a resumed run never creates a duplicate)."""
        ...

    def get_run(self, company_id: UUID, recommendation_id: UUID) -> StoredRecommendation | None: ...
    def find_by_idempotency_key(self, company_id: UUID, key: str) -> StoredRecommendation | None: ...
    def list_node_logs(self, company_id: UUID, recommendation_id: UUID) -> list[ExecutionTraceEntry]: ...
    def list_tool_executions(
        self, company_id: UUID, recommendation_id: UUID
    ) -> list[ToolExecutionRecord]: ...
    def list_run_errors(self, company_id: UUID, recommendation_id: UUID) -> list[RunErrorRecord]: ...
    def list_run_ids_before(self, cutoff: datetime) -> list[UUID]:
        """Maintenance (all tenants): runs last updated before ``cutoff`` (checkpoint retention)."""
        ...

    def save_task_analysis(
        self, company_id: UUID, task_id: UUID | None, analysis: dict[str, Any], analyzer_version: str
    ) -> UUID: ...


class AuditRepository(Protocol):
    def record(
        self,
        company_id: UUID | None,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class UnitOfWork(Protocol):
    """Transaction boundary bundling every repository of one data source."""

    companies: CompanyRepository
    employees: EmployeeRepository
    tasks: TaskRepository
    workforce: WorkforceRepository
    performance: PerformanceRepository
    recommendations: RecommendationRepository
    audit: AuditRepository

    def __enter__(self) -> Self: ...
    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def ping(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, *, read_only: bool = False, statement_timeout_ms: int | None = None) -> UnitOfWork:
        """``read_only``: the data source must reject writes; ``statement_timeout_ms``: server-side bound."""
        ...

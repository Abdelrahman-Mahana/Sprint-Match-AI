"""Stable recommendation request/response contract (also persisted as the immutable run snapshot)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.exceptions import categorize
from app.domain.entities.recommendation import CandidateEvaluation
from app.domain.entities.task import SkillRequirementInput, TaskInput
from app.domain.enums import (
    AgentMode,
    AvailabilityStatus,
    ConfidenceLevel,
    Criterion,
    PresenceStatus,
    RecommendationStatus,
    TaskCategory,
    TaskComplexity,
    TaskPriority,
    WindowPreset,
    WorkloadCertainty,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionWindowRequest(ApiModel):
    preset: WindowPreset | None = Field(
        default=None, description="Defaults to EXPLICIT when start/deadline are given, else the task's plan."
    )
    start: datetime | None = None
    deadline: datetime | None = None
    working_days: int | None = Field(default=None, ge=1, le=60, description="For NEXT_WORKING_DAYS")


class TaskSpecification(ApiModel):
    """Structured task input, for recommendations without a pre-existing database task."""

    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    project_id: UUID | None = None
    sprint_id: UUID | None = None
    priority: TaskPriority | None = None
    complexity: TaskComplexity | None = None
    estimated_hours: float | None = Field(default=None, gt=0, le=2000)
    planned_start: datetime | None = None
    deadline: datetime | None = None
    task_category: TaskCategory | None = None
    acceptance_criteria: str | None = Field(default=None, max_length=10_000)
    dependencies: list[str] = Field(default_factory=list, max_length=50)
    required_skills: list[SkillRequirementInput] = Field(default_factory=list, max_length=30)
    required_certifications: list[str] = Field(default_factory=list, max_length=10)

    def to_task_input(self) -> TaskInput:
        data = self.model_dump()
        data["dependencies"] = tuple(data["dependencies"])
        data["required_skills"] = tuple(self.required_skills)
        data["required_certifications"] = tuple(self.required_certifications)
        return TaskInput(**data)


class RecommendationRequest(ApiModel):
    task_id: UUID | None = None
    task_code: str | None = Field(default=None, max_length=32)
    task: TaskSpecification | None = None
    execution_window: ExecutionWindowRequest | None = None
    requested_by: str | None = Field(default=None, max_length=120)
    include_alternatives: bool = True
    max_alternatives: int = Field(default=3, ge=0, le=10)
    as_of: datetime | None = Field(
        default=None,
        description="Evaluation instant (timezone-aware). Defaults to now; fix it for reproducibility.",
    )

    @model_validator(mode="after")
    def _one_task_reference(self) -> RecommendationRequest:
        if sum(x is not None for x in (self.task_id, self.task_code, self.task)) != 1:
            raise ValueError("provide exactly one of task_id, task_code or task")
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone offset")
        return self


class ScoreComponentOut(ApiModel):
    criterion: Criterion
    raw_score: float | None
    score_used: float
    weight: float
    weighted_score: float
    has_evidence: bool
    applicable: bool
    evidence: list[str]


class ExclusionReasonOut(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateOut(ApiModel):
    employee_id: UUID
    employee_code: str
    name: str
    rank: int | None
    eligible: bool
    suitability_score: float | None
    availability_status: AvailabilityStatus
    presence: PresenceStatus
    available_hours: float
    workload_certainty: WorkloadCertainty
    confidence: float
    confidence_level: ConfidenceLevel
    data_coverage: float
    score_components: list[ScoreComponentOut]
    exclusion_reasons: list[ExclusionReasonOut]
    missing_information: list[str]
    warnings: list[str]

    @classmethod
    def from_evaluation(cls, e: CandidateEvaluation) -> CandidateOut:
        return cls(
            employee_id=e.employee_id,
            employee_code=e.employee_code,
            name=e.full_name,
            rank=e.rank,
            eligible=e.eligible,
            suitability_score=e.suitability_score,
            availability_status=e.availability_status,
            presence=e.presence,
            available_hours=e.available_capacity_hours,
            workload_certainty=e.workload_certainty,
            confidence=e.confidence,
            confidence_level=e.confidence_level,
            data_coverage=e.data_coverage,
            score_components=[
                ScoreComponentOut(
                    criterion=c.criterion,
                    raw_score=c.raw_score,
                    score_used=c.score_used,
                    weight=c.weight,
                    weighted_score=round(c.weighted_score, 2),
                    has_evidence=c.has_evidence,
                    applicable=c.applicable,
                    evidence=list(c.evidence),
                )
                for c in e.criteria
            ],
            exclusion_reasons=[
                ExclusionReasonOut(code=r.code.value, message=r.message, details=r.details)
                for r in e.exclusion_reasons
            ],
            missing_information=list(e.missing_information),
            warnings=list(e.warnings),
        )


class ExecutionWindowOut(ApiModel):
    start: datetime
    end: datetime
    preset: WindowPreset


class DataQualityOut(ApiModel):
    coverage: float
    candidates_evaluated: int
    eligible_candidates: int
    employees_with_unknown_availability: int
    employees_without_performance_history: int


class TraceEntryOut(ApiModel):
    node: str
    status: str
    duration_ms: int
    attempts: int
    error: str | None = None


class ErrorOut(ApiModel):
    code: str
    category: str = ""
    message: str
    node: str | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def _category(self) -> ErrorOut:
        if not self.category:
            self.category = categorize(self.code).value
        return self


class LLMUsageOut(ApiModel):
    calls: int
    prompt_tokens: int
    completion_tokens: int


class RevalidationOut(ApiModel):
    revalidated_from: UUID
    previous_primary_employee_id: UUID | None
    current_primary_employee_id: UUID | None
    still_valid: bool
    changes: list[str]


class RecommendationResponse(ApiModel):
    recommendation_id: UUID
    request_id: str
    status: RecommendationStatus
    task_id: UUID | None
    task_code: str | None
    task_title: str | None
    execution_window: ExecutionWindowOut | None
    primary_candidate: CandidateOut | None
    alternatives: list[CandidateOut]
    excluded_candidates: list[CandidateOut]
    explanation: str | None
    explanation_source: Literal["deterministic", "llm"] | None
    warnings: list[str]
    missing_information: list[str]
    task_analysis: dict[str, Any] | None
    data_quality: DataQualityOut | None
    algorithm_version: str
    scoring_config_fingerprint: str | None
    prompt_version: str | None
    llm_provider: str | None
    llm_model: str | None
    as_of: datetime
    execution_time_ms: int
    trace: list[TraceEntryOut]
    errors: list[ErrorOut]
    persisted: bool
    auto_assigned: Literal[False] = False
    revalidation: RevalidationOut | None = None
    # --- agent execution ------------------------------------------------------------------------------
    thread_id: str | None = None
    agent_mode: AgentMode | None = None
    research: dict[str, Any] | None = Field(
        default=None, description="Advisory research summary of the tool-using agent (never used for ranking)"
    )
    tool_calls: int = 0
    llm_usage: LLMUsageOut | None = None
    resumable: bool = Field(default=False, description="FAILED run that can be resumed from its checkpoint")
    retry_of_id: UUID | None = None
    idempotent_replay: bool = False

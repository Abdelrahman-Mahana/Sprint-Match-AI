"""Pydantic v2 API request/response schemas and agent tool output schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core import categorize
from app.domain import (
    AgentMode,
    AvailabilityResult,
    AvailabilityStatus,
    CandidateEvaluation,
    ConfidenceLevel,
    Criterion,
    DelayReason,
    Employee,
    Employee360,
    ExecutionTraceEntry,
    ExecutionWindow,
    PerformanceSummary,
    PresenceStatus,
    RecommendationStatus,
    RunErrorRecord,
    SkillRequirementInput,
    StoredRecommendation,
    TaskCategory,
    TaskComplexity,
    TaskInput,
    TaskPriority,
    TaskRecord,
    ToolExecutionRecord,
    WindowPreset,
    WorkloadCertainty,
)

# ===========================================================================
# Common API Schemas
# ===========================================================================

class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class EmployeePage(BaseModel):
    items: list[Employee]
    total: int
    limit: int
    offset: int


class TaskPage(BaseModel):
    items: list[TaskRecord]
    total: int
    limit: int
    offset: int


class Employee360Out(BaseModel):
    """Employee 360: professional profile, effective skills, evidence-based performance and current capacity."""

    data: Employee360
    window: ExecutionWindow


class AvailabilityOut(BaseModel):
    employee_id: UUID
    employee_code: str | None = None
    full_name: str | None = None
    availability: AvailabilityResult


class WorkforceAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_ids: list[UUID] | None = Field(default=None, max_length=500)
    as_of: datetime | None = None
    window_start: datetime | None = None
    window_deadline: datetime | None = None
    working_days: int | None = Field(default=None, ge=1, le=60)


class WorkforceAnalysisOut(BaseModel):
    window: ExecutionWindow
    warnings: list[str]
    employees: list[AvailabilityOut]
    status_counts: dict[str, int]


class PerformanceOut(BaseModel):
    summary: PerformanceSummary
    outcome_count: int


class TaskOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    employee_id: UUID
    quality_score: float | None = Field(default=None, ge=0, le=100)
    accepted: bool | None = None
    rework_count: int = Field(default=0, ge=0, le=100)
    on_time: bool | None = None
    delay_reason: DelayReason | None = None
    blocker_hours: float = Field(default=0, ge=0, le=1000)
    reviewed_at: datetime | None = None


class CreatedOut(BaseModel):
    id: UUID

# ===========================================================================
# Recommendation Schemas
# ===========================================================================

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

    title: str = Field(default="", max_length=300)
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
    auto_complete: bool = Field(
        default=False,
        description="When true, automatically infers missing title, effort, complexity, priority, category, and skills.",
    )

    @model_validator(mode="after")
    def _require_title_or_description(self) -> TaskSpecification:
        if not self.title.strip() and not (self.description or "").strip():
            raise ValueError("provide at least a task title or description")
        return self

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
    def from_evaluation(cls, e: CandidateEvaluation, *, is_arabic_task: bool = False) -> CandidateOut:
        from app.intelligence.scoring import translate_evidence_ar

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
                    evidence=[translate_evidence_ar(ev) for ev in c.evidence]
                    if is_arabic_task
                    else list(c.evidence),
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
    # agent execution
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

# ===========================================================================
# Agent Run Schemas
# ===========================================================================

class ApiOut(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRunOut(ApiOut):
    run_id: UUID
    request_id: str
    thread_id: str | None
    status: str
    task_id: UUID | None
    requested_by: str
    agent_mode: str | None
    llm_provider: str | None
    llm_model: str | None
    llm_usage: LLMUsageOut | None
    tool_calls: int
    execution_time_ms: int
    error_category: str | None
    resumable: bool
    retry_of_id: UUID | None
    idempotency_key: str | None
    generated_at: datetime
    updated_at: datetime | None
    primary_employee_code: str | None
    auto_assigned: bool = False

    @classmethod
    def from_stored(cls, s: StoredRecommendation) -> AgentRunOut:
        primary = s.result_snapshot.get("primary_candidate") or {}
        usage = (
            LLMUsageOut(
                calls=s.llm_calls,
                prompt_tokens=s.llm_prompt_tokens or 0,
                completion_tokens=s.llm_completion_tokens or 0,
            )
            if s.llm_calls or s.llm_prompt_tokens
            else None
        )
        return cls(
            run_id=s.id,
            request_id=s.request_id,
            thread_id=s.thread_id,
            status=s.status.value,
            task_id=s.task_id,
            requested_by=s.requested_by,
            agent_mode=s.agent_mode.value if s.agent_mode else None,
            llm_provider=s.llm_provider,
            llm_model=s.llm_model,
            llm_usage=usage,
            tool_calls=s.tool_calls_count,
            execution_time_ms=s.execution_time_ms,
            error_category=s.error_category.value if s.error_category else None,
            resumable=s.resumable,
            retry_of_id=s.retry_of_id,
            idempotency_key=s.idempotency_key,
            generated_at=s.generated_at,
            updated_at=s.updated_at,
            primary_employee_code=primary.get("employee_code"),
        )


class NodeLogOut(ApiOut):
    sequence: int
    node: str
    status: str
    duration_ms: int
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    llm_prompt_tokens: int | None
    llm_completion_tokens: int | None
    output_summary: dict[str, Any]
    error: str | None

    @classmethod
    def from_entry(cls, seq: int, e: ExecutionTraceEntry) -> NodeLogOut:
        return cls(
            sequence=seq,
            node=e.node_name,
            status=e.execution_status,
            duration_ms=e.duration_ms,
            attempts=e.attempts,
            started_at=e.started_at,
            finished_at=e.finished_at,
            llm_prompt_tokens=e.llm_prompt_tokens,
            llm_completion_tokens=e.llm_completion_tokens,
            output_summary=e.output_summary,
            error=e.error_message,
        )


class ToolExecutionOut(ApiOut):
    sequence: int
    research_round: int
    tool_call_id: str
    tool: str
    status: str
    arguments_summary: dict[str, Any]
    result_summary: dict[str, Any]
    error_code: str | None
    duration_ms: int

    @classmethod
    def from_record(cls, r: ToolExecutionRecord) -> ToolExecutionOut:
        return cls(
            sequence=r.sequence,
            research_round=r.research_round,
            tool_call_id=r.tool_call_id,
            tool=r.tool_name,
            status=r.status.value,
            arguments_summary=r.arguments_summary,
            result_summary=r.result_summary,
            error_code=r.error_code,
            duration_ms=r.duration_ms,
        )


class RunErrorOut(ApiOut):
    node: str | None
    code: str
    category: str
    message: str
    retryable: bool

    @classmethod
    def from_record(cls, r: RunErrorRecord) -> RunErrorOut:
        return cls(
            node=r.node_name, code=r.code, category=r.category.value, message=r.message, retryable=r.retryable
        )


class CheckpointOut(ApiOut):
    checkpoint_id: str | None
    step: int | None
    source: str | None
    next: list[str]
    created_at: str | None
    execution_status: str | None
    research_rounds: int | None
    tool_calls: int


class AgentHealthOut(ApiOut):
    status: str
    graph: dict[str, Any]
    tools: list[str]
    agent_mode: str
    agent_mode_reason: str | None
    capabilities: dict[str, Any] | None
    checkpointer: dict[str, Any]
    limits: dict[str, Any]


class LLMHealthOut(ApiOut):
    configured: bool
    provider: str | None = None
    model: str | None = None
    ok: bool | None = None
    latency_ms: int | None = None
    detail: str | None = None
    capabilities: dict[str, Any] | None = None

# ===========================================================================
# Agent Tool Schemas
# ===========================================================================

class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


# employees
class EmployeeSummaryOut(ToolOutput):
    employee_code: str
    name: str
    job_title: str
    department: str | None
    team: str | None
    employment_status: str
    experience_years: float | None


class SearchEmployeesOut(ToolOutput):
    total: int
    returned: int
    employees: list[EmployeeSummaryOut]


class SkillOut(ToolOutput):
    skill: str
    declared_level: int
    verification: str
    effective_level: float
    months_since_assessment: int | None
    stale: bool
    evidence_count: int = 0
    evidence_types: list[str] = Field(default_factory=list)


class CredentialOut(ToolOutput):
    name: str
    credential_type: str
    issuer: str | None
    verification: str
    issued_at: date | None
    expires_at: date | None
    valid_on_evaluation_date: bool


class ProjectRoleOut(ToolOutput):
    project: str
    role: str | None
    completed_tasks: int


class ToolPerformanceOut(ToolOutput):
    """Raw indicators with sample counts; ``None`` means no evidence (never zero)."""

    outcomes_reviewed: int
    quality_samples: int
    quality_mean: float | None
    acceptance_rate: float | None
    average_rework: float | None
    reliability_samples: int
    on_time_rate: float | None
    attributable_delays: int
    excused_delays: int


class ToolAvailabilityOut(ToolOutput):
    window_start: datetime
    window_end: datetime
    status: str
    presence: str
    scheduled_hours: float
    holiday_hours: float
    leave_hours: float
    pending_leave_hours: float
    available_working_hours: float
    committed_work_hours: float
    other_commitment_hours: float
    available_capacity_hours: float
    overcommitted_hours: float
    workload_certainty: str
    has_schedule: bool
    warnings: list[str]


class EmployeeProfileOut(ToolOutput):
    employee: EmployeeSummaryOut
    hire_date: date | None
    skills: list[SkillOut]
    certifications: list[CredentialOut]
    projects: list[ProjectRoleOut]
    performance: ToolPerformanceOut
    availability_in_window: ToolAvailabilityOut
    current_assignments: int


class EmployeeSkillsOut(ToolOutput):
    employee_code: str
    skills: list[SkillOut]


class ExperienceEntryOut(ToolOutput):
    organization_or_project: str
    role: str
    start_date: date
    end_date: date | None
    years: float
    skills: list[str]


class EmployeeExperienceOut(ToolOutput):
    employee_code: str
    experience_years: float | None
    experiences: list[ExperienceEntryOut]
    projects: list[ProjectRoleOut]
    certifications_and_training: list[CredentialOut]


class OutcomeOut(ToolOutput):
    task_code: str
    task_category: str | None
    complexity: str | None
    quality_score: float | None
    accepted: bool | None
    rework_count: int
    on_time: bool | None
    delay_reason: str | None
    reviewed_at: date | None


class EvaluationOut(ToolOutput):
    period_start: date
    period_end: date
    technical_quality: float | None
    delivery: float | None
    collaboration: float | None


class EmployeePerformanceOut(ToolOutput):
    employee_code: str
    filters: dict[str, Any]
    summary: ToolPerformanceOut
    recent_outcomes: list[OutcomeOut]
    periodic_reviews: list[EvaluationOut]
    note: str = "Periodic reviews are informational context; scores use reviewed task outcomes only."


# workforce
class AttendanceRecordOut(ToolOutput):
    work_date: date
    status: str
    check_in: datetime | None
    check_out: datetime | None


class AttendanceOut(ToolOutput):
    employee_code: str
    presence_at_evaluation_time: str
    start_date: date
    end_date: date
    records: list[AttendanceRecordOut]


class LeaveOut(ToolOutput):
    leave_type: str
    status: str
    start_at: datetime
    end_at: datetime


class LeavesOut(ToolOutput):
    employee_code: str
    start_date: date
    end_date: date
    leaves: list[LeaveOut]
    approved_leave_hours_in_range: float
    pending_leave_hours_in_range: float


class ScheduleDayOut(ToolOutput):
    weekday: str
    start_time: str
    end_time: str
    break_minutes: int


class ScheduleOut(ToolOutput):
    employee_code: str
    timezone: str
    has_schedule: bool
    weekly_schedule: list[ScheduleDayOut]
    start_date: date
    end_date: date
    holidays_in_range: list[date]
    scheduled_hours_in_range: float
    holiday_hours_in_range: float


class AssignmentOut(ToolOutput):
    task_code: str
    task_title: str
    status: str
    planned_start: datetime | None
    planned_end: datetime | None
    allocated_hours: float | None
    remaining_estimated_hours: float | None
    active_assignees: int
    logged_hours: float


class AssignmentsOut(ToolOutput):
    employee_code: str
    window_start: datetime
    window_end: datetime
    assignments: list[AssignmentOut]


class WorkloadItemOut(ToolOutput):
    task_code: str
    remaining_hours: float | None
    basis: str
    committed_hours_in_window: float
    certainty: str
    notes: list[str]


class WorkloadOut(ToolOutput):
    employee_code: str
    window_start: datetime
    window_end: datetime
    committed_work_hours: float
    other_commitment_hours: float
    workload_certainty: str
    items: list[WorkloadItemOut]
    unknown_remaining_effort_tasks: list[str]
    note: str = "Logged timesheet hours are never subtracted from capacity (no double counting)."


class CapacityOut(ToolOutput):
    employee_code: str
    window_start: datetime
    window_end: datetime
    scheduled_hours: float
    holiday_hours: float
    leave_hours: float
    available_working_hours: float
    committed_work_hours: float
    other_commitment_hours: float
    available_capacity_hours: float
    overcommitted_hours: float
    utilization: float | None
    formula: str = (
        "available_working_hours = scheduled - holiday - approved leave; "
        "available_capacity = max(0, available_working_hours - committed work - other commitments)"
    )


class EmployeeAvailabilityOut(ToolOutput):
    employee_code: str
    availability: ToolAvailabilityOut


# tasks
class RequiredSkillOut(ToolOutput):
    skill: str
    minimum_level: int
    mandatory: bool
    weight: float
    requires_verification: bool
    source: str


class DependencyOut(ToolOutput):
    task_code: str
    title: str
    status: str
    dependency_type: str


class SprintOut(ToolOutput):
    name: str
    start_date: date
    end_date: date
    status: str


class TaskDetailsOut(ToolOutput):
    task_code: str | None
    title: str
    status: str | None
    project: str | None
    sprint: SprintOut | None
    priority: str | None
    complexity: str | None
    task_category: str | None
    estimated_hours: float | None
    remaining_estimated_hours: float | None
    planned_start: datetime | None
    deadline: datetime | None
    required_skills: list[RequiredSkillOut]
    required_certifications: list[str]
    dependencies: list[DependencyOut]
    description_excerpt: str | None


class TaskSkillsOut(ToolOutput):
    task_code: str | None
    mandatory: list[RequiredSkillOut]
    optional: list[RequiredSkillOut]
    required_certifications: list[str]


class SimilarTaskOut(ToolOutput):
    task_code: str
    task_category: str | None
    complexity: str | None
    similarity: float
    completed_by: list[str]
    average_quality: float | None


class SimilarTasksOut(ToolOutput):
    criteria: dict[str, Any]
    results: list[SimilarTaskOut]


# recommendation
class ExclusionOut(ToolOutput):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class EligibilityOut(ToolOutput):
    employee_code: str
    task_code: str | None
    eligible: bool
    exclusion_reasons: list[ExclusionOut]
    warnings: list[str]


class CriterionOut(ToolOutput):
    criterion: str
    applicable: bool
    has_evidence: bool
    raw_score: float | None
    score_used: float
    weight: float
    weighted_score: float
    evidence: list[str]


class CandidateScoreOut(ToolOutput):
    employee_code: str
    task_code: str | None
    eligible: bool
    suitability_score: float | None
    data_coverage: float
    confidence: float
    confidence_level: str
    criteria: list[CriterionOut]
    exclusion_reasons: list[ExclusionOut]
    algorithm_version: str
    note: str = "Suitability score (0-100) of the deterministic engine; it is not a probability of success."


class RankedCandidateOut(ToolOutput):
    rank: int
    employee_code: str
    name: str
    suitability_score: float
    data_coverage: float
    confidence_level: str
    availability_status: str


class RankingOut(ToolOutput):
    task_code: str | None
    status: str
    algorithm_version: str
    ranked: list[RankedCandidateOut]
    eligible_count: int
    excluded_count: int
    exclusion_summary: dict[str, int]
    warnings: list[str]


class RecommendationEvidenceOut(ToolOutput):
    recommendation_id: str
    employee_code: str
    found: bool
    status: str | None = None
    rank: int | None = None
    eligible: bool | None = None
    suitability_score: float | None = None
    score_components: list[dict[str, Any]] = Field(default_factory=list)
    exclusion_reasons: list[dict[str, Any]] = Field(default_factory=list)


class ExclusionExplanationOut(ToolOutput):
    employee_code: str
    task_code: str | None
    eligible: bool
    reasons: list[ExclusionOut]
    summary: str

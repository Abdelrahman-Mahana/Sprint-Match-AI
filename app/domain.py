"""Domain layer: enums, immutable value objects/entities, versioned policies, and repository protocols."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from enum import StrEnum
from types import TracebackType
from typing import Any, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ===========================================================================
# Error Categories & Models
# ===========================================================================

class ErrorCategory(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    GRAPH_EXECUTION_ERROR = "GRAPH_EXECUTION_ERROR"
    INCOMPLETE_DATA = "INCOMPLETE_DATA"
    NO_SUITABLE_CANDIDATE = "NO_SUITABLE_CANDIDATE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"

# ===========================================================================
# Enums
# ===========================================================================

class Role(StrEnum):
    VIEWER = "viewer"
    MANAGER = "manager"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return {"viewer": 1, "manager": 2, "admin": 3}[self.value]


class EmploymentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class SkillVerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PENDING_REVIEW = "PENDING_REVIEW"
    SELF_DECLARED = "SELF_DECLARED"
    REJECTED = "REJECTED"


class EvidenceType(StrEnum):
    TASK_OUTCOME = "TASK_OUTCOME"
    CODE_REVIEW = "CODE_REVIEW"
    ASSESSMENT = "ASSESSMENT"
    CERTIFICATION = "CERTIFICATION"
    PROJECT_DELIVERY = "PROJECT_DELIVERY"


class CredentialType(StrEnum):
    CERTIFICATION = "CERTIFICATION"
    TRAINING = "TRAINING"


class ProjectStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"


class SprintStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class TaskStatus(StrEnum):
    BACKLOG = "BACKLOG"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskComplexity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def level(self) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskCategory(StrEnum):
    BACKEND_DEVELOPMENT = "backend_development"
    FRONTEND_DEVELOPMENT = "frontend_development"
    MOBILE_DEVELOPMENT = "mobile_development"
    DATA_ENGINEERING = "data_engineering"
    MACHINE_LEARNING = "machine_learning"
    DEVOPS = "devops"
    QA_TESTING = "qa_testing"
    DATABASE = "database"
    SECURITY = "security"
    OTHER = "other"


class AssignmentStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    @property
    def consumes_capacity(self) -> bool:
        return self in (AssignmentStatus.PLANNED, AssignmentStatus.ACTIVE)


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    REMOTE = "REMOTE"
    ABSENT = "ABSENT"
    ON_LEAVE = "ON_LEAVE"


class LeaveType(StrEnum):
    ANNUAL = "ANNUAL"
    CASUAL = "CASUAL"
    SICK = "SICK"
    TRAINING = "TRAINING"
    OTHER = "OTHER"


class LeaveStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class DelayReason(StrEnum):
    NONE = "NONE"
    EMPLOYEE = "EMPLOYEE"
    EXTERNAL_BLOCKER = "EXTERNAL_BLOCKER"
    DEPENDENCY = "DEPENDENCY"
    SCOPE_CHANGE = "SCOPE_CHANGE"
    UNSPECIFIED = "UNSPECIFIED"

    @property
    def attributable_to_employee(self) -> bool:
        # UNSPECIFIED is conservatively attributed to the employee (documented in SCORING.md).
        return self in (DelayReason.EMPLOYEE, DelayReason.UNSPECIFIED)


class PresenceStatus(StrEnum):
    PRESENT = "PRESENT"
    CHECKED_OUT = "CHECKED_OUT"
    ABSENT = "ABSENT"
    ON_LEAVE = "ON_LEAVE"
    NOT_SCHEDULED = "NOT_SCHEDULED"
    NO_RECORD = "NO_RECORD"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIALLY_AVAILABLE = "PARTIALLY_AVAILABLE"
    BUSY = "BUSY"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class WorkloadCertainty(StrEnum):
    CERTAIN = "CERTAIN"
    ESTIMATED = "ESTIMATED"
    UNCERTAIN = "UNCERTAIN"


class RemainingEffortBasis(StrEnum):
    ASSIGNMENT_REMAINING_ESTIMATE = "ASSIGNMENT_REMAINING_ESTIMATE"
    TASK_REMAINING_SOLE_ASSIGNEE = "TASK_REMAINING_SOLE_ASSIGNEE"
    ALLOCATION_MINUS_LOGGED = "ALLOCATION_MINUS_LOGGED"
    UNKNOWN = "UNKNOWN"


class WindowPreset(StrEnum):
    TODAY = "TODAY"
    REMAINING_DAY = "REMAINING_DAY"
    NEXT_WORKING_DAY = "NEXT_WORKING_DAY"
    NEXT_WORKING_DAYS = "NEXT_WORKING_DAYS"
    CURRENT_SPRINT = "CURRENT_SPRINT"
    TASK_PLANNED = "TASK_PLANNED"
    EXPLICIT = "EXPLICIT"


class RequirementSource(StrEnum):
    MANAGER = "MANAGER"
    TASK_RECORD = "TASK_RECORD"
    LLM_SUGGESTED = "LLM_SUGGESTED"
    TEXT_MATCH = "TEXT_MATCH"
    DEFAULT = "DEFAULT"


class Criterion(StrEnum):
    SKILL_MATCH = "SKILL_MATCH"
    EXPERIENCE = "EXPERIENCE"
    QUALITY = "QUALITY"
    RELIABILITY = "RELIABILITY"
    CAPACITY = "CAPACITY"
    COMPLEXITY_FIT = "COMPLEXITY_FIT"
    PROJECT_FAMILIARITY = "PROJECT_FAMILIARITY"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExclusionReason(StrEnum):
    EMPLOYEE_INACTIVE = "EMPLOYEE_INACTIVE"
    MANDATORY_SKILL_MISSING = "MANDATORY_SKILL_MISSING"
    MANDATORY_SKILL_BELOW_THRESHOLD = "MANDATORY_SKILL_BELOW_THRESHOLD"
    MANDATORY_SKILL_NOT_VERIFIED = "MANDATORY_SKILL_NOT_VERIFIED"
    NO_REQUIRED_SKILL_COVERAGE = "NO_REQUIRED_SKILL_COVERAGE"
    TEAM_ACCESS_REQUIRED = "TEAM_ACCESS_REQUIRED"
    PROJECT_ACCESS_REQUIRED = "PROJECT_ACCESS_REQUIRED"
    ON_LEAVE_DURING_WINDOW = "ON_LEAVE_DURING_WINDOW"
    NO_WORKING_TIME_IN_WINDOW = "NO_WORKING_TIME_IN_WINDOW"
    AVAILABILITY_UNKNOWN = "AVAILABILITY_UNKNOWN"
    INSUFFICIENT_AVAILABLE_CAPACITY = "INSUFFICIENT_AVAILABLE_CAPACITY"
    REQUIRED_CERTIFICATION_MISSING = "REQUIRED_CERTIFICATION_MISSING"

    @property
    def is_data_gap(self) -> bool:
        """True when exclusion is caused by missing data rather than a confirmed hard constraint."""
        return self is ExclusionReason.AVAILABILITY_UNKNOWN


class RecommendationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NEEDS_INPUT = "NEEDS_INPUT"
    NO_SUITABLE_CANDIDATE = "NO_SUITABLE_CANDIDATE"
    PARTIAL_DATA = "PARTIAL_DATA"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    FAILED = "FAILED"


class DependencyType(StrEnum):
    FINISH_TO_START = "FINISH_TO_START"
    RELATED = "RELATED"


class AgentMode(StrEnum):
    """How the workforce research agent talks to the model (resolved from provider capabilities)."""

    NATIVE = "native"  # provider-native tool calling, executed by LangGraph's ToolNode
    JSON_PLAN = "json_plan"  # validated JSON tool plan for models without native tool calling
    DISABLED = "disabled"  # no suitable model: deterministic pipeline only


class ToolExecutionStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    REJECTED = "rejected"  # unknown tool, malformed arguments, permission or budget violation
    DUPLICATE = "duplicate"  # identical call already executed in this run; cached result returned
    TIMEOUT = "timeout"

# ===========================================================================
# Base Model
# ===========================================================================

class DomainModel(BaseModel):
    """Immutable, validated domain value object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

# ===========================================================================
# Organization Entities
# ===========================================================================

class Company(DomainModel):
    id: UUID
    code: str
    name: str
    timezone: str
    # Python weekday numbers (Monday=0 ... Sunday=6) that are normally working days.
    working_weekdays: tuple[int, ...] = Field(min_length=1)


class Holiday(DomainModel):
    holiday_date: date
    name: str


class Sprint(DomainModel):
    id: UUID
    project_id: UUID
    name: str
    start_date: date
    end_date: date
    status: str

# ===========================================================================
# Employee Entities
# ===========================================================================

class Skill(DomainModel):
    id: UUID
    name: str
    category: str
    aliases: tuple[str, ...] = ()


class Employee(DomainModel):
    id: UUID
    company_id: UUID
    employee_code: str
    full_name: str
    email: str | None = None
    job_title: str
    department_id: UUID | None = None
    department_name: str | None = None
    team_id: UUID | None = None
    team_name: str | None = None
    hire_date: date | None = None
    experience_years: float | None = Field(default=None, ge=0)
    employment_status: EmploymentStatus
    timezone: str


class EmployeeSkill(DomainModel):
    skill_id: UUID
    skill_name: str
    proficiency_level: int = Field(ge=0, le=100)
    verification_status: SkillVerificationStatus
    verified_at: datetime | None = None
    last_assessed_at: datetime | None = None


class SkillEvidence(DomainModel):
    skill_id: UUID
    evidence_type: EvidenceType
    verification_status: SkillVerificationStatus
    project_id: UUID | None = None
    task_id: UUID | None = None
    notes: str | None = None
    created_at: datetime


class Experience(DomainModel):
    id: UUID
    organization_or_project: str
    role: str
    description: str | None = None
    start_date: date
    end_date: date | None = None
    skill_ids: tuple[UUID, ...] = ()
    skill_names: tuple[str, ...] = ()


class Credential(DomainModel):
    name: str
    issuer: str | None = None
    credential_type: CredentialType
    skill_id: UUID | None = None
    issued_at: date | None = None
    expires_at: date | None = None
    verification_status: SkillVerificationStatus


class ProjectMembership(DomainModel):
    project_id: UUID
    project_name: str
    role: str


class EmployeeProfile(DomainModel):
    """Task-independent professional profile (the data part of the Employee 360 view)."""

    employee: Employee
    skills: tuple[EmployeeSkill, ...] = ()
    evidence: tuple[SkillEvidence, ...] = ()
    experiences: tuple[Experience, ...] = ()
    credentials: tuple[Credential, ...] = ()
    memberships: tuple[ProjectMembership, ...] = ()

    def skill(self, skill_id: UUID) -> EmployeeSkill | None:
        return next((s for s in self.skills if s.skill_id == skill_id), None)

# ===========================================================================
# Task Entities
# ===========================================================================

class RequiredSkill(DomainModel):
    skill_id: UUID | None = None  # None = not (yet) resolved against the skill catalog
    skill_name: str
    minimum_level: int = Field(ge=0, le=100)
    mandatory: bool
    weight: float = Field(default=1.0, gt=0, le=10)
    requires_verification: bool = False
    source: RequirementSource = RequirementSource.MANAGER


class TaskDependencyRef(DomainModel):
    task_id: UUID
    task_code: str
    title: str
    status: TaskStatus
    dependency_type: DependencyType


class TaskRecord(DomainModel):
    """A task as stored in the data source."""

    id: UUID
    company_id: UUID
    task_code: str
    project_id: UUID | None = None
    project_name: str | None = None
    sprint_id: UUID | None = None
    title: str
    description: str | None = None
    complexity: TaskComplexity | None = None
    priority: TaskPriority | None = None
    status: TaskStatus
    estimated_hours: float | None = Field(default=None, ge=0)
    remaining_estimated_hours: float | None = Field(default=None, ge=0)
    planned_start: datetime | None = None
    deadline: datetime | None = None
    task_category: TaskCategory | None = None
    restricted_to_team_id: UUID | None = None
    requires_project_membership: bool = False
    acceptance_criteria: str | None = None
    required_skills: tuple[RequiredSkill, ...] = ()
    required_certifications: tuple[str, ...] = ()
    dependencies: tuple[TaskDependencyRef, ...] = ()


class SkillRequirementInput(DomainModel):
    skill_name: str = Field(min_length=1, max_length=100)
    minimum_level: int = Field(ge=0, le=100)
    mandatory: bool = True
    weight: float | None = Field(default=None, gt=0, le=10)
    requires_verification: bool = False


class TaskInput(DomainModel):
    """Task information as supplied for analysis: free text plus optional structured fields.

    Structured, manager-provided values always take priority over anything an LLM suggests.
    """

    task_id: UUID | None = None
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
    dependencies: tuple[str, ...] = ()
    required_skills: tuple[SkillRequirementInput, ...] = ()
    required_certifications: tuple[str, ...] = ()
    restricted_to_team_id: UUID | None = None
    requires_project_membership: bool = False
    requirements_source: RequirementSource = RequirementSource.MANAGER
    auto_complete: bool = False

    @model_validator(mode="after")
    def _require_title_or_description(self) -> TaskInput:
        if not self.title.strip() and not (self.description or "").strip():
            raise ValueError("provide at least a task title or description")
        return self

    def text_corpus(self) -> str:
        return "\n".join(p for p in (self.title, self.description or "", self.acceptance_criteria or "") if p)


class TaskRequirements(DomainModel):
    """Normalized, validated requirements consumed by the deterministic engines."""

    task_id: UUID | None = None
    title: str
    task_category: TaskCategory
    required_skills: tuple[RequiredSkill, ...]
    complexity: TaskComplexity
    complexity_source: RequirementSource
    estimated_hours: float = Field(gt=0)
    estimated_hours_source: RequirementSource
    priority: TaskPriority
    project_id: UUID | None = None
    restricted_to_team_id: UUID | None = None
    requires_project_membership: bool = False
    required_certifications: tuple[str, ...] = ()

    @property
    def mandatory_skills(self) -> tuple[RequiredSkill, ...]:
        return tuple(s for s in self.required_skills if s.mandatory)


class TaskAnalysis(DomainModel):
    """Outcome of task analysis. ``requirements`` is None when critical information is missing.

    status: READY (requirements usable), NEEDS_INPUT (critical information missing) or
    PROVIDER_UNAVAILABLE (the LLM was required to extract requirements but could not be used).
    """

    analyzer_version: str
    status: Literal["READY", "NEEDS_INPUT", "PROVIDER_UNAVAILABLE"]
    requirements: TaskRequirements | None
    missing_information: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    suggestions: dict[str, Any] = Field(default_factory=dict)
    analysis_confidence: float = Field(ge=0, le=1)
    llm_used: bool = False
    llm_error: str | None = None


class ExecutionWindow(DomainModel):
    start: datetime
    end: datetime
    preset: WindowPreset = WindowPreset.EXPLICIT

    @model_validator(mode="after")
    def _check(self) -> ExecutionWindow:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("execution window datetimes must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("execution window end must be after start")
        return self

# ===========================================================================
# Workforce Entities
# ===========================================================================

class ScheduleDay(DomainModel):
    weekday: int = Field(ge=0, le=6)  # Monday=0
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0, le=240)


class AttendanceRecord(DomainModel):
    work_date: date
    check_in: datetime | None = None
    check_out: datetime | None = None
    status: AttendanceStatus

    @model_validator(mode="after")
    def _check(self) -> AttendanceRecord:
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class LeaveRecord(DomainModel):
    id: UUID
    leave_type: LeaveType
    start_at: datetime
    end_at: datetime
    status: LeaveStatus


class Assignment(DomainModel):
    id: UUID
    task_id: UUID
    task_code: str
    task_title: str
    project_id: UUID | None = None
    assignment_status: AssignmentStatus
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    allocated_hours: float | None = Field(default=None, ge=0)
    remaining_estimated_hours: float | None = Field(default=None, ge=0)
    # Context needed for the documented remaining-effort fallbacks:
    task_remaining_estimated_hours: float | None = Field(default=None, ge=0)
    active_assignee_count: int = Field(default=1, ge=1)
    logged_hours: float = Field(default=0.0, ge=0)


class Commitment(DomainModel):
    """Other known, non-task commitments (support rotation, release duty, training...)."""

    id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    hours: float = Field(ge=0)


class EmployeeWorkforceData(DomainModel):
    employee_id: UUID
    timezone: str
    employment_status: EmploymentStatus
    schedule: tuple[ScheduleDay, ...] = ()
    attendance: tuple[AttendanceRecord, ...] = ()
    leaves: tuple[LeaveRecord, ...] = ()
    assignments: tuple[Assignment, ...] = ()
    commitments: tuple[Commitment, ...] = ()


class WorkloadItem(DomainModel):
    assignment_id: UUID
    task_code: str
    remaining_hours: float | None
    basis: RemainingEffortBasis
    committed_hours_in_window: float
    certainty: WorkloadCertainty
    notes: tuple[str, ...] = ()


class AvailabilityResult(DomainModel):
    """Deterministic capacity picture of one employee over one execution window.

    available_working_hours = scheduled_hours - holiday_hours - leave_hours
    available_capacity_hours = max(0, available_working_hours - committed_work_hours - other_commitment_hours)
    """

    employee_id: UUID
    window: ExecutionWindow
    presence: PresenceStatus
    scheduled_hours: float
    holiday_hours: float
    leave_hours: float
    pending_leave_hours: float
    available_working_hours: float
    committed_work_hours: float
    other_commitment_hours: float
    available_capacity_hours: float
    overcommitted_hours: float
    utilization: float | None
    status: AvailabilityStatus
    workload_certainty: WorkloadCertainty
    fully_on_leave: bool
    has_schedule: bool
    items: tuple[WorkloadItem, ...] = ()
    warnings: tuple[str, ...] = ()

# ===========================================================================
# Performance Entities
# ===========================================================================

class TaskOutcomeRecord(DomainModel):
    """Reviewed outcome of a completed task for one employee, with the task context needed for similarity."""

    task_id: UUID
    task_code: str
    project_id: UUID | None = None
    task_category: TaskCategory | None = None
    complexity: TaskComplexity | None = None
    required_skill_ids: tuple[UUID, ...] = ()
    quality_score: float | None = Field(default=None, ge=0, le=100)
    accepted: bool | None = None
    rework_count: int = Field(default=0, ge=0)
    on_time: bool | None = None
    delay_reason: DelayReason | None = None
    blocker_hours: float = Field(default=0, ge=0)
    reviewed_at: datetime | None = None


class PerformanceSummary(DomainModel):
    """Task-related performance evidence. Raw values are None when no evidence exists (never zero)."""

    employee_id: UUID
    outcome_count: int
    quality_sample_count: int
    raw_quality_mean: float | None
    acceptance_rate: float | None
    average_rework: float | None
    reliability_sample_count: int
    on_time_rate: float | None
    excused_delays: int
    attributable_delays: int
    similar_task_count: int = 0


class PerformanceEvaluation(DomainModel):
    """Periodic work-related review (informational; never a scoring input)."""

    period_start: date
    period_end: date
    technical_quality: float | None = Field(default=None, ge=0, le=100)
    delivery: float | None = Field(default=None, ge=0, le=100)
    collaboration: float | None = Field(default=None, ge=0, le=100)
    summary: str | None = None


class PerformanceMetricSnapshot(DomainModel):
    """Value of a delivery metric at the end of a period (trend reporting; never a scoring input)."""

    metric: str
    period_start: date
    period_end: date
    value: float | None
    sample_size: int

# ===========================================================================
# Recommendation Entities
# ===========================================================================

class ExclusionDetail(DomainModel):
    code: ExclusionReason
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class EligibilityResult(DomainModel):
    employee_id: UUID
    eligible: bool
    exclusion_reasons: tuple[ExclusionDetail, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def reason_codes(self) -> list[str]:
        return [r.code.value for r in self.exclusion_reasons]


class CriterionScore(DomainModel):
    criterion: Criterion
    applicable: bool
    has_evidence: bool
    raw_score: float | None = Field(default=None, ge=0, le=100)  # None when there is no evidence
    score_used: float = Field(ge=0, le=100)  # raw_score, or the documented neutral prior when missing
    weight: float = Field(ge=0, le=1)  # normalized weight actually applied (0 when not applicable)
    weighted_score: float
    evidence: tuple[str, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateEvaluation(DomainModel):
    employee_id: UUID
    employee_code: str
    full_name: str
    rank: int | None = None
    eligible: bool
    suitability_score: float | None = Field(default=None, ge=0, le=100)
    criteria: tuple[CriterionScore, ...] = ()
    exclusion_reasons: tuple[ExclusionDetail, ...] = ()
    availability_status: AvailabilityStatus
    presence: PresenceStatus
    available_capacity_hours: float
    workload_certainty: WorkloadCertainty
    data_coverage: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    confidence_level: ConfidenceLevel
    missing_information: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def criterion(self, criterion: Criterion) -> CriterionScore | None:
        return next((c for c in self.criteria if c.criterion == criterion), None)


class DataQuality(DomainModel):
    coverage: float = Field(ge=0, le=1)
    candidates_evaluated: int
    eligible_candidates: int
    employees_with_unknown_availability: int
    employees_without_performance_history: int
    notes: tuple[str, ...] = ()


class EngineResult(DomainModel):
    status: RecommendationStatus
    algorithm_version: str
    scoring_config_fingerprint: str
    ranked: tuple[CandidateEvaluation, ...]  # eligible candidates, best first
    excluded: tuple[CandidateEvaluation, ...]
    data_quality: DataQuality
    warnings: tuple[str, ...] = ()

    @property
    def primary(self) -> CandidateEvaluation | None:
        return self.ranked[0] if self.ranked else None

# ===========================================================================
# Versioned Business Policies
# ===========================================================================

ALGORITHM_VERSION = "sprintmatch-scoring-1.1.0"


class AvailabilityPolicy(DomainModel):
    # free_ratio = available_capacity_hours / available_working_hours
    available_min_free_ratio: float = Field(default=0.5, ge=0, le=1)
    busy_max_free_ratio: float = Field(default=0.15, ge=0, le=1)
    busy_max_free_hours: float = Field(default=2.0, ge=0)


class SkillPolicy(DomainModel):
    verification_factors: dict[SkillVerificationStatus, float] = Field(
        default_factory=lambda: {
            SkillVerificationStatus.VERIFIED: 1.0,
            SkillVerificationStatus.PENDING_REVIEW: 0.9,
            SkillVerificationStatus.SELF_DECLARED: 0.8,
            SkillVerificationStatus.REJECTED: 0.0,
        }
    )
    # (minimum age in months of the last assessment, multiplicative factor), checked from oldest first.
    staleness_steps: tuple[tuple[int, float], ...] = ((36, 0.7), (24, 0.85))
    # Score for a requirement that is exactly met; the remaining points reward surplus proficiency.
    met_base_score: float = 85.0
    optional_default_weight: float = 0.5
    mandatory_default_weight: float = 1.0


class ConfidencePolicy(DomainModel):
    certainty_factors: dict[str, float] = Field(
        default_factory=lambda: {"CERTAIN": 1.0, "ESTIMATED": 0.85, "UNCERTAIN": 0.6}
    )
    high_threshold: float = 0.8
    medium_threshold: float = 0.55


class EligibilityPolicy(DomainModel):
    # Capacity must cover estimated_hours * buffer.
    capacity_buffer: float = Field(default=1.0, ge=1.0, le=3.0)
    exclude_unknown_availability: bool = True


class PerformancePolicy(DomainModel):
    neutral_quality_prior: float = 60.0
    neutral_reliability_prior: float = 0.7
    shrinkage_samples: int = 3  # Bayesian pseudo-count pulling small samples toward the prior
    rework_penalty: float = 10.0
    rejected_quality_cap: float = 40.0
    similar_task_weight: float = 2.0
    similarity_threshold: float = 0.5


class ExperiencePolicy(DomainModel):
    similar_tasks_for_full_score: int = 5
    relevant_years_for_full_score: float = 5.0
    similar_tasks_share: float = 0.6  # rest is relevant years
    neutral_prior: float = 50.0  # used when the employee has neither task history nor experience records


class ComplexityPolicy(DomainModel):
    # seniority tier thresholds on professional experience years: tier = 1 + number of thresholds reached
    seniority_year_thresholds: tuple[float, ...] = (2.0, 5.0, 8.0)
    demonstrated_quality_threshold: float = 70.0
    underqualified_penalty_per_level: float = 35.0
    overqualified_score: float = 85.0  # tier exceeds requirement by 2+ levels (save seniors for hard tasks)
    neutral_prior: float = 60.0  # used when neither seniority nor demonstrated complexity is known


class ScoringConfig(DomainModel):
    weights: dict[Criterion, float] = Field(
        default_factory=lambda: {
            Criterion.SKILL_MATCH: 0.30,
            Criterion.EXPERIENCE: 0.15,
            Criterion.QUALITY: 0.15,
            Criterion.RELIABILITY: 0.10,
            Criterion.CAPACITY: 0.15,
            Criterion.COMPLEXITY_FIT: 0.10,
            Criterion.PROJECT_FAMILIARITY: 0.05,
        }
    )
    availability: AvailabilityPolicy = Field(default_factory=AvailabilityPolicy)
    skills: SkillPolicy = Field(default_factory=SkillPolicy)
    eligibility: EligibilityPolicy = Field(default_factory=EligibilityPolicy)
    performance: PerformancePolicy = Field(default_factory=PerformancePolicy)
    experience: ExperiencePolicy = Field(default_factory=ExperiencePolicy)
    complexity: ComplexityPolicy = Field(default_factory=ComplexityPolicy)
    confidence: ConfidencePolicy = Field(default_factory=ConfidencePolicy)
    # capacity ratio (capacity / required hours) at which the capacity score reaches 100
    capacity_full_score_ratio: float = Field(default=2.0, gt=1.0)
    close_candidates_margin: float = Field(default=2.0, ge=0)
    partial_data_coverage_threshold: float = Field(default=0.5, ge=0, le=1)
    default_min_level_by_complexity: dict[TaskComplexity, int] = Field(
        default_factory=lambda: {
            TaskComplexity.LOW: 40,
            TaskComplexity.MEDIUM: 55,
            TaskComplexity.HIGH: 70,
            TaskComplexity.CRITICAL: 80,
        }
    )

    @model_validator(mode="after")
    def _weights_valid(self) -> ScoringConfig:
        if set(self.weights) != set(Criterion):
            raise ValueError("weights must define every criterion exactly once")
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("weights must be non-negative")
        if abs(sum(self.weights.values()) - 1.0) > 1e-6:
            raise ValueError("weights must sum to 1.0")
        return self

    def fingerprint(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

# ===========================================================================
# Composite Domain Views
# ===========================================================================

class SkillAssessment(DomainModel):
    skill_id: UUID
    skill_name: str
    proficiency_level: int
    verification_status: SkillVerificationStatus
    verification_factor: float
    recency_factor: float
    effective_level: float
    months_since_assessment: int | None
    stale: bool

    def describe(self) -> str:
        parts = [
            f"{self.skill_name}: declared {self.proficiency_level}",
            self.verification_status.value.lower(),
        ]
        if self.stale:
            parts.append(f"last assessed {self.months_since_assessment} months ago")
        if self.effective_level != self.proficiency_level:
            parts.append(f"effective {self.effective_level:.1f}")
        return ", ".join(parts)


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

# ===========================================================================
# Repository & Store Protocols
# ===========================================================================

# ===========================================================================
# Interfaces & Protocols
# ===========================================================================

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
    # agent execution metadata
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

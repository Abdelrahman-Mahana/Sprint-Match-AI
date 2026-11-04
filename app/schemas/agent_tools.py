"""Strict output schemas of the research agent's tools.

Every tool result is validated against one of these models before it reaches the model. They contain
work-related facts only (no e-mail, no HR data) and identify employees by employee code.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- employees ---------------------------------------------------------------------------------------
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


class PerformanceOut(ToolOutput):
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


class AvailabilityOut(ToolOutput):
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
    performance: PerformanceOut
    availability_in_window: AvailabilityOut
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
    summary: PerformanceOut
    recent_outcomes: list[OutcomeOut]
    periodic_reviews: list[EvaluationOut]
    note: str = "Periodic reviews are informational context; scores use reviewed task outcomes only."


# --- workforce ---------------------------------------------------------------------------------------
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
    availability: AvailabilityOut


# --- tasks -------------------------------------------------------------------------------------------
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


# --- recommendation ----------------------------------------------------------------------------------
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

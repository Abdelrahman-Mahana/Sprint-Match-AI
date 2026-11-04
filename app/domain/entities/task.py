from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.entities.base import DomainModel
from app.domain.enums import (
    DependencyType,
    RequirementSource,
    TaskCategory,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
    WindowPreset,
)


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
    dependencies: tuple[str, ...] = ()
    required_skills: tuple[SkillRequirementInput, ...] = ()
    required_certifications: tuple[str, ...] = ()
    restricted_to_team_id: UUID | None = None
    requires_project_membership: bool = False
    requirements_source: RequirementSource = RequirementSource.MANAGER

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

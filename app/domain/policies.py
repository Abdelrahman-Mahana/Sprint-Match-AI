"""Configurable, versioned business policies. Every threshold used by the deterministic engines lives here.

Changing a value changes ``ScoringConfig.fingerprint()``, which is stored with every recommendation run so
historical results remain explainable.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import Field, model_validator

from app.domain.entities.base import DomainModel
from app.domain.enums import Criterion, SkillVerificationStatus, TaskComplexity

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

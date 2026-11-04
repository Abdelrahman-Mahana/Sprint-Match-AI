from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from app.domain.entities.base import DomainModel
from app.domain.enums import (
    AvailabilityStatus,
    ConfidenceLevel,
    Criterion,
    ExclusionReason,
    PresenceStatus,
    RecommendationStatus,
    WorkloadCertainty,
)


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

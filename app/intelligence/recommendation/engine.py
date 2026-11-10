"""Deterministic recommendation engine (independent of LangGraph, LLMs and the data source).

Pipeline: assess skills -> eligibility (hard constraints) -> criteria scores (soft) -> weighted ranking.
Same inputs always yield the same output: no randomness, stable sorting with explicit tie-breakers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.domain.entities.base import DomainModel
from app.domain.entities.employee import EmployeeProfile
from app.domain.entities.performance import TaskOutcomeRecord
from app.domain.entities.recommendation import (
    CandidateEvaluation,
    CriterionScore,
    DataQuality,
    EligibilityResult,
    EngineResult,
)
from app.domain.entities.task import TaskRequirements
from app.domain.entities.workforce import AvailabilityResult
from app.domain.enums import (
    AvailabilityStatus,
    ConfidenceLevel,
    Criterion,
    EmploymentStatus,
    RecommendationStatus,
    WorkloadCertainty,
)
from app.domain.policies import ALGORITHM_VERSION, ScoringConfig
from app.intelligence.employee_profiles.skills import SkillAssessment, assess_skill
from app.intelligence.performance.metrics import summarize_performance
from app.intelligence.recommendation import criteria
from app.intelligence.recommendation.eligibility import evaluate_eligibility


class CandidateInput(DomainModel):
    profile: EmployeeProfile
    availability: AvailabilityResult
    outcomes: tuple[TaskOutcomeRecord, ...] = ()
    project_history: dict[UUID, int] = Field(default_factory=dict)


class RecommendationEngine:
    def __init__(self, config: ScoringConfig | None = None) -> None:
        self.config = config or ScoringConfig()

    # --- building blocks (also used individually by the LangGraph nodes) ---------------------------
    def assess_skills(self, profile: EmployeeProfile, as_of: datetime) -> dict[UUID, SkillAssessment]:
        return {s.skill_id: assess_skill(s, as_of, self.config.skills) for s in profile.skills}

    def check_eligibility(
        self, requirements: TaskRequirements, candidate: CandidateInput, as_of: datetime
    ) -> EligibilityResult:
        return evaluate_eligibility(
            requirements,
            candidate.profile,
            candidate.availability,
            self.assess_skills(candidate.profile, as_of),
            self.config.eligibility,
            as_of.date(),
        )

    def score_candidate(
        self,
        requirements: TaskRequirements,
        candidate: CandidateInput,
        eligibility: EligibilityResult,
        as_of: datetime,
    ) -> CandidateEvaluation:
        cfg = self.config
        profile = candidate.profile
        assessments = self.assess_skills(profile, as_of)
        summary = summarize_performance(
            profile.employee.id, candidate.outcomes, cfg.performance, requirements
        )
        raw_scores = [
            criteria.score_skill_match(requirements, assessments, cfg),
            criteria.score_experience(requirements, profile, candidate.outcomes, as_of, cfg),
            criteria.score_quality(summary, cfg),
            criteria.score_reliability(summary, cfg),
            criteria.score_capacity(requirements, candidate.availability, cfg),
            criteria.score_complexity_fit(requirements, profile, candidate.outcomes, cfg),
            criteria.score_project_familiarity(requirements, profile, candidate.project_history),
        ]
        scored = self._apply_weights(raw_scores)
        applicable_weight = sum(c.weight for c in scored if c.applicable)
        coverage = (
            sum(c.weight for c in scored if c.applicable and c.has_evidence) / applicable_weight
            if applicable_weight
            else 0.0
        )
        certainty = candidate.availability.workload_certainty
        confidence = coverage * cfg.confidence.certainty_factors.get(certainty.value, 0.6)
        missing = [f"NO_EVIDENCE:{c.criterion.value}" for c in scored if c.applicable and not c.has_evidence]
        if certainty != WorkloadCertainty.CERTAIN:
            missing.append(f"WORKLOAD_{certainty.value}")
        final = round(sum(c.weighted_score for c in scored), 2) if eligibility.eligible else None
        return CandidateEvaluation(
            employee_id=profile.employee.id,
            employee_code=profile.employee.employee_code,
            full_name=profile.employee.full_name,
            eligible=eligibility.eligible,
            suitability_score=final,
            criteria=tuple(scored),
            exclusion_reasons=eligibility.exclusion_reasons,
            availability_status=candidate.availability.status,
            presence=candidate.availability.presence,
            available_capacity_hours=candidate.availability.available_capacity_hours,
            workload_certainty=certainty,
            data_coverage=round(coverage, 4),
            confidence=round(confidence, 4),
            confidence_level=self._confidence_level(confidence),
            missing_information=tuple(missing),
            warnings=tuple(dict.fromkeys(eligibility.warnings + candidate.availability.warnings)),
        )

    def rank(
        self,
        requirements: TaskRequirements,
        evaluations: Sequence[CandidateEvaluation],
        availabilities: Sequence[AvailabilityResult] = (),
    ) -> EngineResult:
        eligible = sorted(
            (e for e in evaluations if e.eligible),
            key=lambda e: (
                -(e.suitability_score or 0.0),
                -e.data_coverage,
                -e.available_capacity_hours,
                e.employee_code,
            ),
        )
        ranked = tuple(e.model_copy(update={"rank": i}) for i, e in enumerate(eligible, start=1))
        excluded = tuple(sorted((e for e in evaluations if not e.eligible), key=lambda e: e.employee_code))

        warnings: list[str] = []
        if len(ranked) >= 2 and (ranked[0].suitability_score or 0) - (ranked[1].suitability_score or 0) <= (
            self.config.close_candidates_margin
        ):
            warnings.append("CLOSE_CANDIDATES")
        if not ranked:
            max_working = max((a.available_working_hours for a in availabilities), default=0.0)
            if requirements.estimated_hours * self.config.eligibility.capacity_buffer > max_working:
                warnings.append("DEADLINE_INFEASIBLE")
            if any(
                e.exclusion_reasons and all(r.code.is_data_gap for r in e.exclusion_reasons) for e in excluded
            ):
                warnings.append("SOME_CANDIDATES_EXCLUDED_FOR_MISSING_DATA")
            # Intelligent nearest alternative discovery
            active_excluded = [
                e for e in excluded
                if e.availability_status != AvailabilityStatus.UNKNOWN
            ]
            if active_excluded:
                best_near = max(active_excluded, key=lambda e: sum(c.weighted_score for c in e.criteria))
                best_pts = round(sum(c.weighted_score for c in best_near.criteria), 1)
                missing = [r.message for r in best_near.exclusion_reasons]
                warnings.append(
                    f"NEAREST_MATCH_INSIGHT:{best_near.employee_code}:{best_near.full_name} (Fit: {best_pts}%, Gap: {'; '.join(missing[:2])})"
                )

        if not ranked:
            status = RecommendationStatus.NO_SUITABLE_CANDIDATE
        elif (
            ranked[0].data_coverage < self.config.partial_data_coverage_threshold
            or ranked[0].workload_certainty == WorkloadCertainty.UNCERTAIN
        ):
            status = RecommendationStatus.PARTIAL_DATA
        else:
            status = RecommendationStatus.SUCCESS

        quality = DataQuality(
            coverage=ranked[0].data_coverage if ranked else 0.0,
            candidates_evaluated=len(evaluations),
            eligible_candidates=len(ranked),
            employees_with_unknown_availability=sum(
                1 for e in evaluations if e.availability_status == AvailabilityStatus.UNKNOWN
            ),
            employees_without_performance_history=sum(
                1
                for e in evaluations
                if (q := e.criterion(Criterion.QUALITY)) is not None and not q.has_evidence
            ),
        )
        return EngineResult(
            status=status,
            algorithm_version=ALGORITHM_VERSION,
            scoring_config_fingerprint=self.config.fingerprint(),
            ranked=ranked,
            excluded=excluded,
            data_quality=quality,
            warnings=tuple(warnings),
        )

    # --- one-shot convenience -------------------------------------------------------------------
    def evaluate(
        self, requirements: TaskRequirements, candidates: Sequence[CandidateInput], as_of: datetime
    ) -> EngineResult:
        evaluations = []
        for c in sorted(candidates, key=lambda c: c.profile.employee.employee_code):
            eligibility = self.check_eligibility(requirements, c, as_of)
            evaluations.append(self.score_candidate(requirements, c, eligibility, as_of))
        active_availability = [
            c.availability
            for c in candidates
            if c.profile.employee.employment_status == EmploymentStatus.ACTIVE
        ]
        return self.rank(requirements, evaluations, active_availability)

    # --- helpers --------------------------------------------------------------------------------
    def _apply_weights(self, scores: list[CriterionScore]) -> list[CriterionScore]:
        """Normalize configured weights over *applicable* criteria (N/A criteria give their weight away)."""
        applicable_total = sum(self.config.weights[s.criterion] for s in scores if s.applicable)
        result = []
        for s in scores:
            w = (
                self.config.weights[s.criterion] / applicable_total
                if s.applicable and applicable_total
                else 0.0
            )
            result.append(
                s.model_copy(update={"weight": round(w, 6), "weighted_score": round(w * s.score_used, 4)})
            )
        return result

    def _confidence_level(self, confidence: float) -> ConfidenceLevel:
        p = self.config.confidence
        if confidence >= p.high_threshold:
            return ConfidenceLevel.HIGH
        if confidence >= p.medium_threshold:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

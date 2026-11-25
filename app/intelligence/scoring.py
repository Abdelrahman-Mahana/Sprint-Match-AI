"""Deterministic recommendation intelligence: hard eligibility gates, 7-criterion scoring, ranking, and grounded explanation generation."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.domain import (
    ALGORITHM_VERSION,
    AvailabilityResult,
    AvailabilityStatus,
    CandidateEvaluation,
    ConfidenceLevel,
    Criterion,
    CriterionScore,
    DataQuality,
    DomainModel,
    EligibilityPolicy,
    EligibilityResult,
    EmployeeProfile,
    EmploymentStatus,
    EngineResult,
    ExclusionDetail,
    ExclusionReason,
    PerformanceSummary,
    RecommendationStatus,
    ScoringConfig,
    SkillAssessment,
    SkillVerificationStatus,
    TaskComplexity,
    TaskOutcomeRecord,
    TaskRequirements,
    WorkloadCertainty,
)
from app.infrastructure.llm import LLMError, LLMProvider
from app.intelligence.analyzer import explanation_messages
from app.intelligence.capacity import assess_skill, shrink, summarize_performance, task_similarity

# ===========================================================================
# Hard Eligibility Gates
# ===========================================================================

def evaluate_eligibility(
    requirements: TaskRequirements,
    profile: EmployeeProfile,
    availability: AvailabilityResult,
    assessments: dict[UUID, SkillAssessment],
    policy: EligibilityPolicy,
    as_of: date | None = None,
) -> EligibilityResult:
    reasons: list[ExclusionDetail] = []
    warnings: list[str] = []
    employee = profile.employee

    if employee.employment_status != EmploymentStatus.ACTIVE:
        reasons.append(
            ExclusionDetail(
                code=ExclusionReason.EMPLOYEE_INACTIVE,
                message=f"Employment status is {employee.employment_status.value}",
            )
        )

    # Skills
    for req in requirements.mandatory_skills:
        a = assessments.get(req.skill_id) if req.skill_id else None
        if a is None or a.effective_level <= 0:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.MANDATORY_SKILL_MISSING,
                    message=f"Missing mandatory skill {req.skill_name}",
                    details={"skill": req.skill_name, "required_level": req.minimum_level},
                )
            )
            continue
        if a.effective_level < req.minimum_level:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.MANDATORY_SKILL_BELOW_THRESHOLD,
                    message=(
                        f"{req.skill_name} effective level {a.effective_level:.1f} is below the required "
                        f"{req.minimum_level}"
                    ),
                    details={
                        "skill": req.skill_name,
                        "required_level": req.minimum_level,
                        "declared_level": a.proficiency_level,
                        "effective_level": a.effective_level,
                        "verification_status": a.verification_status.value,
                        "stale": a.stale,
                    },
                )
            )
        if req.requires_verification and a.verification_status != SkillVerificationStatus.VERIFIED:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.MANDATORY_SKILL_NOT_VERIFIED,
                    message=f"{req.skill_name} must be verified for this task",
                    details={"skill": req.skill_name, "verification_status": a.verification_status.value},
                )
            )

    if not requirements.mandatory_skills and requirements.required_skills:
        covered = any(
            (a := assessments.get(r.skill_id)) is not None and a.effective_level >= r.minimum_level
            for r in requirements.required_skills
            if r.skill_id
        )
        if not covered:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.NO_REQUIRED_SKILL_COVERAGE,
                    message="Does not meet the minimum level of any required skill",
                )
            )

    # Access
    if requirements.restricted_to_team_id and employee.team_id != requirements.restricted_to_team_id:
        reasons.append(
            ExclusionDetail(
                code=ExclusionReason.TEAM_ACCESS_REQUIRED, message="Task is restricted to another team"
            )
        )
    if (
        requirements.requires_project_membership
        and requirements.project_id
        and not any(m.project_id == requirements.project_id for m in profile.memberships)
    ):
        reasons.append(
            ExclusionDetail(
                code=ExclusionReason.PROJECT_ACCESS_REQUIRED, message="Not a member of the task's project"
            )
        )

    # Required certifications (valid on the evaluation date, not rejected)
    for name in requirements.required_certifications:
        held = [
            c
            for c in profile.credentials
            if c.name.strip().lower() == name.strip().lower()
            and c.verification_status != SkillVerificationStatus.REJECTED
            and (as_of is None or c.expires_at is None or c.expires_at >= as_of)
        ]
        if not held:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.REQUIRED_CERTIFICATION_MISSING,
                    message=f"Required certification {name} is missing, expired or rejected",
                    details={"certification": name},
                )
            )

    # Availability / capacity
    required_hours = requirements.estimated_hours * policy.capacity_buffer
    if employee.employment_status == EmploymentStatus.ACTIVE:
        if availability.fully_on_leave:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.ON_LEAVE_DURING_WINDOW,
                    message="Approved leave covers all working time in the execution window",
                    details={"leave_hours": availability.leave_hours},
                )
            )
        elif availability.status == AvailabilityStatus.UNAVAILABLE:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.NO_WORKING_TIME_IN_WINDOW,
                    message="No working time available in the execution window",
                )
            )
        elif availability.status == AvailabilityStatus.BUSY:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.INSUFFICIENT_AVAILABLE_CAPACITY,
                    message=(
                        f"Employee is busy/fully committed ({availability.committed_work_hours + availability.other_commitment_hours:.1f}h committed, "
                        f"{availability.available_capacity_hours:.1f}h free)"
                    ),
                    details={
                        "available_capacity_hours": availability.available_capacity_hours,
                        "required_hours": round(required_hours, 2),
                        "committed_work_hours": availability.committed_work_hours
                        + availability.other_commitment_hours,
                    },
                )
            )
        elif availability.status == AvailabilityStatus.UNKNOWN and policy.exclude_unknown_availability:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.AVAILABILITY_UNKNOWN,
                    message="Capacity cannot be determined from the available data (data gap, not a rejection)",
                    details={
                        "has_schedule": availability.has_schedule,
                        "workload_certainty": availability.workload_certainty.value,
                    },
                )
            )
        elif availability.available_capacity_hours + 1e-6 < required_hours:
            reasons.append(
                ExclusionDetail(
                    code=ExclusionReason.INSUFFICIENT_AVAILABLE_CAPACITY,
                    message=(
                        f"Available capacity {availability.available_capacity_hours:.1f}h is below the required "
                        f"{required_hours:.1f}h"
                    ),
                    details={
                        "available_capacity_hours": availability.available_capacity_hours,
                        "required_hours": round(required_hours, 2),
                        "committed_work_hours": availability.committed_work_hours,
                        "leave_hours": availability.leave_hours,
                    },
                )
            )

    if availability.pending_leave_hours > 0:
        warnings.append("PENDING_LEAVE_IN_WINDOW")
    return EligibilityResult(
        employee_id=employee.id,
        eligible=not reasons,
        exclusion_reasons=tuple(reasons),
        warnings=tuple(warnings),
    )

# ===========================================================================
# 7 Soft Ranking Criteria
# ===========================================================================

def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score(
    criterion: Criterion,
    raw: float | None,
    prior: float,
    *,
    evidence: Sequence[str] = (),
    details: dict[str, object] | None = None,
    applicable: bool = True,
) -> CriterionScore:
    used = _clamp(raw) if raw is not None else prior
    return CriterionScore(
        criterion=criterion,
        applicable=applicable,
        has_evidence=raw is not None,
        raw_score=round(_clamp(raw), 2) if raw is not None else None,
        score_used=round(used, 2),
        weight=0.0,
        weighted_score=0.0,
        evidence=tuple(evidence),
        details=details or {},
    )


# 1. Skill match
def skill_requirement_score(effective: float, minimum: int, met_base: float) -> float:
    """met exactly -> met_base; surplus up to 100 -> up to 100; below minimum -> proportional share of met_base."""
    if minimum <= 0:
        return met_base + (100 - met_base) * (effective / 100) if effective > 0 else 0.0
    coverage = min(effective / minimum, 1.0)
    if coverage < 1.0:
        return met_base * coverage
    surplus = (effective - minimum) / (100 - minimum) if minimum < 100 else 0.0
    return met_base + (100 - met_base) * min(max(surplus, 0.0), 1.0)


def score_skill_match(
    requirements: TaskRequirements, assessments: dict[UUID, SkillAssessment], config: ScoringConfig
) -> CriterionScore:
    total_w = 0.0
    total = 0.0
    evidence: list[str] = []
    per_skill: list[dict[str, object]] = []
    for req in requirements.required_skills:
        a = assessments.get(req.skill_id) if req.skill_id else None
        effective = a.effective_level if a else 0.0
        s = skill_requirement_score(effective, req.minimum_level, config.skills.met_base_score)
        total += req.weight * s
        total_w += req.weight
        kind = "mandatory" if req.mandatory else "optional"
        if a:
            evidence.append(f"{a.describe()} (required {req.minimum_level}, {kind})")
        else:
            evidence.append(f"{req.skill_name}: not in profile (required {req.minimum_level}, {kind})")
        per_skill.append(
            {
                "skill": req.skill_name,
                "mandatory": req.mandatory,
                "required_level": req.minimum_level,
                "effective_level": effective,
                "score": round(s, 2),
            }
        )
    raw = total / total_w if total_w else 0.0
    # Skill requirements are always evaluable: lacking a skill is real evidence, not missing data.
    return _score(Criterion.SKILL_MATCH, raw, 0.0, evidence=evidence, details={"skills": per_skill})


# 2. Relevant experience
def _relevant_years(profile: EmployeeProfile, skill_ids: set[UUID], as_of: date) -> float:
    intervals = sorted(
        (e.start_date, e.end_date or as_of)
        for e in profile.experiences
        if skill_ids & set(e.skill_ids) and (e.end_date or as_of) > e.start_date
    )
    total_days = 0
    cur_s: date | None = None
    cur_e: date | None = None
    for s, e in intervals:
        if cur_e is None or s > cur_e:
            if cur_s is not None and cur_e is not None:
                total_days += (cur_e - cur_s).days
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_s is not None and cur_e is not None:
        total_days += (cur_e - cur_s).days
    return total_days / 365.25


def score_experience(
    requirements: TaskRequirements,
    profile: EmployeeProfile,
    outcomes: Sequence[TaskOutcomeRecord],
    as_of: datetime,
    config: ScoringConfig,
) -> CriterionScore:
    p = config.experience
    skill_ids = {s.skill_id for s in requirements.required_skills if s.skill_id}
    has_history = bool(outcomes)
    has_records = bool(profile.experiences)
    if not has_history and not has_records:
        return _score(
            Criterion.EXPERIENCE,
            None,
            p.neutral_prior,
            evidence=["No task history or experience records available"],
        )
    similar = [
        o for o in outcomes if task_similarity(o, requirements) >= config.performance.similarity_threshold
    ]
    years = _relevant_years(profile, skill_ids, as_of.date()) if has_records else None
    sim_component = min(len(similar) / p.similar_tasks_for_full_score, 1.0) * 100 if has_history else None
    years_component = min(years / p.relevant_years_for_full_score, 1.0) * 100 if years is not None else None
    raw: float | None
    if sim_component is not None and years_component is not None:
        raw = p.similar_tasks_share * sim_component + (1 - p.similar_tasks_share) * years_component
    else:
        raw = sim_component if sim_component is not None else years_component
    evidence = []
    if has_history:
        evidence.append(f"{len(similar)} completed similar task(s) out of {len(outcomes)} reviewed")
    if years is not None:
        evidence.append(f"{years:.1f} year(s) of experience with the required technologies")
    return _score(
        Criterion.EXPERIENCE,
        raw,
        p.neutral_prior,
        evidence=evidence,
        details={
            "similar_task_count": len(similar),
            "similar_task_codes": [o.task_code for o in similar][:10],
            "relevant_years": round(years, 2) if years is not None else None,
        },
    )


# 3. Historical quality
def score_quality(summary: PerformanceSummary, config: ScoringConfig) -> CriterionScore:
    p = config.performance
    if summary.quality_sample_count == 0 or summary.raw_quality_mean is None:
        return _score(
            Criterion.QUALITY,
            None,
            p.neutral_quality_prior,
            evidence=["No reviewed task outcomes with a quality score"],
        )
    adjusted = shrink(
        summary.raw_quality_mean, summary.quality_sample_count, p.neutral_quality_prior, p.shrinkage_samples
    )
    evidence = [
        f"Mean reviewed quality {summary.raw_quality_mean:.1f} over {summary.quality_sample_count} outcome(s)"
        f" (similar tasks weighted x{p.similar_task_weight:g})"
    ]
    if summary.acceptance_rate is not None:
        evidence.append(
            f"Acceptance rate {summary.acceptance_rate:.0%}, average rework {summary.average_rework}"
        )
    return _score(
        Criterion.QUALITY,
        adjusted,
        p.neutral_quality_prior,
        evidence=evidence,
        details={
            "raw_mean": summary.raw_quality_mean,
            "samples": summary.quality_sample_count,
            "shrinkage_pseudo_count": p.shrinkage_samples,
        },
    )


# 4. Delivery reliability
def score_reliability(summary: PerformanceSummary, config: ScoringConfig) -> CriterionScore:
    p = config.performance
    if summary.reliability_sample_count == 0 or summary.on_time_rate is None:
        note = "No delivery history"
        if summary.excused_delays:
            note += f" (only {summary.excused_delays} externally caused delay(s), excluded)"
        return _score(Criterion.RELIABILITY, None, p.neutral_reliability_prior * 100, evidence=[note])
    adjusted = shrink(
        summary.on_time_rate,
        summary.reliability_sample_count,
        p.neutral_reliability_prior,
        p.shrinkage_samples,
    )
    evidence = [
        f"On time in {summary.on_time_rate:.0%} of {summary.reliability_sample_count} attributable deliveries",
    ]
    if summary.excused_delays:
        evidence.append(
            f"{summary.excused_delays} delay(s) caused by external blockers/dependencies excluded"
        )
    return _score(
        Criterion.RELIABILITY,
        adjusted * 100,
        p.neutral_reliability_prior * 100,
        evidence=evidence,
        details={
            "on_time_rate": summary.on_time_rate,
            "samples": summary.reliability_sample_count,
            "attributable_delays": summary.attributable_delays,
            "excused_delays": summary.excused_delays,
        },
    )


# 5. Available capacity
def score_capacity(
    requirements: TaskRequirements, availability: AvailabilityResult, config: ScoringConfig
) -> CriterionScore:
    required = requirements.estimated_hours
    capacity = availability.available_capacity_hours
    ratio = capacity / required
    if ratio >= 1.0:
        raw = 50 + 50 * min((ratio - 1.0) / (config.capacity_full_score_ratio - 1.0), 1.0)
    else:
        raw = 50 * ratio
    evidence = [
        f"{capacity:.1f}h free of {availability.available_working_hours:.1f}h working time in window "
        f"for a {required:g}h task",
    ]
    if availability.workload_certainty.value != "CERTAIN":
        evidence.append(f"Workload estimate is {availability.workload_certainty.value.lower()}")
    return _score(
        Criterion.CAPACITY,
        raw if availability.has_schedule else None,
        50.0,
        evidence=evidence,
        details={
            "capacity_ratio": round(ratio, 3),
            "available_capacity_hours": capacity,
            "required_hours": required,
            "workload_certainty": availability.workload_certainty.value,
        },
    )


# 6. Complexity fit
def seniority_tier(experience_years: float | None, thresholds: Sequence[float]) -> int | None:
    if experience_years is None:
        return None
    return 1 + sum(1 for t in thresholds if experience_years >= t)


def score_complexity_fit(
    requirements: TaskRequirements,
    profile: EmployeeProfile,
    outcomes: Sequence[TaskOutcomeRecord],
    config: ScoringConfig,
) -> CriterionScore:
    p = config.complexity
    tier = seniority_tier(profile.employee.experience_years, p.seniority_year_thresholds)
    demonstrated_levels = [
        o.complexity.level
        for o in outcomes
        if o.complexity is not None
        and o.accepted is not False
        and o.quality_score is not None
        and o.quality_score >= p.demonstrated_quality_threshold
    ]
    demonstrated = max(demonstrated_levels) if demonstrated_levels else None
    if tier is None and demonstrated is None:
        return _score(
            Criterion.COMPLEXITY_FIT, None, p.neutral_prior, evidence=["No seniority or complexity history"]
        )
    effective = max(tier or 0, demonstrated or 0)
    required = requirements.complexity.level
    gap = required - effective
    if gap > 0:
        raw = 100 - p.underqualified_penalty_per_level * gap
    elif gap >= -1:
        raw = 100.0
    else:
        raw = p.overqualified_score
    names = {c.level: c.value for c in TaskComplexity}
    evidence = [
        f"Task complexity {requirements.complexity.value}; capability level {names.get(min(effective, 4))}"
    ]
    if demonstrated is not None:
        evidence.append(f"Has delivered {names[demonstrated]}-complexity work with good quality")
    return _score(
        Criterion.COMPLEXITY_FIT,
        raw,
        p.neutral_prior,
        evidence=evidence,
        details={"seniority_tier": tier, "demonstrated_level": demonstrated, "required_level": required},
    )


# 7. Project familiarity
def score_project_familiarity(
    requirements: TaskRequirements, profile: EmployeeProfile, project_history: dict[UUID, int]
) -> CriterionScore:
    if requirements.project_id is None:
        return _score(
            Criterion.PROJECT_FAMILIARITY, None, 0.0, evidence=["Task has no project"], applicable=False
        )
    completed = project_history.get(requirements.project_id, 0)
    member = any(m.project_id == requirements.project_id for m in profile.memberships)
    raw = {0: 0.0, 1: 50.0, 2: 75.0}.get(completed, 100.0)
    if completed == 0 and member:
        raw = 25.0
    evidence = [
        f"{completed} completed task(s) on this project" + (", current project member" if member else "")
    ]
    return _score(
        Criterion.PROJECT_FAMILIARITY,
        raw,
        0.0,
        evidence=evidence,
        details={"completed_project_tasks": completed, "project_member": member},
    )

# ===========================================================================
# Recommendation Engine
# ===========================================================================

class CandidateInput(DomainModel):
    profile: EmployeeProfile
    availability: AvailabilityResult
    outcomes: tuple[TaskOutcomeRecord, ...] = ()
    project_history: dict[UUID, int] = Field(default_factory=dict)


class RecommendationEngine:
    def __init__(self, config: ScoringConfig | None = None) -> None:
        self.config = config or ScoringConfig()

    # building blocks (also used individually by the LangGraph nodes)
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
            score_skill_match(requirements, assessments, cfg),
            score_experience(requirements, profile, candidate.outcomes, as_of, cfg),
            score_quality(summary, cfg),
            score_reliability(summary, cfg),
            score_capacity(requirements, candidate.availability, cfg),
            score_complexity_fit(requirements, profile, candidate.outcomes, cfg),
            score_project_familiarity(requirements, profile, candidate.project_history),
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
            active_excluded = [e for e in excluded if e.availability_status != AvailabilityStatus.UNKNOWN]
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

    # one-shot convenience
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

    # helpers
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

# ===========================================================================
# Deterministic & Grounded Explanations
# ===========================================================================

_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")

_CRITERION_LABEL = {
    Criterion.SKILL_MATCH: "Skills",
    Criterion.EXPERIENCE: "Relevant experience",
    Criterion.QUALITY: "Historical quality",
    Criterion.RELIABILITY: "Delivery reliability",
    Criterion.CAPACITY: "Available capacity",
    Criterion.COMPLEXITY_FIT: "Complexity fit",
    Criterion.PROJECT_FAMILIARITY: "Project familiarity",
}

_CRITERION_LABEL_AR = {
    Criterion.SKILL_MATCH: "المهارات والتقنيات المطلوبة",
    Criterion.EXPERIENCE: "الخبرة السابقة والمهام المماثلة",
    Criterion.QUALITY: "جودة العمل والتسليمات السابقة",
    Criterion.RELIABILITY: "الالتزام بمواعيد التسليم والموثوقية",
    Criterion.CAPACITY: "الساعات المتاحة وعبء العمل في الـ Sprint",
    Criterion.COMPLEXITY_FIT: "ملاءمة مستوى الأقدمية والصعوبة",
    Criterion.PROJECT_FAMILIARITY: "الإلمام بالمشروع وفريق العمل",
}

_EXCLUSION_CODE_AR = {
    "INSUFFICIENT_AVAILABLE_CAPACITY": "عدم توفر ساعات عمل كافية في الـ Sprint",
    "NO_REQUIRED_SKILL_COVERAGE": "عدم تغطية المهارات الأساسية المطلوبة",
    "ON_LEAVE_DURING_WINDOW": "الموظف في إجازة رسمية خلال فترة الـ Sprint",
    "MISSING_MANDATORY_SKILL": "نقص مهارة إلزامية مطلوبة للمهمة",
    "INACTIVE_EMPLOYEE": "الموظف غير نشط حالياً في الشركة",
    "OUTSIDE_DEPARTMENT": "الموظف ليس ضمن القسم المخصص للمهمة",
    "OVERLAPPING_COMMITTED_TASKS": "تضارب مع مهام حرجة أخرى ملتزم بها الموظف",
}

_AVAILABILITY_STATUS_AR = {
    "AVAILABLE": "متاح بالكامل (Available)",
    "PARTIALLY_AVAILABLE": "متاح جزئياً (Partially Available)",
    "BUSY": "مشغول بكامل طاقته (Busy)",
    "ON_LEAVE": "في إجازة رسمية (On Leave)",
    "UNKNOWN": "غير محدد جدول العمل",
}

_CONFIDENCE_AR = {
    "HIGH": "عالية",
    "MEDIUM": "متوسطة",
    "LOW": "منخفضة",
}


def is_arabic(text: str | None) -> bool:
    """Returns True if the string contains Arabic characters."""
    if not text:
        return False
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def translate_evidence_ar(text: str) -> str:
    """Translates individual criteria evidence metrics into clear, professional Arabic."""
    # 1. Delivery reliability
    m = re.match(r"On time in (\d+(?:\.\d+)?%) of (\d+) attributable deliveries", text)
    if m:
        return f"الالتزام التام بمواعيد التسليم بنسبة {m.group(1)} من إجمالي {m.group(2)} تسليم(ات) منسوبة للمطور"
    m = re.match(r"(\d+) delay\(s\) caused by external blockers/dependencies excluded", text)
    if m:
        return f"تم استبعاد {m.group(1)} تأخير(ات) ناتجة عن معوقات أو اعتمادات خارجية"
    if text.startswith("No delivery history"):
        return "لا يوجد سجل تسليمات سابقة للمطور"

    # 2. Experience
    m = re.match(r"(\d+) completed similar task\(s\) out of (\d+) reviewed", text)
    if m:
        return f"أنجز {m.group(1)} مهمة/مهام مماثلة بنجاح من أصل {m.group(2)} مهمة تمت مراجعتها"
    m = re.match(r"(\d+(?:\.\d+)?) year\(s\) of experience with the required technologies", text)
    if m:
        return f"خبرة عملية موثقة تبلغ {m.group(1)} سنة/سنوات في التقنيات المطلوبة"
    if text == "No task history or experience records available":
        return "لا يوجد سجل مهام سابقة أو خبرات مسجلة في ملف الموظف"

    # 3. Quality
    m = re.match(r"Mean reviewed quality (\d+(?:\.\d+)?) over (\d+) outcome\(s\)(.*)", text)
    if m:
        return f"متوسط جودة المراجعة الفنية {m.group(1)} من 100 عبر {m.group(2)} تسليم(ات) سابقة"
    m = re.match(r"Acceptance rate (\d+(?:\.\d+)?%), average rework (\d+(?:\.\d+)?)", text)
    if m:
        return f"معدل قبول التسليمات من أول مرة {m.group(1)}، ومتوسط طلبات إعادة العمل {m.group(2)}"
    if text == "No reviewed task outcomes with a quality score":
        return "لا توجد تسليمات سابقة خضعت لتقييم الجودة البرمجية"

    # 4. Capacity
    m = re.match(
        r"(\d+(?:\.\d+)?)h free of (\d+(?:\.\d+)?)h working time in window for a (\d+(?:\.\d+)?)h task", text
    )
    if m:
        return f"سعة عمل متاحة تبلغ {m.group(1)} ساعة من أصل {m.group(2)} ساعة عمل في نافذة الـ Sprint لمهمة تتطلب {m.group(3)} ساعة"
    m = re.match(r"Workload estimate is (.+)", text)
    if m:
        val = m.group(1).lower()
        ar_val = {"certain": "مؤكد", "approximate": "تقريبي", "unknown": "غير محدد"}.get(val, val)
        return f"تقدير عبء العمل: {ar_val}"

    # 5. Complexity fit
    m = re.match(r"Task complexity (\w+); capability level (\w+)", text)
    if m:
        comp_ar = {"low": "منخفض", "medium": "متوسط", "high": "مرتفع", "critical": "حرج"}.get(
            m.group(1).lower(), m.group(1)
        )
        cap_ar = {"low": "مبتدئ", "medium": "متوسط", "high": "متقدم", "critical": "خبير"}.get(
            m.group(2).lower(), m.group(2)
        )
        return f"صعوبة المهمة: {comp_ar}؛ ومستوى قدرة المطور المثبتة: {cap_ar}"
    m = re.match(r"Has delivered (\w+)-complexity work with good quality", text)
    if m:
        comp_ar = {"low": "منخفضة التعقيد", "medium": "متوسطة التعقيد", "high": "عالية التعقيد"}.get(
            m.group(1).lower(), m.group(1)
        )
        return f"أنجز بنجاح أعمالاً {comp_ar} بجودة برمجية عالية معتمدة"
    if text == "No seniority or complexity history":
        return "لا توجد بيانات سابقة عن مستوى الأقدمية أو سوابق التعقيد"

    # 6. Project familiarity
    m = re.match(r"(\d+) completed task\(s\) on this project(, current project member)?", text)
    if m:
        extra = " (وهو عضو نشط في فريق المشروع حالياً)" if m.group(2) else ""
        return f"أنجز {m.group(1)} مهمة/مهام سابقة في هذا المشروع{extra}"
    if text == "Task has no project":
        return "المهمة غير مرتبطة بمشروع محدد"

    # 7. Skills
    m = re.match(
        r"(.+): declared (\d+), (\w+)(?:, last assessed (\d+) months ago)?(?:, effective ([\d.]+))? \(required (\d+), (\w+)\)",
        text,
    )
    if m:
        skill = m.group(1)
        decl = m.group(2)
        v_status = {"verified": "مُعتمد ومُوثق", "self_declared": "مُعلن ذاتياً"}.get(m.group(3), m.group(3))
        req_level = m.group(6)
        kind = {"mandatory": "إلزامي", "optional": "اختياري"}.get(m.group(7), m.group(7))
        eff = f"، والفعلي {m.group(5)}" if m.group(5) else ""
        return f"{skill}: المستوى المُعلن {decl} ({v_status}{eff}) [المطلوب: {req_level}، {kind}]"

    m = re.match(r"(.+): not in profile \(required (\d+), (\w+)\)", text)
    if m:
        skill = m.group(1)
        req_level = m.group(2)
        kind = {"mandatory": "إلزامي", "optional": "اختياري"}.get(m.group(3), m.group(3))
        return f"{skill}: غير مسجلة في ملف الموظف [المطلوب: {req_level}، {kind}]"

    return text


def _reason_summary(excluded: tuple[CandidateEvaluation, ...]) -> str:
    # Number of employees per reason (an employee missing two skills counts once).
    counts = Counter(
        code for e in excluded for code in dict.fromkeys(r.code.value for r in e.exclusion_reasons)
    )
    return ", ".join(f"{code} ({n})" for code, n in counts.most_common())


def _reason_summary_ar(excluded: tuple[CandidateEvaluation, ...]) -> str:
    counts = Counter(
        code for e in excluded for code in dict.fromkeys(r.code.value for r in e.exclusion_reasons)
    )
    return ", ".join(f"{_EXCLUSION_CODE_AR.get(code, code)} ({n})" for code, n in counts.most_common())


def _runner_up_advantages(
    primary: CandidateEvaluation, runner: CandidateEvaluation
) -> list[dict[str, Any]]:
    """Top applicable criteria where the primary match outscores the runner-up."""
    runner_map = {c.criterion: c for c in runner.criteria if c.applicable}
    edges: list[dict[str, Any]] = []
    for c in primary.criteria:
        if not c.applicable:
            continue
        rc = runner_map.get(c.criterion)
        if rc is None:
            continue
        delta = round(c.score_used - rc.score_used, 1)
        if delta >= 3.0:
            edges.append(
                {
                    "criterion": c.criterion,
                    "label_en": _CRITERION_LABEL[c.criterion],
                    "label_ar": _CRITERION_LABEL_AR[c.criterion],
                    "delta": delta,
                    "weighted_delta": round(c.weighted_score - rc.weighted_score, 2),
                }
            )
    edges.sort(key=lambda item: (-float(item["weighted_delta"]), -float(item["delta"])))
    return edges[:2]


def _deterministic_explanation_en(requirements: TaskRequirements, result: EngineResult) -> str:
    primary = result.primary
    lines: list[str] = []
    if primary is None:
        lines.append(f'No employee satisfies all hard constraints for "{requirements.title}".')
        if result.excluded:
            lines.append(f"- Exclusion reasons: {_reason_summary(result.excluded)}.")
        if "DEADLINE_INFEASIBLE" in result.warnings:
            lines.append(
                f"- The task needs {requirements.estimated_hours:g}h but no employee has that much working time in "
                "the execution window; extend the deadline or split the task."
            )
        for w in result.warnings:
            if w.startswith("NEAREST_MATCH_INSIGHT:"):
                insight = w.split(":", 1)[1]
                lines.append(f"- Smart Advisory: Nearest potential candidate is {insight}.")
        lines.append("- No employee was forced into a recommendation.")
        return "\n".join(lines)

    lines.append(
        f'{primary.full_name} ({primary.employee_code}) is recommended for "{requirements.title}" with a '
        f"suitability score of {primary.suitability_score:.1f}/100 "
        f"(confidence {primary.confidence_level.value.lower()}, data coverage {primary.data_coverage:.0%})."
    )
    if requirements.mandatory_skills:
        lines.append("- Satisfies every mandatory skill requirement.")
    for c in primary.criteria:
        if not c.applicable or not c.evidence:
            continue
        label = _CRITERION_LABEL[c.criterion]
        suffix = "" if c.has_evidence else " (no evidence; neutral default used)"
        lines.append(f"- {label}: {'; '.join(c.evidence)}{suffix}.")
    lines.append(f"- Availability status in the execution window: {primary.availability_status.value}.")
    if len(result.ranked) > 1:
        runner = result.ranked[1]
        lines.append(
            f"- Next best eligible candidate: {runner.full_name} ({runner.suitability_score:.1f}/100)."
            + (
                " Scores are close; manager judgement is advised."
                if "CLOSE_CANDIDATES" in result.warnings
                else ""
            )
        )
        edges = _runner_up_advantages(primary, runner)
        if edges:
            parts = [f"{e['label_en']} (+{e['delta']:.1f})" for e in edges]
            lines.append(
                f"- Deciding edge over {runner.full_name}: stronger {' and '.join(parts)}."
            )
    if result.excluded:
        lines.append(f"- {len(result.excluded)} employee(s) excluded: {_reason_summary(result.excluded)}.")
    if primary.missing_information:
        lines.append(f"- Data gaps: {', '.join(primary.missing_information)}.")
    lines.append("- This is a recommendation only; the project manager decides the assignment.")
    return "\n".join(lines)


def _deterministic_explanation_ar(requirements: TaskRequirements, result: EngineResult) -> str:
    primary = result.primary
    lines: list[str] = []
    if primary is None:
        lines.append(f'لا يوجد موظف يستوفي كافة الشروط الإلزامية لمهمة "{requirements.title}".')
        if result.excluded:
            lines.append(f"- أسباب الاستبعاد الرئيسية: {_reason_summary_ar(result.excluded)}.")
        if "DEADLINE_INFEASIBLE" in result.warnings:
            lines.append(
                f"- تتطلب المهمة {requirements.estimated_hours:g} ساعة عمل، ولا يتوفر أي مطور لديه هذه الساعات ضمن نافذة الـ Sprint المحددة؛ يُرجى تمديد المهلة أو تجزئة المهمة."
            )
        for w in result.warnings:
            if w.startswith("NEAREST_MATCH_INSIGHT:"):
                insight = w.split(":", 1)[1]
                lines.append(f"- استشارة ذكية للمدير: أقرب مرشح بديل مناسب هو {insight}.")
        lines.append("- لم يتم فرض أي مرشح غير مؤهل حفاظاً على معايير الجودة والتسليم.")
        return "\n".join(lines)

    conf_label = _CONFIDENCE_AR.get(primary.confidence_level.value, primary.confidence_level.value)
    lines.append(
        f'تم ترشيح المطور {primary.full_name} ({primary.employee_code}) لتنفيذ مهمة "{requirements.title}" '
        f"بنسبة ملاءمة وتوافق بلغت {primary.suitability_score:.1f}/100 "
        f"(مستوى الثقة: {conf_label}، اكتمال بيانات التقييم: {primary.data_coverage:.0%})."
    )
    if requirements.mandatory_skills:
        lines.append("- يستوفي جميع المهارات الإلزامية المطلوبة للمهمة بنجاح.")
    for c in primary.criteria:
        if not c.applicable or not c.evidence:
            continue
        label = _CRITERION_LABEL_AR.get(c.criterion, c.criterion.value)
        translated_items = [translate_evidence_ar(ev) for ev in c.evidence]
        suffix = "" if c.has_evidence else " (لا توجد بيانات سابقة؛ تم استخدام التقييم المحايد الافتراضي)"
        lines.append(f"- {label}: {'; '.join(translated_items)}{suffix}.")
    avail_status = _AVAILABILITY_STATUS_AR.get(
        primary.availability_status.value, primary.availability_status.value
    )
    lines.append(f"- حالة التوفر في فترة التنفيذ: {avail_status}.")
    if len(result.ranked) > 1:
        runner = result.ranked[1]
        lines.append(
            f"- البديل الأنسب التالي: {runner.full_name} (بنسبة توافق {runner.suitability_score:.1f}/100)."
            + (
                " الفارق بين المرشحين متقارب؛ يُنصح بتقدير مدير المشروع."
                if "CLOSE_CANDIDATES" in result.warnings
                else ""
            )
        )
        edges = _runner_up_advantages(primary, runner)
        if edges:
            parts = [f"{e['label_ar']} (+{e['delta']:.1f})" for e in edges]
            lines.append(
                f"- نقطة التفوق الحاسمة أمام {runner.full_name}: تفوق في {' و'.join(parts)}."
            )
    if result.excluded:
        lines.append(f"- تم استبعاد {len(result.excluded)} موظف(ين): {_reason_summary_ar(result.excluded)}.")
    if primary.missing_information:
        lines.append(f"- فجوات البيانات الإحصائية: {', '.join(primary.missing_information)}.")
    lines.append("- هذا التقرير استرشادي لدعم القرار؛ والاعتماد النهائي يخضع لتقدير مدير المشروع.")
    return "\n".join(lines)


def deterministic_explanation(
    requirements: TaskRequirements, result: EngineResult, *, lang: str | None = None
) -> str:
    use_ar = (lang == "ar") or (lang in (None, "auto") and is_arabic(requirements.title))
    if use_ar:
        return _deterministic_explanation_ar(requirements, result)
    return _deterministic_explanation_en(requirements, result)


def explanation_evidence(requirements: TaskRequirements, result: EngineResult) -> dict[str, Any]:
    primary = result.primary
    evidence: dict[str, Any] = {
        "task": {
            "title": requirements.title,
            "estimated_hours": requirements.estimated_hours,
            "complexity": requirements.complexity.value,
        },
        "status": result.status.value,
        "warnings": list(result.warnings),
    }
    if primary is not None:
        evidence["recommended"] = {
            "name": primary.full_name,
            "suitability_score": primary.suitability_score,
            "availability_status": primary.availability_status.value,
            "available_capacity_hours": primary.available_capacity_hours,
            "data_coverage": primary.data_coverage,
            "criteria": {
                c.criterion.value: {"score": c.score_used, "evidence": list(c.evidence)}
                for c in primary.criteria
                if c.applicable
            },
        }
        evidence["alternatives"] = [
            {"name": c.full_name, "suitability_score": c.suitability_score} for c in result.ranked[1:4]
        ]
        if len(result.ranked) > 1:
            evidence["runner_up_advantages"] = [
                {"criterion": e["criterion"].value, "label": e["label_en"], "delta": e["delta"]}
                for e in _runner_up_advantages(primary, result.ranked[1])
            ]
    evidence["excluded_reasons"] = _reason_summary(result.excluded)
    return evidence


def is_grounded(text: str, evidence: dict[str, Any], required_name: str | None) -> bool:
    if not text.strip():
        return False
    if required_name and required_name not in text:
        return False
    allowed = {float(n) for n in _NUMBER.findall(json.dumps(evidence, default=str))}
    allowed |= {round(a) for a in allowed} | {round(a, 1) for a in allowed}
    allowed |= {a * 100 for a in allowed if 0 < a <= 1}  # coverage ratios rendered as percentages
    for raw in _NUMBER.findall(text):
        value = float(raw)
        if value <= 6 and value.is_integer():  # list numbering
            continue
        if not any(abs(value - a) <= 0.051 for a in allowed):
            return False
    return True


def generate_explanation(
    requirements: TaskRequirements,
    result: EngineResult,
    llm: LLMProvider | None,
    *,
    use_llm: bool,
    lang: str | None = None,
) -> tuple[str, str, list[str]]:
    """Returns (text, source, warnings) where source is 'deterministic' or 'llm'."""
    fallback = deterministic_explanation(requirements, result, lang=lang)
    if not use_llm or llm is None or result.status == RecommendationStatus.NO_SUITABLE_CANDIDATE:
        return fallback, "deterministic", []
    evidence = explanation_evidence(requirements, result)
    use_ar = (lang == "ar") or (lang in (None, "auto") and is_arabic(requirements.title))
    target_lang = "ar" if use_ar else "en"
    try:
        text = llm.generate_text(explanation_messages(evidence, lang=target_lang)).value
    except LLMError as exc:
        return fallback, "deterministic", [f"LLM_EXPLANATION_UNAVAILABLE:{exc.code}"]
    primary_name = result.primary.full_name if result.primary else None
    if not is_grounded(text, evidence, primary_name):
        return fallback, "deterministic", ["LLM_EXPLANATION_REJECTED_UNGROUNDED"]
    return text, "llm", []

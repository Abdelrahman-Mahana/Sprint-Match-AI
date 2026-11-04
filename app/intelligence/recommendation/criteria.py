"""Scoring criteria. Every function is pure and returns a 0..100 score plus its evidence.

``raw_score`` is None when the employee has no evidence for the criterion; the documented neutral prior is then
used as ``score_used`` (missing evidence is never treated as zero performance) and data coverage drops.
Weights are filled in later by the engine.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from app.domain.entities.employee import EmployeeProfile
from app.domain.entities.performance import PerformanceSummary, TaskOutcomeRecord
from app.domain.entities.recommendation import CriterionScore
from app.domain.entities.task import TaskRequirements
from app.domain.entities.workforce import AvailabilityResult
from app.domain.enums import Criterion, TaskComplexity
from app.domain.policies import ScoringConfig
from app.intelligence.employee_profiles.skills import SkillAssessment
from app.intelligence.performance.metrics import shrink, task_similarity


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


# --- 1. Skill match --------------------------------------------------------------------------------
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


# --- 2. Relevant experience --------------------------------------------------------------------------
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


# --- 3. Historical quality ---------------------------------------------------------------------------
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


# --- 4. Delivery reliability -------------------------------------------------------------------------
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


# --- 5. Available capacity ---------------------------------------------------------------------------
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


# --- 6. Complexity fit -------------------------------------------------------------------------------
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


# --- 7. Project familiarity --------------------------------------------------------------------------
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

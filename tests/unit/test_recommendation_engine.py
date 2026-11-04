from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.enums import (
    AvailabilityStatus,
    ConfidenceLevel,
    Criterion,
    DelayReason,
    EmploymentStatus,
    ExclusionReason,
    RecommendationStatus,
    SkillVerificationStatus,
    TaskComplexity,
)
from app.domain.policies import ScoringConfig
from app.intelligence.employee_profiles.skills import assess_skill
from app.intelligence.performance.metrics import summarize_performance
from app.intelligence.recommendation.criteria import skill_requirement_score
from app.intelligence.recommendation.engine import CandidateInput, RecommendationEngine
from app.intelligence.workforce.capacity import WorkforceCalculator
from tests.fixtures.builders import (
    AS_OF,
    PROJECT_ID,
    assignment,
    employee,
    experience,
    leave,
    local,
    outcome,
    profile,
    requirements,
    skill,
    window,
    workforce,
)

W = window(local(date(2026, 11, 4)), local(date(2026, 11, 9)))  # 21 working hours
engine = RecommendationEngine()
calc = WorkforceCalculator()


def candidate(
    code: str,
    skills,
    *,
    outcomes=(),
    wf_kwargs=None,
    years=5.0,
    history=None,
    experiences=(),
    status=EmploymentStatus.ACTIVE,
) -> CandidateInput:
    emp = employee(code, years=years, status=status)
    wf = workforce(emp.id, status=status, **(wf_kwargs or {}))
    return CandidateInput(
        profile=profile(emp, tuple(skills), experiences=experiences),
        availability=calc.calculate(wf, W, as_of=AS_OF),
        outcomes=tuple(outcomes),
        project_history=history or {},
    )


# --- skill matching ---------------------------------------------------------------------------------
def test_skill_requirement_score_curve() -> None:
    assert skill_requirement_score(75, 75, 85) == 85
    assert skill_requirement_score(100, 75, 85) == 100
    assert skill_requirement_score(37.5, 75, 85) == pytest.approx(42.5)
    assert skill_requirement_score(0, 75, 85) == 0


def test_effective_level_discounts_unverified_and_stale_skills() -> None:
    cfg = ScoringConfig().skills
    fresh = assess_skill(skill("Python", 80), AS_OF, cfg)
    assert fresh.effective_level == 80 and not fresh.stale
    old = assess_skill(
        skill("Python", 85, SkillVerificationStatus.SELF_DECLARED, assessed=AS_OF - timedelta(days=4 * 365)),
        AS_OF,
        cfg,
    )
    assert old.stale and old.effective_level == pytest.approx(85 * 0.8 * 0.7)
    rejected = assess_skill(skill("Python", 90, SkillVerificationStatus.REJECTED), AS_OF, cfg)
    assert rejected.effective_level == 0


def test_unrelated_skills_do_not_inflate_score() -> None:
    base = candidate("A", [skill("Python", 80), skill("FastAPI", 80)])
    many = candidate(
        "B",
        [
            skill("Python", 80),
            skill("FastAPI", 80),
            skill("React", 95),
            skill("Docker", 95),
            skill("Kubernetes", 95),
        ],
    )
    result = engine.evaluate(requirements(), [base, many], AS_OF)
    a, b = (next(c for c in result.ranked if c.employee_code == code) for code in ("A", "B"))
    assert a.criterion(Criterion.SKILL_MATCH).score_used == b.criterion(Criterion.SKILL_MATCH).score_used


# --- eligibility ------------------------------------------------------------------------------------
def test_exclusion_reasons_are_explicit() -> None:
    missing = candidate("MISS", [skill("Python", 90)])
    below = candidate("LOW", [skill("Python", 60), skill("FastAPI", 80)])
    busy = candidate(
        "BUSY",
        [skill("Python", 90), skill("FastAPI", 90)],
        wf_kwargs={
            "assignments": (
                assignment(
                    remaining=20,
                    planned_start=local(date(2026, 11, 4), 9),
                    planned_end=local(date(2026, 11, 8), 17),
                ),
            )
        },
    )
    away = candidate(
        "AWAY",
        [skill("Python", 90), skill("FastAPI", 90)],
        wf_kwargs={"leaves": (leave(local(date(2026, 11, 4)), local(date(2026, 11, 9))),)},
    )
    gone = candidate("GONE", [skill("Python", 90), skill("FastAPI", 90)], status=EmploymentStatus.INACTIVE)
    result = engine.evaluate(requirements(), [missing, below, busy, away, gone], AS_OF)
    reasons = {e.employee_code: {r.code for r in e.exclusion_reasons} for e in result.excluded}
    assert reasons["MISS"] == {ExclusionReason.MANDATORY_SKILL_MISSING}
    assert reasons["LOW"] == {ExclusionReason.MANDATORY_SKILL_BELOW_THRESHOLD}
    assert reasons["BUSY"] == {ExclusionReason.INSUFFICIENT_AVAILABLE_CAPACITY}
    assert reasons["AWAY"] == {ExclusionReason.ON_LEAVE_DURING_WINDOW}
    assert reasons["GONE"] == {ExclusionReason.EMPLOYEE_INACTIVE}
    assert result.status == RecommendationStatus.NO_SUITABLE_CANDIDATE
    assert result.primary is None
    assert all(e.suitability_score is None for e in result.excluded)


def test_other_tasks_do_not_disqualify_when_capacity_suffices() -> None:
    c = candidate(
        "OK",
        [skill("Python", 90), skill("FastAPI", 90)],
        wf_kwargs={
            "assignments": (
                assignment(
                    remaining=4,
                    planned_start=local(date(2026, 11, 4), 9),
                    planned_end=local(date(2026, 11, 8), 17),
                ),
            )
        },
    )
    result = engine.evaluate(requirements(), [c], AS_OF)
    assert result.primary is not None and result.primary.available_capacity_hours == 17


def test_requires_verification_and_unknown_availability() -> None:
    req = requirements()
    req = req.model_copy(
        update={
            "required_skills": tuple(
                s.model_copy(update={"requires_verification": True}) for s in req.required_skills
            )
        }
    )
    # 99 * 0.8 (self-declared) = 79.2 >= 75, so only the verification requirement fails.
    unverified = candidate(
        "U", [skill("Python", 99, SkillVerificationStatus.SELF_DECLARED), skill("FastAPI", 90)]
    )
    unknown = candidate("X", [skill("Python", 90), skill("FastAPI", 90)], wf_kwargs={"schedule": ()})
    result = engine.evaluate(req, [unverified, unknown], AS_OF)
    reasons = {e.employee_code: [r.code for r in e.exclusion_reasons] for e in result.excluded}
    assert reasons["U"] == [ExclusionReason.MANDATORY_SKILL_NOT_VERIFIED]
    assert reasons["X"] == [ExclusionReason.AVAILABILITY_UNKNOWN]
    assert ExclusionReason.AVAILABILITY_UNKNOWN.is_data_gap


def test_optional_only_requirements_need_some_coverage() -> None:
    req = requirements(skills=(("Python", 60, False), ("PostgreSQL", 60, False)))
    none = candidate("N", [skill("React", 90)])
    some = candidate("S", [skill("PostgreSQL", 70)])
    result = engine.evaluate(req, [none, some], AS_OF)
    assert [c.employee_code for c in result.ranked] == ["S"]
    assert result.excluded[0].exclusion_reasons[0].code == ExclusionReason.NO_REQUIRED_SKILL_COVERAGE


# --- performance & missing data -------------------------------------------------------------------
def test_missing_history_uses_neutral_prior_and_lowers_coverage() -> None:
    veteran = candidate(
        "V", [skill("Python", 85), skill("FastAPI", 85)], outcomes=[outcome(80)] * 4, history={PROJECT_ID: 2}
    )
    newbie = candidate("N", [skill("Python", 85), skill("FastAPI", 85)])
    result = engine.evaluate(requirements(), [veteran, newbie], AS_OF)
    n = next(c for c in result.ranked if c.employee_code == "N")
    q = n.criterion(Criterion.QUALITY)
    assert q.raw_score is None and not q.has_evidence
    assert q.score_used == ScoringConfig().performance.neutral_quality_prior  # never zero
    assert "NO_EVIDENCE:QUALITY" in n.missing_information
    v = next(c for c in result.ranked if c.employee_code == "V")
    assert n.data_coverage < v.data_coverage
    assert n.confidence_level in (ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM)


def test_external_delays_excluded_from_reliability() -> None:
    cfg = ScoringConfig().performance
    outs = [
        outcome(on_time=True),
        outcome(on_time=False, delay=DelayReason.EXTERNAL_BLOCKER),
        outcome(on_time=False, delay=DelayReason.EMPLOYEE),
    ]
    s = summarize_performance(employee().id, outs, cfg)
    assert (s.reliability_sample_count, s.on_time_rate, s.excused_delays) == (2, 0.5, 1)


def test_quality_penalizes_rework_and_rejection_and_shrinks_small_samples() -> None:
    cfg = ScoringConfig().performance
    s = summarize_performance(employee().id, [outcome(90, rework=2)], cfg)
    assert s.raw_quality_mean == 70
    rejected = summarize_performance(employee().id, [outcome(90, accepted=False)], cfg)
    assert rejected.raw_quality_mean == cfg.rejected_quality_cap
    one = candidate("ONE", [skill("Python", 85), skill("FastAPI", 85)], outcomes=[outcome(100)])
    q = engine.evaluate(requirements(), [one], AS_OF).ranked[0].criterion(Criterion.QUALITY)
    assert q.score_used == pytest.approx((100 + 3 * 60) / 4)


def test_experience_counts_similar_tasks_and_relevant_years() -> None:
    c = candidate(
        "E",
        [skill("Python", 85), skill("FastAPI", 85)],
        outcomes=[outcome()] * 5 + [outcome(skills=("React",))],
        experiences=(experience(("Python",), 5),),
    )
    e = engine.evaluate(requirements(), [c], AS_OF).ranked[0].criterion(Criterion.EXPERIENCE)
    assert e.details["similar_task_count"] == 5
    assert e.score_used == pytest.approx(100, abs=0.1)


def test_complexity_fit_penalizes_underqualification() -> None:
    junior = candidate("J", [skill("Python", 85), skill("FastAPI", 85)], years=1)
    senior = candidate("S", [skill("Python", 85), skill("FastAPI", 85)], years=6)
    r = engine.evaluate(requirements(complexity=TaskComplexity.HIGH), [junior, senior], AS_OF)
    scores = {c.employee_code: c.criterion(Criterion.COMPLEXITY_FIT).score_used for c in r.ranked}
    assert scores == {"J": 30, "S": 100}


# --- weighting & ranking ---------------------------------------------------------------------------
def test_not_applicable_criterion_weight_is_redistributed() -> None:
    c = candidate("A", [skill("Python", 85), skill("FastAPI", 85)])
    r = engine.evaluate(requirements(project_id=None), [c], AS_OF).ranked[0]
    pf = r.criterion(Criterion.PROJECT_FAMILIARITY)
    assert not pf.applicable and pf.weight == 0
    assert sum(x.weight for x in r.criteria) == pytest.approx(1.0)
    assert r.suitability_score == pytest.approx(sum(x.weight * x.score_used for x in r.criteria), abs=0.01)


def test_deterministic_tie_breaking_and_close_candidates() -> None:
    twins = [candidate(code, [skill("Python", 85), skill("FastAPI", 85)]) for code in ("EMP-2", "EMP-1")]
    first = engine.evaluate(requirements(), twins, AS_OF)
    second = engine.evaluate(requirements(), list(reversed(twins)), AS_OF)
    assert [c.employee_code for c in first.ranked] == ["EMP-1", "EMP-2"]
    assert first.model_dump() == second.model_dump()
    assert "CLOSE_CANDIDATES" in first.warnings
    assert [c.rank for c in first.ranked] == [1, 2]


def test_better_candidate_ranks_first_and_success_status() -> None:
    strong = candidate(
        "S", [skill("Python", 95), skill("FastAPI", 92)], outcomes=[outcome(92)] * 5, history={PROJECT_ID: 3}
    )
    ok = candidate("O", [skill("Python", 78), skill("FastAPI", 72)], outcomes=[outcome(70)] * 2)
    result = engine.evaluate(requirements(), [ok, strong], AS_OF)
    assert result.status == RecommendationStatus.SUCCESS
    assert result.primary.employee_code == "S"
    assert result.ranked[0].suitability_score > result.ranked[1].suitability_score
    assert result.primary.availability_status == AvailabilityStatus.AVAILABLE


def test_deadline_infeasible_warning_when_nobody_can_fit_the_effort() -> None:
    c = candidate("A", [skill("Python", 90), skill("FastAPI", 90)])
    result = engine.evaluate(requirements(hours=40), [c], AS_OF)  # window has 21h
    assert result.status == RecommendationStatus.NO_SUITABLE_CANDIDATE
    assert "DEADLINE_INFEASIBLE" in result.warnings


def test_partial_data_status_for_low_coverage_primary() -> None:
    cfg = ScoringConfig(partial_data_coverage_threshold=0.9)
    c = candidate("N", [skill("Python", 85), skill("FastAPI", 85)], years=None)
    result = RecommendationEngine(cfg).evaluate(requirements(), [c], AS_OF)
    assert result.status == RecommendationStatus.PARTIAL_DATA


def test_scoring_config_validation_and_fingerprint() -> None:
    with pytest.raises(ValueError):
        ScoringConfig(weights={Criterion.SKILL_MATCH: 1.0})
    a, b = ScoringConfig(), ScoringConfig(close_candidates_margin=5)
    assert a.fingerprint() == ScoringConfig().fingerprint() != b.fingerprint()

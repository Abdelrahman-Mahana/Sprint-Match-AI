"""Historical performance metrics derived exclusively from reviewed task outcomes.

No evidence -> metrics are ``None`` (never zero). Delays caused by external blockers, dependencies or scope
changes are excluded from the reliability denominator.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.domain.entities.performance import PerformanceSummary, TaskOutcomeRecord
from app.domain.entities.task import TaskRequirements
from app.domain.policies import PerformancePolicy


def task_similarity(outcome: TaskOutcomeRecord, requirements: TaskRequirements) -> float:
    """0..1: weighted share of the new task's required skills that the past task also required (80%),
    plus category match (20%)."""
    reqs = [s for s in requirements.required_skills if s.skill_id is not None]
    category_match = (
        1.0 if outcome.task_category and outcome.task_category == requirements.task_category else 0.0
    )
    if not reqs:
        return category_match
    past = set(outcome.required_skill_ids)
    total = sum(s.weight for s in reqs)
    shared = sum(s.weight for s in reqs if s.skill_id in past)
    return round(0.8 * (shared / total) + 0.2 * category_match, 4)


def outcome_quality(outcome: TaskOutcomeRecord, policy: PerformancePolicy) -> float | None:
    if outcome.quality_score is None:
        return None
    q = outcome.quality_score - policy.rework_penalty * outcome.rework_count
    if outcome.accepted is False:
        q = min(q, policy.rejected_quality_cap)
    return max(min(q, 100.0), 0.0)


def summarize_performance(
    employee_id: UUID,
    outcomes: Sequence[TaskOutcomeRecord],
    policy: PerformancePolicy,
    requirements: TaskRequirements | None = None,
) -> PerformanceSummary:
    weighted_sum = 0.0
    weight_total = 0.0
    quality_samples = 0
    similar = 0
    for o in outcomes:
        is_similar = (
            requirements is not None and task_similarity(o, requirements) >= policy.similarity_threshold
        )
        similar += int(is_similar)
        q = outcome_quality(o, policy)
        if q is None:
            continue
        w = policy.similar_task_weight if is_similar else 1.0
        weighted_sum += w * q
        weight_total += w
        quality_samples += 1

    accepted_known = [o.accepted for o in outcomes if o.accepted is not None]
    on_time = 0
    attributable = 0
    excused = 0
    for o in outcomes:
        if o.on_time is None:
            continue
        if o.on_time:
            on_time += 1
        elif o.delay_reason is None or o.delay_reason.attributable_to_employee:
            attributable += 1
        else:
            excused += 1
    reliability_samples = on_time + attributable

    return PerformanceSummary(
        employee_id=employee_id,
        outcome_count=len(outcomes),
        quality_sample_count=quality_samples,
        raw_quality_mean=round(weighted_sum / weight_total, 2) if weight_total else None,
        acceptance_rate=round(sum(accepted_known) / len(accepted_known), 4) if accepted_known else None,
        average_rework=round(sum(o.rework_count for o in outcomes) / len(outcomes), 2) if outcomes else None,
        reliability_sample_count=reliability_samples,
        on_time_rate=round(on_time / reliability_samples, 4) if reliability_samples else None,
        excused_delays=excused,
        attributable_delays=attributable,
        similar_task_count=similar,
    )


def shrink(value: float, samples: int, prior: float, pseudo_count: int) -> float:
    """Bayesian shrinkage toward the prior: small samples cannot produce extreme scores."""
    return (samples * value + pseudo_count * prior) / (samples + pseudo_count)

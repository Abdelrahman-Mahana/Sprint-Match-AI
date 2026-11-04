"""Evidence-grounded explanations.

The deterministic explanation is built exclusively from the structured engine result and is always available.
An optional LLM rewording receives only that structured evidence and is accepted only if it passes a grounding
check (it must name the recommended employee and may only use numbers present in the evidence).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from app.domain.entities.recommendation import CandidateEvaluation, EngineResult
from app.domain.entities.task import TaskRequirements
from app.domain.enums import Criterion, RecommendationStatus
from app.infrastructure.llm.base import LLMError, LLMProvider
from app.intelligence.prompts import explanation_messages

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


def _reason_summary(excluded: tuple[CandidateEvaluation, ...]) -> str:
    # Number of employees per reason (an employee missing two skills counts once).
    counts = Counter(
        code for e in excluded for code in dict.fromkeys(r.code.value for r in e.exclusion_reasons)
    )
    return ", ".join(f"{code} ({n})" for code, n in counts.most_common())


def deterministic_explanation(requirements: TaskRequirements, result: EngineResult) -> str:
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
    if result.excluded:
        lines.append(f"- {len(result.excluded)} employee(s) excluded: {_reason_summary(result.excluded)}.")
    if primary.missing_information:
        lines.append(f"- Data gaps: {', '.join(primary.missing_information)}.")
    lines.append("- This is a recommendation only; the project manager decides the assignment.")
    return "\n".join(lines)


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
    requirements: TaskRequirements, result: EngineResult, llm: LLMProvider | None, *, use_llm: bool
) -> tuple[str, str, list[str]]:
    """Returns (text, source, warnings) where source is 'deterministic' or 'llm'."""
    fallback = deterministic_explanation(requirements, result)
    if not use_llm or llm is None or result.status == RecommendationStatus.NO_SUITABLE_CANDIDATE:
        return fallback, "deterministic", []
    evidence = explanation_evidence(requirements, result)
    try:
        text = llm.generate_text(explanation_messages(evidence)).value
    except LLMError as exc:
        return fallback, "deterministic", [f"LLM_EXPLANATION_UNAVAILABLE:{exc.code}"]
    primary_name = result.primary.full_name if result.primary else None
    if not is_grounded(text, evidence, primary_name):
        return fallback, "deterministic", ["LLM_EXPLANATION_REJECTED_UNGROUNDED"]
    return text, "llm", []

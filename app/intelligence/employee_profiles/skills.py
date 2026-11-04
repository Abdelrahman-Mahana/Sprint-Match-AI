"""Effective skill proficiency: declared level discounted by verification status and assessment staleness.

effective_level = proficiency_level * verification_factor * recency_factor
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.entities.base import DomainModel
from app.domain.entities.employee import EmployeeSkill
from app.domain.enums import SkillVerificationStatus
from app.domain.policies import SkillPolicy


class SkillAssessment(DomainModel):
    skill_id: UUID
    skill_name: str
    proficiency_level: int
    verification_status: SkillVerificationStatus
    verification_factor: float
    recency_factor: float
    effective_level: float
    months_since_assessment: int | None
    stale: bool

    def describe(self) -> str:
        parts = [
            f"{self.skill_name}: declared {self.proficiency_level}",
            self.verification_status.value.lower(),
        ]
        if self.stale:
            parts.append(f"last assessed {self.months_since_assessment} months ago")
        if self.effective_level != self.proficiency_level:
            parts.append(f"effective {self.effective_level:.1f}")
        return ", ".join(parts)


def _months_between(earlier: datetime, later: datetime) -> int:
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return max(months, 0)


def assess_skill(skill: EmployeeSkill, as_of: datetime, policy: SkillPolicy) -> SkillAssessment:
    verification_factor = policy.verification_factors.get(skill.verification_status, 0.0)
    assessed = skill.last_assessed_at or skill.verified_at
    months = _months_between(assessed, as_of) if assessed else None
    recency_factor = 1.0
    if months is not None:
        for min_months, factor in sorted(policy.staleness_steps, key=lambda s: -s[0]):
            if months >= min_months:
                recency_factor = factor
                break
    effective = round(skill.proficiency_level * verification_factor * recency_factor, 2)
    return SkillAssessment(
        skill_id=skill.skill_id,
        skill_name=skill.skill_name,
        proficiency_level=skill.proficiency_level,
        verification_status=skill.verification_status,
        verification_factor=verification_factor,
        recency_factor=recency_factor,
        effective_level=effective,
        months_since_assessment=months,
        stale=recency_factor < 1.0,
    )

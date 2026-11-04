"""Hard eligibility constraints, evaluated independently from (and before) soft ranking.

Having other tasks never disqualifies an employee by itself: only insufficient *remaining capacity* does.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.domain.entities.employee import EmployeeProfile
from app.domain.entities.recommendation import EligibilityResult, ExclusionDetail
from app.domain.entities.task import TaskRequirements
from app.domain.entities.workforce import AvailabilityResult
from app.domain.enums import AvailabilityStatus, EmploymentStatus, ExclusionReason, SkillVerificationStatus
from app.domain.policies import EligibilityPolicy
from app.intelligence.employee_profiles.skills import SkillAssessment


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

    # --- Skills ------------------------------------------------------------------------------
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

    # --- Access -------------------------------------------------------------------------------
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

    # --- Required certifications (valid on the evaluation date, not rejected) ----------------------
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

    # --- Availability / capacity -----------------------------------------------------------------
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

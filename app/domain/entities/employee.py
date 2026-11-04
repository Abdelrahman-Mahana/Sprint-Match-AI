from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from app.domain.entities.base import DomainModel
from app.domain.enums import CredentialType, EmploymentStatus, EvidenceType, SkillVerificationStatus


class Skill(DomainModel):
    id: UUID
    name: str
    category: str
    aliases: tuple[str, ...] = ()


class Employee(DomainModel):
    id: UUID
    company_id: UUID
    employee_code: str
    full_name: str
    email: str | None = None
    job_title: str
    department_id: UUID | None = None
    department_name: str | None = None
    team_id: UUID | None = None
    team_name: str | None = None
    hire_date: date | None = None
    experience_years: float | None = Field(default=None, ge=0)
    employment_status: EmploymentStatus
    timezone: str


class EmployeeSkill(DomainModel):
    skill_id: UUID
    skill_name: str
    proficiency_level: int = Field(ge=0, le=100)
    verification_status: SkillVerificationStatus
    verified_at: datetime | None = None
    last_assessed_at: datetime | None = None


class SkillEvidence(DomainModel):
    skill_id: UUID
    evidence_type: EvidenceType
    verification_status: SkillVerificationStatus
    project_id: UUID | None = None
    task_id: UUID | None = None
    notes: str | None = None
    created_at: datetime


class Experience(DomainModel):
    id: UUID
    organization_or_project: str
    role: str
    description: str | None = None
    start_date: date
    end_date: date | None = None
    skill_ids: tuple[UUID, ...] = ()
    skill_names: tuple[str, ...] = ()


class Credential(DomainModel):
    name: str
    issuer: str | None = None
    credential_type: CredentialType
    skill_id: UUID | None = None
    issued_at: date | None = None
    expires_at: date | None = None
    verification_status: SkillVerificationStatus


class ProjectMembership(DomainModel):
    project_id: UUID
    project_name: str
    role: str


class EmployeeProfile(DomainModel):
    """Task-independent professional profile (the data part of the Employee 360 view)."""

    employee: Employee
    skills: tuple[EmployeeSkill, ...] = ()
    evidence: tuple[SkillEvidence, ...] = ()
    experiences: tuple[Experience, ...] = ()
    credentials: tuple[Credential, ...] = ()
    memberships: tuple[ProjectMembership, ...] = ()

    def skill(self, skill_id: UUID) -> EmployeeSkill | None:
        return next((s for s in self.skills if s.skill_id == skill_id), None)

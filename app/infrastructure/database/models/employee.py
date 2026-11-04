from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import CredentialType, EmploymentStatus, EvidenceType, SkillVerificationStatus
from app.infrastructure.database.models.base import Base, TimestampMixin, enum_column, uuid_pk


class EmployeeModel(TimestampMixin, Base):
    """Professional profile only. No protected/sensitive personal attributes are stored by design.

    Weekly capacity is *derived* from ``work_schedule_days`` (single source of truth), not stored here.
    """

    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    employee_code: Mapped[str] = mapped_column(String(32))
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    job_title: Mapped[str] = mapped_column(String(120))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), index=True)
    hire_date: Mapped[date | None] = mapped_column(Date)
    experience_years: Mapped[float | None] = mapped_column(Numeric(4, 1))
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        enum_column(EmploymentStatus, "employment_status")
    )
    timezone: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("company_id", "employee_code"),
        UniqueConstraint("company_id", "email"),
        CheckConstraint(
            "experience_years IS NULL OR experience_years >= 0", name="experience_years_non_negative"
        ),
        Index("ix_employees_company_status", "company_id", "employment_status"),
        Index("ix_employees_department", "department_id"),
    )


class SkillModel(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")

    __table_args__ = (UniqueConstraint("company_id", "name"),)


class EmployeeSkillModel(Base):
    __tablename__ = "employee_skills"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    proficiency_level: Mapped[int] = mapped_column(SmallInteger)
    verification_status: Mapped[SkillVerificationStatus] = mapped_column(
        enum_column(SkillVerificationStatus, "skill_verification_status")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("employee_id", "skill_id"),
        CheckConstraint("proficiency_level BETWEEN 0 AND 100", name="proficiency_range"),
        CheckConstraint(
            "verification_status <> 'VERIFIED' OR verified_at IS NOT NULL", name="verified_requires_timestamp"
        ),
    )


class EmployeeSkillEvidenceModel(Base):
    __tablename__ = "employee_skill_evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee_skills.id", ondelete="CASCADE"), index=True
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(enum_column(EvidenceType, "evidence_type"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    verification_status: Mapped[SkillVerificationStatus] = mapped_column(
        enum_column(SkillVerificationStatus, "evidence_verification_status")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeeExperienceModel(Base):
    __tablename__ = "employee_experiences"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    organization_or_project: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    relevant_start_date: Mapped[date] = mapped_column(Date)
    relevant_end_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint(
            "relevant_end_date IS NULL OR relevant_end_date >= relevant_start_date", name="experience_dates"
        ),
    )


class EmployeeExperienceSkillModel(Base):
    """Normalized 'technologies' of an experience record."""

    __tablename__ = "employee_experience_skills"

    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee_experiences.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)


class EmployeeCredentialModel(Base):
    """Certifications and trainings."""

    __tablename__ = "employee_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str | None] = mapped_column(String(200))
    credential_type: Mapped[CredentialType] = mapped_column(enum_column(CredentialType, "credential_type"))
    skill_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"))
    issued_at: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[date | None] = mapped_column(Date)
    verification_status: Mapped[SkillVerificationStatus] = mapped_column(
        enum_column(SkillVerificationStatus, "credential_verification_status")
    )

    __table_args__ = (
        CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at > issued_at", name="credential_dates"
        ),
    )

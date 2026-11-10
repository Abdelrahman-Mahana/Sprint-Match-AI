from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import Select, exists, func, select

from app.core.exceptions import NotFoundError
from app.domain.entities.employee import (
    Credential,
    Employee,
    EmployeeProfile,
    EmployeeSkill,
    Experience,
    ProjectMembership,
    SkillEvidence,
)
from app.domain.entities.organization import Company
from app.domain.enums import EmploymentStatus
from app.domain.interfaces import Page
from app.infrastructure.database.models import (
    CompanyModel,
    DepartmentModel,
    EmployeeCredentialModel,
    EmployeeExperienceModel,
    EmployeeExperienceSkillModel,
    EmployeeModel,
    EmployeeSkillEvidenceModel,
    EmployeeSkillModel,
    ProjectMemberModel,
    ProjectModel,
    SkillModel,
    TeamModel,
)
from app.infrastructure.database.repositories.base import SqlRepository, to_float


class SqlCompanyRepository(SqlRepository):
    @staticmethod
    def _to_domain(m: CompanyModel) -> Company:
        return Company(
            id=m.id, code=m.code, name=m.name, timezone=m.timezone, working_weekdays=tuple(m.working_weekdays)
        )

    def get_by_code(self, code: str) -> Company | None:
        m = self._execute(select(CompanyModel).where(CompanyModel.code == code)).scalar_one_or_none()
        return self._to_domain(m) if m else None

    def get(self, company_id: UUID) -> Company | None:
        m = self._execute(select(CompanyModel).where(CompanyModel.id == company_id)).scalar_one_or_none()
        return self._to_domain(m) if m else None


class SqlEmployeeRepository(SqlRepository):
    def _base(self, company_id: UUID) -> Select[EmployeeModel, str, str]:
        return (
            select(EmployeeModel, DepartmentModel.name, TeamModel.name)
            .outerjoin(DepartmentModel, DepartmentModel.id == EmployeeModel.department_id)
            .outerjoin(TeamModel, TeamModel.id == EmployeeModel.team_id)
            .where(EmployeeModel.company_id == company_id)
        )

    @staticmethod
    def _to_domain(m: EmployeeModel, dept: str | None, team: str | None) -> Employee:
        return Employee(
            id=m.id,
            company_id=m.company_id,
            employee_code=m.employee_code,
            full_name=m.full_name,
            email=m.email,
            job_title=m.job_title,
            department_id=m.department_id,
            department_name=dept,
            team_id=m.team_id,
            team_name=team,
            hire_date=m.hire_date,
            experience_years=to_float(m.experience_years),
            employment_status=m.employment_status,
            timezone=m.timezone,
        )

    def list_employees(
        self,
        company_id: UUID,
        *,
        status: EmploymentStatus | None = None,
        department_id: UUID | None = None,
        skill_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Employee]:
        stmt = self._base(company_id)
        if status is not None:
            stmt = stmt.where(EmployeeModel.employment_status == status)
        if department_id is not None:
            stmt = stmt.where(EmployeeModel.department_id == department_id)
        if skill_name:
            stmt = stmt.where(
                exists()
                .where(EmployeeSkillModel.employee_id == EmployeeModel.id)
                .where(EmployeeSkillModel.skill_id == SkillModel.id)
                .where(func.lower(SkillModel.name) == skill_name.lower())
            )
        total = self._execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = self._execute(stmt.order_by(EmployeeModel.employee_code).limit(limit).offset(offset)).all()
        return Page[Employee](
            items=tuple(self._to_domain(*r) for r in rows), total=total, limit=limit, offset=offset
        )

    def list_employee_ids(self, company_id: UUID, *, active_only: bool = False) -> list[UUID]:
        stmt = select(EmployeeModel.id).where(EmployeeModel.company_id == company_id)
        if active_only:
            stmt = stmt.where(EmployeeModel.employment_status == EmploymentStatus.ACTIVE)
        return list(self._execute(stmt.order_by(EmployeeModel.employee_code)).scalars())

    def get_employee(self, company_id: UUID, employee_id: UUID) -> Employee | None:
        row = self._execute(self._base(company_id).where(EmployeeModel.id == employee_id)).one_or_none()
        return self._to_domain(*row) if row else None

    def update_employee(
        self,
        company_id: UUID,
        employee_id: UUID,
        *,
        full_name: str | None = None,
        job_title: str | None = None,
        experience_years: float | None = None,
        employment_status: EmploymentStatus | None = None,
        email: str | None = None,
    ) -> Employee:
        m = self._execute(
            select(EmployeeModel)
            .where(EmployeeModel.company_id == company_id)
            .where(EmployeeModel.id == employee_id)
        ).scalar_one_or_none()
        if m is None:
            raise NotFoundError("Employee not found")
        if full_name is not None:
            m.full_name = full_name.strip()
        if job_title is not None:
            m.job_title = job_title.strip()
        if experience_years is not None:
            m.experience_years = experience_years
        if employment_status is not None:
            m.employment_status = employment_status
        if email is not None:
            m.email = email.strip()
        self._s.flush()
        emp = self.get_employee(company_id, employee_id)
        assert emp is not None
        return emp

    def get_employee_by_code(self, company_id: UUID, employee_code: str) -> Employee | None:
        row = self._execute(
            self._base(company_id).where(func.upper(EmployeeModel.employee_code) == employee_code.upper())
        ).one_or_none()
        return self._to_domain(*row) if row else None

    def search_employees(
        self,
        company_id: UUID,
        *,
        department: str | None = None,
        team: str | None = None,
        job_title: str | None = None,
        skill_names: tuple[str, ...] = (),
        status: EmploymentStatus | None = None,
        limit: int = 25,
    ) -> Page[Employee]:
        """Case-insensitive filters; every listed skill must be held (any level, any verification)."""
        stmt = self._base(company_id)
        if department:
            stmt = stmt.where(func.lower(DepartmentModel.name) == department.strip().lower())
        if team:
            stmt = stmt.where(func.lower(TeamModel.name) == team.strip().lower())
        if job_title:
            stmt = stmt.where(EmployeeModel.job_title.ilike(f"%{job_title.strip()}%"))
        if status is not None:
            stmt = stmt.where(EmployeeModel.employment_status == status)
        for name in skill_names:
            stmt = stmt.where(
                exists()
                .where(EmployeeSkillModel.employee_id == EmployeeModel.id)
                .where(EmployeeSkillModel.skill_id == SkillModel.id)
                .where(func.lower(SkillModel.name) == name.strip().lower())
            )
        total = self._execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = self._execute(stmt.order_by(EmployeeModel.employee_code).limit(limit)).all()
        return Page[Employee](
            items=tuple(self._to_domain(*r) for r in rows), total=total, limit=limit, offset=0
        )

    def get_profiles(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, EmployeeProfile]:
        if not employee_ids:
            return {}
        rows = self._execute(self._base(company_id).where(EmployeeModel.id.in_(employee_ids))).all()
        employees = {r[0].id: self._to_domain(*r) for r in rows}
        ids = list(employees)
        if not ids:
            return {}

        skills: dict[UUID, list[EmployeeSkill]] = defaultdict(list)
        for es, name in self._execute(
            select(EmployeeSkillModel, SkillModel.name)
            .join(SkillModel, SkillModel.id == EmployeeSkillModel.skill_id)
            .where(EmployeeSkillModel.employee_id.in_(ids))
            .order_by(SkillModel.name)
        ).all():
            skills[es.employee_id].append(
                EmployeeSkill(
                    skill_id=es.skill_id,
                    skill_name=name,
                    proficiency_level=es.proficiency_level,
                    verification_status=es.verification_status,
                    verified_at=es.verified_at,
                    last_assessed_at=es.last_assessed_at,
                )
            )

        evidence: dict[UUID, list[SkillEvidence]] = defaultdict(list)
        for ev, emp_id, skill_id in self._execute(
            select(EmployeeSkillEvidenceModel, EmployeeSkillModel.employee_id, EmployeeSkillModel.skill_id)
            .join(EmployeeSkillModel, EmployeeSkillModel.id == EmployeeSkillEvidenceModel.employee_skill_id)
            .where(EmployeeSkillModel.employee_id.in_(ids))
            .order_by(EmployeeSkillEvidenceModel.created_at)
        ).all():
            evidence[emp_id].append(
                SkillEvidence(
                    skill_id=skill_id,
                    evidence_type=ev.evidence_type,
                    verification_status=ev.verification_status,
                    project_id=ev.project_id,
                    task_id=ev.task_id,
                    notes=ev.notes,
                    created_at=ev.created_at,
                )
            )

        exp_skills: dict[UUID, list[tuple[UUID, str]]] = defaultdict(list)
        for exp_id, skill_id, name in self._execute(
            select(EmployeeExperienceSkillModel.experience_id, SkillModel.id, SkillModel.name)
            .join(SkillModel, SkillModel.id == EmployeeExperienceSkillModel.skill_id)
            .join(
                EmployeeExperienceModel,
                EmployeeExperienceModel.id == EmployeeExperienceSkillModel.experience_id,
            )
            .where(EmployeeExperienceModel.employee_id.in_(ids))
            .order_by(SkillModel.name)
        ).all():
            exp_skills[exp_id].append((skill_id, name))
        experiences: dict[UUID, list[Experience]] = defaultdict(list)
        for ex in self._execute(
            select(EmployeeExperienceModel)
            .where(EmployeeExperienceModel.employee_id.in_(ids))
            .order_by(EmployeeExperienceModel.relevant_start_date)
        ).scalars():
            sk = exp_skills.get(ex.id, [])
            experiences[ex.employee_id].append(
                Experience(
                    id=ex.id,
                    organization_or_project=ex.organization_or_project,
                    role=ex.role,
                    description=ex.description,
                    start_date=ex.relevant_start_date,
                    end_date=ex.relevant_end_date,
                    skill_ids=tuple(s for s, _ in sk),
                    skill_names=tuple(n for _, n in sk),
                )
            )

        credentials: dict[UUID, list[Credential]] = defaultdict(list)
        for c in self._execute(
            select(EmployeeCredentialModel)
            .where(EmployeeCredentialModel.employee_id.in_(ids))
            .order_by(EmployeeCredentialModel.issued_at)
        ).scalars():
            credentials[c.employee_id].append(
                Credential(
                    name=c.name,
                    issuer=c.issuer,
                    credential_type=c.credential_type,
                    skill_id=c.skill_id,
                    issued_at=c.issued_at,
                    expires_at=c.expires_at,
                    verification_status=c.verification_status,
                )
            )

        memberships: dict[UUID, list[ProjectMembership]] = defaultdict(list)
        for pm, pname in self._execute(
            select(ProjectMemberModel, ProjectModel.name)
            .join(ProjectModel, ProjectModel.id == ProjectMemberModel.project_id)
            .where(ProjectMemberModel.employee_id.in_(ids))
            .order_by(ProjectModel.name)
        ).all():
            memberships[pm.employee_id].append(
                ProjectMembership(project_id=pm.project_id, project_name=pname, role=pm.role)
            )

        return {
            eid: EmployeeProfile(
                employee=emp,
                skills=tuple(skills.get(eid, [])),
                evidence=tuple(evidence.get(eid, [])),
                experiences=tuple(experiences.get(eid, [])),
                credentials=tuple(credentials.get(eid, [])),
                memberships=tuple(memberships.get(eid, [])),
            )
            for eid, emp in employees.items()
        }

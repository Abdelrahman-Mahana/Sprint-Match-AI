from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy import exc as sa_exc

from app.core.exceptions import ConflictError
from app.domain.entities.employee import Skill
from app.domain.entities.organization import Sprint
from app.domain.entities.task import RequiredSkill, TaskDependencyRef, TaskRecord
from app.domain.enums import RequirementSource, TaskCategory, TaskComplexity, TaskPriority, TaskStatus
from app.domain.interfaces import NewTask, Page
from app.infrastructure.database.models import (
    ProjectModel,
    SkillModel,
    SprintModel,
    TaskDependencyModel,
    TaskModel,
    TaskRequiredSkillModel,
)
from app.infrastructure.database.repositories.base import SqlRepository, to_float, translate_db_errors


class SqlTaskRepository(SqlRepository):
    def _required_skills(self, task_ids: list[UUID]) -> dict[UUID, list[RequiredSkill]]:
        result: dict[UUID, list[RequiredSkill]] = defaultdict(list)
        if not task_ids:
            return result
        for rs, name in self._execute(
            select(TaskRequiredSkillModel, SkillModel.name)
            .join(SkillModel, SkillModel.id == TaskRequiredSkillModel.skill_id)
            .where(TaskRequiredSkillModel.task_id.in_(task_ids))
            .order_by(TaskRequiredSkillModel.is_mandatory.desc(), SkillModel.name)
        ).all():
            result[rs.task_id].append(
                RequiredSkill(
                    skill_id=rs.skill_id,
                    skill_name=name,
                    minimum_level=rs.minimum_level,
                    mandatory=rs.is_mandatory,
                    weight=float(rs.weight),
                    requires_verification=rs.requires_verification,
                    source=RequirementSource.TASK_RECORD,
                )
            )
        return result

    def _dependencies(self, task_ids: list[UUID]) -> dict[UUID, list[TaskDependencyRef]]:
        result: dict[UUID, list[TaskDependencyRef]] = defaultdict(list)
        if not task_ids:
            return result
        for dep, other in self._execute(
            select(TaskDependencyModel, TaskModel)
            .join(TaskModel, TaskModel.id == TaskDependencyModel.depends_on_task_id)
            .where(TaskDependencyModel.task_id.in_(task_ids))
            .order_by(TaskModel.task_code)
        ).all():
            result[dep.task_id].append(
                TaskDependencyRef(
                    task_id=other.id,
                    task_code=other.task_code,
                    title=other.title,
                    status=other.status,
                    dependency_type=dep.dependency_type,
                )
            )
        return result

    @staticmethod
    def _to_domain(
        m: TaskModel,
        project_name: str | None,
        skills: list[RequiredSkill],
        dependencies: list[TaskDependencyRef] | None = None,
    ) -> TaskRecord:
        return TaskRecord(
            id=m.id,
            company_id=m.company_id,
            task_code=m.task_code,
            project_id=m.project_id,
            project_name=project_name,
            sprint_id=m.sprint_id,
            title=m.title,
            description=m.description,
            complexity=m.complexity,
            priority=m.priority,
            status=m.status,
            estimated_hours=to_float(m.estimated_hours),
            remaining_estimated_hours=to_float(m.remaining_estimated_hours),
            planned_start=m.planned_start,
            deadline=m.deadline,
            task_category=m.task_category,
            restricted_to_team_id=m.restricted_to_team_id,
            requires_project_membership=m.requires_project_membership,
            acceptance_criteria=m.acceptance_criteria,
            required_skills=tuple(skills),
            required_certifications=tuple(m.required_certifications or ()),
            dependencies=tuple(dependencies or ()),
        )

    def _select(self, company_id: UUID) -> Select[TaskModel, str]:
        return (
            select(TaskModel, ProjectModel.name)
            .outerjoin(ProjectModel, ProjectModel.id == TaskModel.project_id)
            .where(TaskModel.company_id == company_id)
        )

    def _one(self, stmt: Select[TaskModel, str]) -> TaskRecord | None:
        row = self._execute(stmt).one_or_none()
        if row is None:
            return None
        task_id = row[0].id
        return self._to_domain(
            row[0],
            row[1],
            self._required_skills([task_id]).get(task_id, []),
            self._dependencies([task_id]).get(task_id, []),
        )

    def get_task(self, company_id: UUID, task_id: UUID) -> TaskRecord | None:
        return self._one(self._select(company_id).where(TaskModel.id == task_id))

    def get_task_by_code(self, company_id: UUID, task_code: str) -> TaskRecord | None:
        return self._one(self._select(company_id).where(TaskModel.task_code == task_code))

    def list_tasks(
        self,
        company_id: UUID,
        *,
        status: TaskStatus | None = None,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[TaskRecord]:
        stmt = self._select(company_id)
        if status is not None:
            stmt = stmt.where(TaskModel.status == status)
        if project_id is not None:
            stmt = stmt.where(TaskModel.project_id == project_id)
        if sprint_id is not None:
            stmt = stmt.where(TaskModel.sprint_id == sprint_id)
        total = self._execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = self._execute(stmt.order_by(TaskModel.task_code).limit(limit).offset(offset)).all()
        ids = [r[0].id for r in rows]
        skills, deps = self._required_skills(ids), self._dependencies(ids)
        return Page[TaskRecord](
            items=tuple(
                self._to_domain(r[0], r[1], skills.get(r[0].id, []), deps.get(r[0].id, [])) for r in rows
            ),
            total=total,
            limit=limit,
            offset=offset,
        )

    def create_task(self, company_id: UUID, task: NewTask) -> TaskRecord:
        model = TaskModel(
            company_id=company_id,
            task_code=task.task_code,
            project_id=task.project_id,
            sprint_id=task.sprint_id,
            title=task.title,
            description=task.description,
            acceptance_criteria=task.acceptance_criteria,
            complexity=TaskComplexity(task.complexity) if task.complexity else None,
            priority=TaskPriority(task.priority) if task.priority else None,
            status=TaskStatus.TODO,
            estimated_hours=task.estimated_hours,
            remaining_estimated_hours=task.estimated_hours,
            planned_start=task.planned_start,
            deadline=task.deadline,
            task_category=TaskCategory(task.task_category) if task.task_category else None,
            restricted_to_team_id=task.restricted_to_team_id,
            requires_project_membership=task.requires_project_membership,
            required_certifications=list(task.required_certifications),
        )
        try:
            with translate_db_errors():
                self._s.add(model)
                self._s.flush()
                for rs in task.required_skills:
                    self._s.add(
                        TaskRequiredSkillModel(
                            task_id=model.id,
                            skill_id=rs.skill_id,
                            minimum_level=rs.minimum_level,
                            is_mandatory=rs.mandatory,
                            requires_verification=rs.requires_verification,
                            weight=rs.weight,
                        )
                    )
                self._s.flush()
        except sa_exc.IntegrityError as exc:
            raise ConflictError(
                f"Task code {task.task_code!r} already exists or references invalid data"
            ) from exc
        created = self.get_task(company_id, model.id)
        assert created is not None
        return created

    def get_skill_catalog(self, company_id: UUID) -> list[Skill]:
        return [
            Skill(id=m.id, name=m.name, category=m.category, aliases=tuple(m.aliases or ()))
            for m in self._execute(
                select(SkillModel).where(SkillModel.company_id == company_id).order_by(SkillModel.name)
            ).scalars()
        ]

    def get_sprint(self, company_id: UUID, sprint_id: UUID) -> Sprint | None:
        m = self._execute(
            select(SprintModel)
            .join(ProjectModel, ProjectModel.id == SprintModel.project_id)
            .where(SprintModel.id == sprint_id, ProjectModel.company_id == company_id)
        ).scalar_one_or_none()
        if m is None:
            return None
        return Sprint(
            id=m.id,
            project_id=m.project_id,
            name=m.name,
            start_date=m.start_date,
            end_date=m.end_date,
            status=m.status.value,
        )

    def project_exists(self, company_id: UUID, project_id: UUID) -> bool:
        return (
            self._execute(
                select(func.count()).where(
                    ProjectModel.id == project_id, ProjectModel.company_id == company_id
                )
            ).scalar_one()
            > 0
        )

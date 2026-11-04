from __future__ import annotations

from uuid import UUID

from app.core.exceptions import InputValidationError, NotFoundError
from app.domain.entities.organization import Company
from app.domain.entities.task import RequiredSkill, SkillRequirementInput, TaskAnalysis, TaskInput, TaskRecord
from app.domain.enums import RequirementSource, TaskStatus
from app.domain.interfaces import NewTask, Page, UnitOfWork
from app.intelligence.task_analysis.analyzer import ANALYZER_VERSION, TaskAnalyzer


def task_input_from_record(task: TaskRecord) -> TaskInput:
    return TaskInput(
        task_id=task.id,
        title=task.title,
        description=task.description,
        project_id=task.project_id,
        sprint_id=task.sprint_id,
        priority=task.priority,
        complexity=task.complexity,
        estimated_hours=task.remaining_estimated_hours or task.estimated_hours,
        planned_start=task.planned_start,
        deadline=task.deadline,
        task_category=task.task_category,
        acceptance_criteria=task.acceptance_criteria,
        required_skills=tuple(
            SkillRequirementInput(
                skill_name=s.skill_name,
                minimum_level=s.minimum_level,
                mandatory=s.mandatory,
                weight=s.weight,
                requires_verification=s.requires_verification,
            )
            for s in task.required_skills
        ),
        required_certifications=task.required_certifications,
        dependencies=tuple(d.task_code for d in task.dependencies),
        restricted_to_team_id=task.restricted_to_team_id,
        requires_project_membership=task.requires_project_membership,
        requirements_source=RequirementSource.TASK_RECORD,
    )


class TaskService:
    def __init__(self, uow: UnitOfWork, analyzer: TaskAnalyzer) -> None:
        self.uow = uow
        self.analyzer = analyzer

    def get(
        self, company: Company, *, task_id: UUID | None = None, task_code: str | None = None
    ) -> TaskRecord:
        task = (
            self.uow.tasks.get_task(company.id, task_id)
            if task_id
            else self.uow.tasks.get_task_by_code(company.id, task_code or "")
        )
        if task is None:
            raise NotFoundError("Task not found")
        return task

    def list(
        self,
        company: Company,
        *,
        status: TaskStatus | None = None,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[TaskRecord]:
        return self.uow.tasks.list_tasks(
            company.id, status=status, project_id=project_id, sprint_id=sprint_id, limit=limit, offset=offset
        )

    def create(self, company: Company, task_code: str, spec: TaskInput) -> TaskRecord:
        if spec.project_id and not self.uow.tasks.project_exists(company.id, spec.project_id):
            raise InputValidationError("Unknown project_id")
        catalog = {s.name.lower(): s for s in self.uow.tasks.get_skill_catalog(company.id)}
        skills = []
        for req in spec.required_skills:
            skill = catalog.get(req.skill_name.lower())
            if skill is None:
                raise InputValidationError(
                    f"Unknown skill {req.skill_name!r}", details={"skill": req.skill_name}
                )
            skills.append(
                RequiredSkill(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    minimum_level=req.minimum_level,
                    mandatory=req.mandatory,
                    weight=req.weight or (1.0 if req.mandatory else 0.5),
                    requires_verification=req.requires_verification,
                )
            )
        return self.uow.tasks.create_task(
            company.id,
            NewTask(
                task_code=task_code,
                title=spec.title,
                description=spec.description,
                project_id=spec.project_id,
                sprint_id=spec.sprint_id,
                complexity=spec.complexity.value if spec.complexity else None,
                priority=spec.priority.value if spec.priority else None,
                estimated_hours=spec.estimated_hours,
                planned_start=spec.planned_start,
                deadline=spec.deadline,
                task_category=spec.task_category.value if spec.task_category else None,
                acceptance_criteria=spec.acceptance_criteria,
                required_skills=tuple(skills),
                required_certifications=spec.required_certifications,
            ),
        )

    def analyze(self, company: Company, task: TaskInput, *, persist: bool = True) -> TaskAnalysis:
        analysis = self.analyzer.analyze(task, self.uow.tasks.get_skill_catalog(company.id))
        if persist:
            self.uow.recommendations.save_task_analysis(
                company.id, task.task_id, analysis.model_dump(mode="json"), ANALYZER_VERSION
            )
        return analysis

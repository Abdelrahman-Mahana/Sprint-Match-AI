from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import exc as sa_exc
from sqlalchemy import func, select

from app.core.exceptions import ConflictError, InputValidationError, NotFoundError
from app.domain.entities.performance import (
    PerformanceEvaluation,
    PerformanceMetricSnapshot,
    TaskOutcomeRecord,
)
from app.domain.enums import AssignmentStatus, TaskStatus
from app.domain.interfaces import EvaluationSummary, NewTaskOutcome
from app.infrastructure.database.models import (
    EmployeeModel,
    PerformanceEvaluationModel,
    PerformanceMetricModel,
    RecommendationRunModel,
    TaskAssignmentModel,
    TaskModel,
    TaskOutcomeModel,
    TaskRequiredSkillModel,
)
from app.infrastructure.database.repositories.base import SqlRepository, to_float, translate_db_errors


class SqlPerformanceRepository(SqlRepository):
    def get_outcomes(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, list[TaskOutcomeRecord]]:
        if not employee_ids:
            return {}
        rows = self._execute(
            select(TaskOutcomeModel, TaskModel)
            .join(TaskModel, TaskModel.id == TaskOutcomeModel.task_id)
            .where(TaskModel.company_id == company_id, TaskOutcomeModel.employee_id.in_(employee_ids))
            .order_by(TaskOutcomeModel.reviewed_at, TaskModel.task_code)
        ).all()
        task_ids = list({t.id for _, t in rows})
        skills: dict[UUID, list[UUID]] = defaultdict(list)
        if task_ids:
            for task_id, skill_id in self._execute(
                select(TaskRequiredSkillModel.task_id, TaskRequiredSkillModel.skill_id).where(
                    TaskRequiredSkillModel.task_id.in_(task_ids)
                )
            ).all():
                skills[task_id].append(skill_id)
        result: dict[UUID, list[TaskOutcomeRecord]] = defaultdict(list)
        for o, t in rows:
            result[o.employee_id].append(
                TaskOutcomeRecord(
                    task_id=t.id,
                    task_code=t.task_code,
                    project_id=t.project_id,
                    task_category=t.task_category,
                    complexity=t.complexity,
                    required_skill_ids=tuple(sorted(skills.get(t.id, []))),
                    quality_score=to_float(o.quality_score),
                    accepted=o.accepted,
                    rework_count=o.rework_count,
                    on_time=o.on_time,
                    delay_reason=o.delay_reason,
                    blocker_hours=float(o.blocker_hours or 0),
                    reviewed_at=o.reviewed_at,
                )
            )
        return dict(result)

    def get_project_history(self, company_id: UUID, employee_ids: list[UUID]) -> dict[UUID, dict[UUID, int]]:
        if not employee_ids:
            return {}
        result: dict[UUID, dict[UUID, int]] = defaultdict(dict)
        for emp_id, project_id, n in self._execute(
            select(TaskAssignmentModel.employee_id, TaskModel.project_id, func.count())
            .join(TaskModel, TaskModel.id == TaskAssignmentModel.task_id)
            .where(
                TaskModel.company_id == company_id,
                TaskModel.project_id.is_not(None),
                TaskAssignmentModel.employee_id.in_(employee_ids),
                TaskAssignmentModel.assignment_status == AssignmentStatus.COMPLETED,
            )
            .group_by(TaskAssignmentModel.employee_id, TaskModel.project_id)
        ).all():
            result[emp_id][project_id] = int(n)
        return dict(result)

    def record_outcome(self, company_id: UUID, outcome: NewTaskOutcome) -> UUID:
        task_status = self._execute(
            select(TaskModel.status).where(
                TaskModel.id == outcome.task_id, TaskModel.company_id == company_id
            )
        ).scalar_one_or_none()
        emp_ok = self._execute(
            select(func.count()).where(
                EmployeeModel.id == outcome.employee_id, EmployeeModel.company_id == company_id
            )
        ).scalar_one()
        if task_status is None or not emp_ok:
            raise NotFoundError("Task or employee not found")
        # Outcomes are task evidence: only for completed tasks, only for employees who worked on them.
        if task_status != TaskStatus.DONE:
            raise InputValidationError("Outcomes can only be recorded for DONE tasks")
        assigned = self._execute(
            select(func.count()).where(
                TaskAssignmentModel.task_id == outcome.task_id,
                TaskAssignmentModel.employee_id == outcome.employee_id,
            )
        ).scalar_one()
        if not assigned:
            raise InputValidationError("The employee was not assigned to this task")
        model = TaskOutcomeModel(**outcome.model_dump())
        try:
            with translate_db_errors():
                self._s.add(model)
                self._s.flush()
        except sa_exc.IntegrityError as exc:
            raise ConflictError("An outcome for this task and employee already exists") from exc
        return model.id

    def evaluation_summary(self, company_id: UUID) -> EvaluationSummary:
        by_status = {
            str(s.value): int(n)
            for s, n in self._execute(
                select(RecommendationRunModel.status, func.count())
                .where(RecommendationRunModel.company_id == company_id)
                .group_by(RecommendationRunModel.status)
            ).all()
        }
        avg_cov = self._execute(
            select(func.avg(RecommendationRunModel.data_coverage)).where(
                RecommendationRunModel.company_id == company_id,
                RecommendationRunModel.primary_employee_id.is_not(None),
            )
        ).scalar_one()
        # Latest recommendation per task, to compare against who actually delivered it.
        latest = (
            select(
                RecommendationRunModel.task_id,
                RecommendationRunModel.primary_employee_id,
                func.row_number()
                .over(
                    partition_by=RecommendationRunModel.task_id,
                    order_by=RecommendationRunModel.generated_at.desc(),
                )
                .label("rn"),
            )
            .where(
                RecommendationRunModel.company_id == company_id, RecommendationRunModel.task_id.is_not(None)
            )
            .subquery()
        )
        rows = self._execute(
            select(TaskOutcomeModel.employee_id, TaskOutcomeModel.quality_score, latest.c.primary_employee_id)
            .join(TaskModel, TaskModel.id == TaskOutcomeModel.task_id)
            .join(latest, (latest.c.task_id == TaskOutcomeModel.task_id) & (latest.c.rn == 1))
            .where(TaskModel.company_id == company_id)
        ).all()
        matched = [float(q) for emp, q, primary in rows if emp == primary and q is not None]
        others = [float(q) for emp, q, primary in rows if emp != primary and q is not None]
        total_outcomes = self._execute(
            select(func.count())
            .select_from(TaskOutcomeModel)
            .join(TaskModel, TaskModel.id == TaskOutcomeModel.task_id)
            .where(TaskModel.company_id == company_id)
        ).scalar_one()
        return EvaluationSummary(
            recommendations_by_status=by_status,
            average_primary_coverage=round(float(avg_cov), 4) if avg_cov is not None else None,
            outcomes_recorded=int(total_outcomes),
            outcomes_for_recommended_primary=len([1 for emp, _, primary in rows if emp == primary]),
            average_quality_recommended_primary=round(sum(matched) / len(matched), 2) if matched else None,
            average_quality_other_assignees=round(sum(others) / len(others), 2) if others else None,
        )

    def get_evaluations(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 12
    ) -> list[PerformanceEvaluation]:
        rows = self._execute(
            select(PerformanceEvaluationModel)
            .where(
                PerformanceEvaluationModel.company_id == company_id,
                PerformanceEvaluationModel.employee_id == employee_id,
            )
            .order_by(PerformanceEvaluationModel.period_end.desc())
            .limit(limit)
        ).scalars()
        return [
            PerformanceEvaluation(
                period_start=m.period_start,
                period_end=m.period_end,
                technical_quality=to_float(m.technical_quality),
                delivery=to_float(m.delivery),
                collaboration=to_float(m.collaboration),
                summary=m.summary,
            )
            for m in rows
        ]

    def get_metric_snapshots(
        self, company_id: UUID, employee_id: UUID, *, limit: int = 60
    ) -> list[PerformanceMetricSnapshot]:
        rows = self._execute(
            select(PerformanceMetricModel)
            .where(
                PerformanceMetricModel.company_id == company_id,
                PerformanceMetricModel.employee_id == employee_id,
            )
            .order_by(PerformanceMetricModel.period_end.desc(), PerformanceMetricModel.metric)
            .limit(limit)
        ).scalars()
        return [
            PerformanceMetricSnapshot(
                metric=m.metric,
                period_start=m.period_start,
                period_end=m.period_end,
                value=to_float(m.value),
                sample_size=m.sample_size,
            )
            for m in rows
        ]

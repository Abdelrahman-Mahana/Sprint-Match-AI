from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api.deps import Manager, ServicesDep, Viewer
from app.domain.interfaces import EvaluationSummary, NewTaskOutcome
from app.schemas.common import CreatedOut, TaskOutcomeRequest

router = APIRouter(tags=["evaluation"])


@router.post("/task-outcomes", response_model=CreatedOut, status_code=status.HTTP_201_CREATED)
def record_task_outcome(body: TaskOutcomeRequest, ctx: Manager, svc: ServicesDep) -> CreatedOut:
    outcome_id = svc.uow.performance.record_outcome(
        ctx.company.id,
        NewTaskOutcome(
            **body.model_dump(exclude={"reviewed_at"}), reviewed_at=body.reviewed_at or datetime.now(UTC)
        ),
    )
    svc.uow.audit.record(ctx.company.id, ctx.actor, "task_outcome.recorded", "task_outcome", str(outcome_id))
    svc.uow.commit()
    return CreatedOut(id=outcome_id)


@router.get("/evaluation/metrics", response_model=EvaluationSummary)
@router.get("/evaluation/summary", response_model=EvaluationSummary)
def evaluation_summary(ctx: Viewer, svc: ServicesDep) -> EvaluationSummary:
    return svc.uow.performance.evaluation_summary(ctx.company.id)

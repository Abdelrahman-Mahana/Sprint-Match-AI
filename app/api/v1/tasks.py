from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Manager, ServicesDep, Viewer, rate_limited
from app.domain.entities.task import TaskAnalysis, TaskRecord
from app.domain.enums import TaskStatus
from app.schemas.common import TaskPage
from app.schemas.recommendations import TaskSpecification

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskRecord, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskSpecification,
    task_code: Annotated[str, Query(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")],
    ctx: Manager,
    svc: ServicesDep,
) -> TaskRecord:
    task = svc.tasks.create(ctx.company, task_code, body.to_task_input())
    svc.uow.audit.record(
        ctx.company.id, ctx.actor, "task.created", "task", str(task.id), {"task_code": task_code}
    )
    svc.uow.commit()
    return task


@router.get("", response_model=TaskPage)
def list_tasks(
    ctx: Viewer,
    svc: ServicesDep,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    project_id: UUID | None = None,
    sprint_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskPage:
    page = svc.tasks.list(
        ctx.company,
        status=task_status,
        project_id=project_id,
        sprint_id=sprint_id,
        limit=limit,
        offset=offset,
    )
    return TaskPage(items=list(page.items), total=page.total, limit=page.limit, offset=page.offset)


@router.post("/analyze", response_model=TaskAnalysis, dependencies=[Depends(rate_limited)])
def analyze_task(body: TaskSpecification, ctx: Manager, svc: ServicesDep) -> TaskAnalysis:
    analysis = svc.tasks.analyze(ctx.company, body.to_task_input(), persist=True)
    svc.uow.commit()
    return analysis


@router.get("/{task_id}", response_model=TaskRecord)
def get_task(task_id: UUID, ctx: Viewer, svc: ServicesDep) -> TaskRecord:
    return svc.tasks.get(ctx.company, task_id=task_id)

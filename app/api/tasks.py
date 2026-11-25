"""Employee lookup and task assignment/analysis endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import AsOfDep, Manager, ServicesDep, Viewer, WindowDep, rate_limited
from app.application.services import Services
from app.domain import (
    AssignmentStatus,
    Employee,
    EmploymentStatus,
    LeaveStatus,
    TaskAnalysis,
    TaskComplexity,
    TaskPriority,
    TaskRecord,
    TaskStatus,
)
from app.infrastructure.orm import (
    EmployeeCommitmentModel,
    EmployeeModel,
    EmployeeSkillModel,
    LeaveRecordModel,
    SkillModel,
    TaskAssignmentModel,
    TaskModel,
    WorkScheduleDayModel,
)
from app.infrastructure.repositories import SqlAlchemyUnitOfWork
from app.schemas import (
    AvailabilityOut,
    Employee360Out,
    EmployeePage,
    PerformanceOut,
    TaskPage,
    TaskSpecification,
)

# ===========================================================================
# Employee Endpoints (/employees/*)
# ===========================================================================

employees_router = APIRouter(prefix="/employees", tags=["employees"])


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    experience_years: float | None = Field(default=None, ge=0, le=50)
    employment_status: EmploymentStatus | None = None
    email: str | None = Field(default=None, max_length=254)


@employees_router.get("", response_model=EmployeePage)
def list_employees(
    ctx: Viewer,
    svc: ServicesDep,
    status: EmploymentStatus | None = None,
    department_id: UUID | None = None,
    skill: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EmployeePage:
    page = svc.employees.list_employees(
        ctx.company, status=status, department_id=department_id, skill=skill, limit=limit, offset=offset
    )
    return EmployeePage(items=list(page.items), total=page.total, limit=page.limit, offset=page.offset)


@employees_router.get("/{employee_id}", response_model=Employee)
def get_employee(employee_id: UUID, ctx: Viewer, svc: ServicesDep) -> Employee:
    return svc.employees.get(ctx.company, employee_id)


@employees_router.get("/{employee_id}/360", response_model=Employee360Out)
def get_employee_360(
    employee_id: UUID, ctx: Viewer, svc: ServicesDep, window: WindowDep, as_of: AsOfDep
) -> Employee360Out:
    resolved, _ = svc.workforce.resolve_window(ctx.company, as_of, window)
    data = svc.employees.get_360(ctx.company, employee_id, as_of=as_of, window=resolved)
    return Employee360Out(data=data, window=resolved)


@employees_router.get("/{employee_id}/availability", response_model=AvailabilityOut)
def get_employee_availability(
    employee_id: UUID, ctx: Viewer, svc: ServicesDep, window: WindowDep, as_of: AsOfDep
) -> AvailabilityOut:
    resolved, _ = svc.workforce.resolve_window(ctx.company, as_of, window)
    employee = svc.employees.get(ctx.company, employee_id)
    return AvailabilityOut(
        employee_id=employee_id,
        employee_code=employee.employee_code,
        full_name=employee.full_name,
        availability=svc.employees.availability(ctx.company, employee_id, resolved, as_of),
    )


@employees_router.get("/{employee_id}/performance", response_model=PerformanceOut)
def get_employee_performance(employee_id: UUID, ctx: Viewer, svc: ServicesDep) -> PerformanceOut:
    perf = svc.employees.performance(ctx.company, employee_id)
    return PerformanceOut(summary=perf.summary, outcome_count=len(perf.outcomes))


@employees_router.patch("/{employee_id}", response_model=Employee)
def update_employee(
    employee_id: UUID,
    body: EmployeeUpdate,
    ctx: Manager,
    svc: ServicesDep,
) -> Employee:
    return svc.employees.update_employee(
        ctx.company,
        employee_id,
        full_name=body.full_name,
        job_title=body.job_title,
        experience_years=body.experience_years,
        employment_status=body.employment_status,
        email=body.email,
    )

# ===========================================================================
# Task Endpoints (/tasks/*)
# ===========================================================================

tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


def _db_session(svc: Services) -> Session:
    if not isinstance(svc.uow, SqlAlchemyUnitOfWork):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Task write operations require the native SQLAlchemy database adapter.",
        )
    return svc.uow.session


class TaskAssignRequest(BaseModel):
    employee_id: UUID
    task_code: str
    title: str
    description: str | None = None
    estimated_hours: float = 16.0
    priority: str = "high"
    complexity: str = "medium"
    planned_start: datetime | None = None
    deadline: datetime | None = None
    required_skills: list[str] = Field(default_factory=list)
    project_id: UUID | None = None
    sprint_id: UUID | None = None


class TaskUnassignRequest(BaseModel):
    task_code: str
    employee_id: UUID | None = None


@tasks_router.get("/assigned")
def list_assigned_tasks(ctx: Viewer, svc: ServicesDep) -> list[dict[str, Any]]:
    """Return tasks that have an active, planned or completed employee assignment."""
    if not isinstance(svc.uow, SqlAlchemyUnitOfWork):
        return []
    query = text("""
        SELECT
            t.id AS task_id,
            t.task_code,
            t.title,
            t.description,
            t.status AS task_status,
            t.priority,
            t.estimated_hours,
            p.name AS project_name,
            a.assignment_status,
            a.allocated_hours,
            e.id AS employee_id,
            e.employee_code,
            e.full_name AS employee_name,
            e.job_title,
            d.name AS department_name
        FROM task_assignments a
        JOIN tasks t ON t.id = a.task_id
        JOIN employees e ON e.id = a.employee_id
        LEFT JOIN departments d ON d.id = e.department_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.company_id = :company_id
        ORDER BY a.assigned_at DESC
        LIMIT 100;
    """)
    rows = _db_session(svc).execute(query, {"company_id": ctx.company.id}).mappings().all()
    results = []
    for r in rows:
        d = dict(r)
        d["task_id"] = str(d["task_id"])
        d["employee_id"] = str(d["employee_id"])
        if d.get("allocated_hours") is not None:
            d["allocated_hours"] = float(d["allocated_hours"])
        results.append(d)
    return results


@tasks_router.post("/assign", status_code=status.HTTP_200_OK)
def assign_task_to_employee(
    body: TaskAssignRequest,
    ctx: Manager,
    svc: ServicesDep,
) -> dict[str, Any]:
    """Validate employment status, leave, capacity, and skills before persisting a task assignment."""
    session = _db_session(svc)
    now = datetime.now(UTC)
    task_start = body.planned_start or now
    task_end = body.deadline or (task_start + timedelta(days=5))

    # Re-fetch employee from real database
    employee = (
        session.query(EmployeeModel)
        .filter(
            EmployeeModel.id == body.employee_id,
            EmployeeModel.company_id == ctx.company.id,
        )
        .first()
    )
    if not employee:
        raise HTTPException(status_code=404, detail="Employee record not found in database.")

    if employee.employment_status != EmploymentStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Assignment rejected: Employee {employee.full_name} is inactive ({employee.employment_status.value}).",
        )

    overlapping_leave = (
        session.query(LeaveRecordModel)
        .filter(
            LeaveRecordModel.employee_id == body.employee_id,
            LeaveRecordModel.status == LeaveStatus.APPROVED,
            LeaveRecordModel.start_at < task_end,
            LeaveRecordModel.end_at > task_start,
        )
        .first()
    )
    if overlapping_leave:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Assignment rejected: Employee {employee.full_name} is on approved leave ({overlapping_leave.leave_type.value}) during this task window.",
        )

    commitments = (
        session.query(EmployeeCommitmentModel)
        .filter(
            EmployeeCommitmentModel.employee_id == body.employee_id,
            EmployeeCommitmentModel.start_at < task_end,
            EmployeeCommitmentModel.end_at > task_start,
        )
        .all()
    )
    comm_hours = sum(float(c.hours or 0.0) for c in commitments)

    if comm_hours >= 100.0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Assignment rejected: Employee {employee.full_name} is currently BUSY and fully committed "
                f"({comm_hours:.1f}h total commitments). Available capacity is 0.0h."
            ),
        )

    active_assignments = (
        session.query(TaskAssignmentModel)
        .filter(
            TaskAssignmentModel.employee_id == body.employee_id,
            TaskAssignmentModel.assignment_status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED]),
        )
        .all()
    )
    assigned_hours = sum(
        float(a.remaining_estimated_hours or a.allocated_hours or 0.0) for a in active_assignments
    )

    schedules = (
        session.query(WorkScheduleDayModel)
        .filter(
            WorkScheduleDayModel.employee_id == body.employee_id,
        )
        .all()
    )
    if schedules:
        weekly_cap = 0.0
        for s in schedules:
            diff = (s.end_time.hour + s.end_time.minute / 60.0) - (
                s.start_time.hour + s.start_time.minute / 60.0
            )
            diff -= (s.break_minutes or 0) / 60.0
            weekly_cap += max(0.0, diff)
    else:
        weekly_cap = 35.0

    horizon_days = max(1, (task_end.date() - task_start.date()).days)
    daily_cap = weekly_cap / 5.0
    window_cap = daily_cap * min(horizon_days, 5)
    free_capacity = max(0.0, window_cap - assigned_hours - comm_hours)

    if free_capacity < body.estimated_hours:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Assignment rejected: Employee {employee.full_name} has insufficient available capacity "
                f"({free_capacity:.1f}h free in window, task requires {body.estimated_hours:.1f}h)."
            ),
        )

    if body.required_skills:
        emp_skills = (
            session.query(EmployeeSkillModel, SkillModel)
            .join(SkillModel, SkillModel.id == EmployeeSkillModel.skill_id)
            .filter(EmployeeSkillModel.employee_id == body.employee_id)
            .all()
        )
        emp_skill_names = {s.name.lower() for _, s in emp_skills}
        missing = [r for r in body.required_skills if r.lower() not in emp_skill_names]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Assignment rejected: Employee {employee.full_name} lacks required skill(s): {', '.join(missing)}.",
            )

    task = (
        session.query(TaskModel)
        .filter(
            TaskModel.company_id == ctx.company.id,
            TaskModel.task_code == body.task_code,
        )
        .first()
    )

    try:
        priority_enum = TaskPriority(body.priority.lower())
    except (ValueError, AttributeError):
        priority_enum = TaskPriority.HIGH

    try:
        complexity_enum = TaskComplexity(body.complexity.lower())
    except (ValueError, AttributeError):
        complexity_enum = TaskComplexity.MEDIUM

    if not task:
        task = TaskModel(
            company_id=ctx.company.id,
            task_code=body.task_code,
            title=body.title,
            description=body.description,
            complexity=complexity_enum,
            priority=priority_enum,
            status=TaskStatus.IN_PROGRESS,
            estimated_hours=body.estimated_hours,
            remaining_estimated_hours=body.estimated_hours,
            planned_start=task_start,
            deadline=task_end,
            project_id=body.project_id,
            sprint_id=body.sprint_id,
        )
        session.add(task)
        session.flush()
    else:
        task.status = TaskStatus.IN_PROGRESS
        task.remaining_estimated_hours = body.estimated_hours

    assignment = (
        session.query(TaskAssignmentModel)
        .filter(
            TaskAssignmentModel.task_id == task.id,
            TaskAssignmentModel.employee_id == body.employee_id,
        )
        .first()
    )

    if not assignment:
        assignment = TaskAssignmentModel(
            task_id=task.id,
            employee_id=body.employee_id,
            assignment_status=AssignmentStatus.ACTIVE,
            planned_start=task_start,
            planned_end=task_end,
            allocated_hours=body.estimated_hours,
            remaining_estimated_hours=body.estimated_hours,
            assigned_at=now,
        )
        session.add(assignment)
    else:
        assignment.assignment_status = AssignmentStatus.ACTIVE
        assignment.allocated_hours = body.estimated_hours
        assignment.remaining_estimated_hours = body.estimated_hours
        assignment.assigned_at = now

    task_comm = (
        session.query(EmployeeCommitmentModel)
        .filter(
            EmployeeCommitmentModel.employee_id == body.employee_id,
            EmployeeCommitmentModel.title == f"Assigned: {task.task_code}",
        )
        .first()
    )

    if not task_comm:
        task_comm = EmployeeCommitmentModel(
            employee_id=body.employee_id,
            start_at=task_start,
            end_at=task_end,
            hours=body.estimated_hours,
            title=f"Assigned: {task.task_code}",
        )
        session.add(task_comm)
    else:
        task_comm.hours = body.estimated_hours
        task_comm.start_at = task_start
        task_comm.end_at = task_end

    svc.uow.audit.record(
        ctx.company.id,
        ctx.actor,
        "task.assigned",
        "task",
        str(task.id),
        {"employee_id": str(body.employee_id), "task_code": body.task_code},
    )
    svc.uow.commit()

    return {
        "status": "success",
        "message": f"Task {task.task_code} successfully assigned to {employee.full_name}",
        "assignment": {
            "task_id": str(task.id),
            "task_code": task.task_code,
            "title": task.title,
            "employee_id": str(employee.id),
            "employee_code": employee.employee_code,
            "employee_name": employee.full_name,
            "job_title": employee.job_title,
            "assigned_at": now.isoformat(),
            "allocated_hours": float(body.estimated_hours),
            "assignment_status": "ACTIVE",
        },
    }


@tasks_router.post("/unassign", status_code=status.HTTP_200_OK)
def unassign_task_from_employee(
    body: TaskUnassignRequest,
    ctx: Manager,
    svc: ServicesDep,
) -> dict[str, Any]:
    session = _db_session(svc)
    task = (
        session.query(TaskModel)
        .filter(
            TaskModel.company_id == ctx.company.id,
            TaskModel.task_code == body.task_code,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    assignments = session.query(TaskAssignmentModel).filter(TaskAssignmentModel.task_id == task.id)
    if body.employee_id:
        assignments = assignments.filter(TaskAssignmentModel.employee_id == body.employee_id)
    assignment_rows = assignments.all()
    emp_ids = [body.employee_id] if body.employee_id else [a.employee_id for a in assignment_rows]
    for a in assignment_rows:
        session.delete(a)

    if emp_ids:
        session.query(EmployeeCommitmentModel).filter(
            EmployeeCommitmentModel.employee_id.in_(emp_ids),
            EmployeeCommitmentModel.title == f"Assigned: {task.task_code}",
        ).delete(synchronize_session=False)

    task.status = TaskStatus.TODO
    svc.uow.audit.record(
        ctx.company.id,
        ctx.actor,
        "task.unassigned",
        "task",
        str(task.id),
        {"task_code": body.task_code},
    )
    svc.uow.commit()
    return {"status": "success", "message": f"Task {body.task_code} unassigned successfully"}


@tasks_router.post("", response_model=TaskRecord, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskSpecification,
    task_code: Annotated[str, Query(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")],
    ctx: Manager,
    svc: ServicesDep,
) -> TaskRecord:
    task_input = body.to_task_input()
    if not task_input.title.strip():
        derived = ((task_input.description or "").strip().splitlines()[0] if task_input.description else "Untitled Task")[:120]
        task_input = task_input.model_copy(update={"title": derived or "Untitled Task"})
    task = svc.tasks.create(ctx.company, task_code, task_input)
    svc.uow.audit.record(
        ctx.company.id, ctx.actor, "task.created", "task", str(task.id), {"task_code": task_code}
    )
    svc.uow.commit()
    return task


@tasks_router.get("", response_model=TaskPage)
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


@tasks_router.post("/analyze", response_model=TaskAnalysis, dependencies=[Depends(rate_limited)])
def analyze_task(body: TaskSpecification, ctx: Manager, svc: ServicesDep) -> TaskAnalysis:
    analysis = svc.tasks.analyze(ctx.company, body.to_task_input(), persist=True)
    svc.uow.commit()
    return analysis


@tasks_router.get("/{task_id}", response_model=TaskRecord)
def get_task(task_id: UUID, ctx: Viewer, svc: ServicesDep) -> TaskRecord:
    return svc.tasks.get(ctx.company, task_id=task_id)

router = APIRouter()
router.include_router(employees_router)
router.include_router(tasks_router)

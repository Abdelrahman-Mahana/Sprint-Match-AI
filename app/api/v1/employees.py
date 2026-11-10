from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from pydantic import BaseModel, Field

from app.api.deps import AsOfDep, Manager, ServicesDep, Viewer, WindowDep
from app.domain.entities.employee import Employee
from app.domain.enums import EmploymentStatus
from app.schemas.common import AvailabilityOut, Employee360Out, EmployeePage, PerformanceOut

router = APIRouter(prefix="/employees", tags=["employees"])


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    experience_years: float | None = Field(default=None, ge=0, le=50)
    employment_status: EmploymentStatus | None = None
    email: str | None = Field(default=None, max_length=254)


@router.get("", response_model=EmployeePage)
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


@router.get("/{employee_id}", response_model=Employee)
def get_employee(employee_id: UUID, ctx: Viewer, svc: ServicesDep) -> Employee:
    return svc.employees.get(ctx.company, employee_id)


@router.get("/{employee_id}/360", response_model=Employee360Out)
def get_employee_360(
    employee_id: UUID, ctx: Viewer, svc: ServicesDep, window: WindowDep, as_of: AsOfDep
) -> Employee360Out:
    resolved, _ = svc.workforce.resolve_window(ctx.company, as_of, window)
    data = svc.employees.get_360(ctx.company, employee_id, as_of=as_of, window=resolved)
    return Employee360Out(data=data, window=resolved)


@router.get("/{employee_id}/availability", response_model=AvailabilityOut)
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


@router.get("/{employee_id}/performance", response_model=PerformanceOut)
def get_employee_performance(employee_id: UUID, ctx: Viewer, svc: ServicesDep) -> PerformanceOut:
    perf = svc.employees.performance(ctx.company, employee_id)
    return PerformanceOut(summary=perf.summary, outcome_count=len(perf.outcomes))


@router.patch("/{employee_id}", response_model=Employee)
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

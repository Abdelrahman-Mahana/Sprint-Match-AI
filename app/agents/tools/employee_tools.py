"""Group A - employee information tools (read-only, PostgreSQL-backed)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import Field

from app.agents.tools.base import AgentRuntimeArg, run_query
from app.domain.enums import EmploymentStatus, TaskCategory

EmployeeId = Annotated[str, Field(description="Employee code (e.g. EMP-001) or employee UUID", max_length=64)]
CategoryName = Literal[
    "backend_development",
    "frontend_development",
    "mobile_development",
    "data_engineering",
    "machine_learning",
    "devops",
    "qa_testing",
    "database",
    "security",
    "other",
]


@tool("search_employees")
def search_employees(
    runtime: AgentRuntimeArg,
    department: Annotated[str | None, Field(description="Exact department name", max_length=120)] = None,
    team: Annotated[str | None, Field(description="Exact team name", max_length=120)] = None,
    job_title: Annotated[str | None, Field(description="Part of the job title", max_length=120)] = None,
    required_skills: Annotated[
        list[str], Field(description="Skill names every result must hold", max_length=10)
    ] = [],  # noqa: B006 - immutable default for the tool schema
    employment_status: Literal["ACTIVE", "SUSPENDED", "INACTIVE"] | None = "ACTIVE",
    limit: Annotated[int, Field(ge=1, le=50)] = 25,
) -> str:
    """Find employees with database filters (department, team, job title, skills held, employment status).

    Returns employee codes and work-related profile fields only. Use it to discover candidates; it does not
    evaluate suitability.
    """
    return run_query(
        runtime,
        lambda q: q.search_employees(
            department=department,
            team=team,
            job_title=job_title,
            required_skills=list(required_skills),
            employment_status=EmploymentStatus(employment_status) if employment_status else None,
            limit=limit,
        ),
    )


@tool("get_employee_profile")
def get_employee_profile(employee_id: EmployeeId, runtime: AgentRuntimeArg) -> str:
    """Retrieve the Employee 360 professional profile: skills (declared, verification, effective level),
    certifications, projects, reviewed-performance indicators with sample counts, and availability in the
    current execution window."""
    return run_query(runtime, lambda q: q.employee_profile(employee_id))


@tool("get_employee_skills")
def get_employee_skills(employee_id: EmployeeId, runtime: AgentRuntimeArg) -> str:
    """Retrieve an employee's skills with declared level, verification status, effective level (after
    verification and staleness factors) and the types of supporting evidence."""
    return run_query(runtime, lambda q: q.employee_skills(employee_id))


@tool("get_employee_experience")
def get_employee_experience(employee_id: EmployeeId, runtime: AgentRuntimeArg) -> str:
    """Retrieve previous projects and responsibilities, relevant experience entries with their skills,
    completed tasks per project, and certifications/training."""
    return run_query(runtime, lambda q: q.employee_experience(employee_id))


@tool("get_employee_performance")
def get_employee_performance(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    date_from: Annotated[date | None, Field(description="Only outcomes reviewed on/after this date")] = None,
    date_to: Annotated[date | None, Field(description="Only outcomes reviewed on/before this date")] = None,
    task_category: CategoryName | None = None,
) -> str:
    """Retrieve historical task performance (quality, acceptance, rework, on-time delivery, delays) with
    sample counts. Missing evidence is reported as null, never as zero."""
    return run_query(
        runtime,
        lambda q: q.employee_performance(
            employee_id,
            date_from=date_from,
            date_to=date_to,
            task_category=TaskCategory(task_category) if task_category else None,
            recent_limit=10,
        ),
    )


EMPLOYEE_TOOLS = [
    search_employees,
    get_employee_profile,
    get_employee_skills,
    get_employee_experience,
    get_employee_performance,
]

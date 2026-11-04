"""Group C - task intelligence tools."""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import Field

from app.agents.tools.base import AgentRuntimeArg, run_query
from app.agents.tools.employee_tools import CategoryName
from app.domain.enums import TaskCategory, TaskComplexity

TaskId = Annotated[
    str | None,
    Field(
        description="Task code (e.g. TASK-SCN-01) or UUID; default: the task being researched", max_length=64
    ),
]


@tool("get_task_details")
def get_task_details(runtime: AgentRuntimeArg, task_id: TaskId = None) -> str:
    """Retrieve a task with its project, sprint, priority, complexity, estimate, dates, required skills,
    required certifications and dependencies."""
    return run_query(runtime, lambda q: q.task_details(task_id))


@tool("get_task_required_skills")
def get_task_required_skills(runtime: AgentRuntimeArg, task_id: TaskId = None) -> str:
    """Retrieve the mandatory and optional skill requirements (with minimum levels and their source) and
    required certifications of a task."""
    return run_query(runtime, lambda q: q.task_required_skills(task_id))


@tool("search_similar_tasks")
def search_similar_tasks(
    runtime: AgentRuntimeArg,
    skills: Annotated[list[str], Field(description="Skill names of the new task", max_length=10)] = [],  # noqa: B006
    task_category: CategoryName | None = None,
    complexity: Literal["low", "medium", "high", "critical"] | None = None,
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
) -> str:
    """Find previously completed, reviewed tasks similar to the given skills/category/complexity, with who
    completed them and their average reviewed quality."""
    return run_query(
        runtime,
        lambda q: q.similar_tasks(
            skills=list(skills),
            task_category=TaskCategory(task_category) if task_category else None,
            complexity=TaskComplexity(complexity) if complexity else None,
            limit=limit,
        ),
    )


TASK_TOOLS = [get_task_details, get_task_required_skills, search_similar_tasks]

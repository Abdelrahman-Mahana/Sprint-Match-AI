"""Shared test builders and deterministic agent fixtures."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.agents.state import AgentContext, AgentLimits, SprintMatchAgentState
from app.agents.tools import build_tool_node
from app.application.services import AppContainer, RequestContext, task_input_from_record
from app.domain import (
    Assignment,
    AssignmentStatus,
    AttendanceRecord,
    AttendanceStatus,
    Commitment,
    Company,
    DelayReason,
    Employee,
    EmployeeProfile,
    EmployeeSkill,
    EmployeeWorkforceData,
    EmploymentStatus,
    ExecutionWindow,
    Experience,
    LeaveRecord,
    LeaveStatus,
    LeaveType,
    ProjectMembership,
    RequiredSkill,
    RequirementSource,
    ScheduleDay,
    SkillVerificationStatus,
    TaskCategory,
    TaskComplexity,
    TaskOutcomeRecord,
    TaskPriority,
    TaskRequirements,
)

# ===========================================================================
# Entity & Workforce Builders
# ===========================================================================

CAIRO = "Africa/Cairo"
TZ = ZoneInfo(CAIRO)
SUN_THU = (6, 0, 1, 2, 3)
# 2026-11-03 is a Tuesday. Nov 6/7 (Fri/Sat) are weekend days in the Sun-Thu calendar.
AS_OF = datetime(2026, 11, 3, 10, 0, tzinfo=TZ)
COMPANY = Company(id=uuid.UUID(int=1), code="TST", name="Test Co", timezone=CAIRO, working_weekdays=SUN_THU)

SKILL_IDS = {
    name: uuid.uuid5(uuid.NAMESPACE_DNS, f"skill-{name}")
    for name in ["Python", "FastAPI", "PostgreSQL", "React", "Docker", "Kubernetes"]
}
PROJECT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "project-1")


def local(d: date, h: int = 0, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), tzinfo=TZ)


def window(start: datetime, end: datetime) -> ExecutionWindow:
    return ExecutionWindow(start=start, end=end)


def standard_schedule(weekdays: tuple[int, ...] = SUN_THU) -> tuple[ScheduleDay, ...]:
    return tuple(
        ScheduleDay(weekday=w, start_time=time(9), end_time=time(17), break_minutes=60) for w in weekdays
    )


def assignment(
    *,
    remaining: float | None = None,
    planned_start: datetime | None = None,
    planned_end: datetime | None = None,
    allocated: float | None = None,
    logged: float = 0.0,
    task_remaining: float | None = None,
    assignees: int = 1,
    status: AssignmentStatus = AssignmentStatus.ACTIVE,
    code: str = "T-1",
) -> Assignment:
    return Assignment(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        task_code=code,
        task_title=code,
        assignment_status=status,
        planned_start=planned_start,
        planned_end=planned_end,
        allocated_hours=allocated,
        remaining_estimated_hours=remaining,
        task_remaining_estimated_hours=task_remaining,
        active_assignee_count=assignees,
        logged_hours=logged,
    )


def leave(start: datetime, end: datetime, status: LeaveStatus = LeaveStatus.APPROVED) -> LeaveRecord:
    return LeaveRecord(
        id=uuid.uuid4(), leave_type=LeaveType.ANNUAL, start_at=start, end_at=end, status=status
    )


def commitment(start: datetime, end: datetime, hours: float) -> Commitment:
    return Commitment(id=uuid.uuid4(), title="Support rotation", start_at=start, end_at=end, hours=hours)


def checked_in_today(day: date = AS_OF.date()) -> AttendanceRecord:
    return AttendanceRecord(work_date=day, check_in=local(day, 8, 55), status=AttendanceStatus.PRESENT)


def workforce(
    employee_id: uuid.UUID | None = None,
    *,
    schedule: tuple[ScheduleDay, ...] | None = None,
    leaves: tuple[LeaveRecord, ...] = (),
    assignments: tuple[Assignment, ...] = (),
    commitments: tuple[Commitment, ...] = (),
    attendance: tuple[AttendanceRecord, ...] = (),
    status: EmploymentStatus = EmploymentStatus.ACTIVE,
    timezone: str = CAIRO,
) -> EmployeeWorkforceData:
    return EmployeeWorkforceData(
        employee_id=employee_id or uuid.uuid4(),
        timezone=timezone,
        employment_status=status,
        schedule=standard_schedule() if schedule is None else schedule,
        attendance=attendance,
        leaves=leaves,
        assignments=assignments,
        commitments=commitments,
    )


def employee(
    code: str = "EMP-1",
    *,
    years: float | None = 5.0,
    status: EmploymentStatus = EmploymentStatus.ACTIVE,
    team_id: uuid.UUID | None = None,
) -> Employee:
    return Employee(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, f"emp-{code}"),
        company_id=COMPANY.id,
        employee_code=code,
        full_name=f"Employee {code}",
        job_title="Engineer",
        team_id=team_id,
        experience_years=years,
        employment_status=status,
        timezone=CAIRO,
    )


def skill(
    name: str,
    level: int,
    status: SkillVerificationStatus = SkillVerificationStatus.VERIFIED,
    assessed: datetime | None = None,
) -> EmployeeSkill:
    assessed = assessed or (AS_OF - timedelta(days=90))
    return EmployeeSkill(
        skill_id=SKILL_IDS[name],
        skill_name=name,
        proficiency_level=level,
        verification_status=status,
        verified_at=assessed if status == SkillVerificationStatus.VERIFIED else None,
        last_assessed_at=assessed,
    )


def profile(
    emp: Employee,
    skills: tuple[EmployeeSkill, ...],
    *,
    experiences: tuple[Experience, ...] = (),
    member_of: tuple[uuid.UUID, ...] = (),
) -> EmployeeProfile:
    return EmployeeProfile(
        employee=emp,
        skills=skills,
        experiences=experiences,
        memberships=tuple(
            ProjectMembership(project_id=p, project_name="P", role="developer") for p in member_of
        ),
    )


def experience(skill_names: tuple[str, ...], years: float) -> Experience:
    end = AS_OF.date()
    return Experience(
        id=uuid.uuid4(),
        organization_or_project="Prior Co",
        role="Engineer",
        start_date=end - timedelta(days=int(years * 365.25)),
        end_date=end,
        skill_ids=tuple(SKILL_IDS[s] for s in skill_names),
        skill_names=skill_names,
    )


def outcome(
    quality: float | None = 85,
    *,
    skills: tuple[str, ...] = ("Python", "FastAPI"),
    on_time: bool | None = True,
    delay: DelayReason | None = None,
    accepted: bool | None = True,
    rework: int = 0,
    complexity: TaskComplexity = TaskComplexity.MEDIUM,
    category: TaskCategory = TaskCategory.BACKEND_DEVELOPMENT,
    project_id: uuid.UUID | None = PROJECT_ID,
) -> TaskOutcomeRecord:
    return TaskOutcomeRecord(
        task_id=uuid.uuid4(),
        task_code="H-1",
        project_id=project_id,
        task_category=category,
        complexity=complexity,
        required_skill_ids=tuple(SKILL_IDS[s] for s in skills),
        quality_score=quality,
        accepted=accepted,
        rework_count=rework,
        on_time=on_time,
        delay_reason=delay,
        reviewed_at=AS_OF - timedelta(days=30),
    )


def requirements(
    skills: tuple[tuple[str, int, bool], ...] = (("Python", 75, True), ("FastAPI", 70, True)),
    *,
    hours: float = 16,
    complexity: TaskComplexity = TaskComplexity.HIGH,
    project_id: uuid.UUID | None = PROJECT_ID,
) -> TaskRequirements:
    return TaskRequirements(
        title="Build refund API",
        task_category=TaskCategory.BACKEND_DEVELOPMENT,
        required_skills=tuple(
            RequiredSkill(
                skill_id=SKILL_IDS[name],
                skill_name=name,
                minimum_level=lvl,
                mandatory=mand,
                weight=1.0 if mand else 0.5,
            )
            for name, lvl, mand in skills
        ),
        complexity=complexity,
        complexity_source=RequirementSource.MANAGER,
        estimated_hours=hours,
        estimated_hours_source=RequirementSource.MANAGER,
        priority=TaskPriority.HIGH,
        project_id=project_id,
    )


def utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)

# ===========================================================================
# Agent Test Container & Fake Query Builders
# ===========================================================================

def tool_state(
    container: AppContainer, ctx: RequestContext, task_code: str = "TASK-SCN-01"
) -> dict[str, Any]:
    """The slice of graph state tools read: as_of, current task requirements, window and record."""
    with container.uow_factory() as uow:
        s = container.services(uow)
        record = s.tasks.get(ctx.company, task_code=task_code)
        analysis = s.tasks.analyze(ctx.company, task_input_from_record(record), persist=False)
        window, _ = s.workforce.resolve_window(
            ctx.company, AS_OF, None, planned_start=record.planned_start, deadline=record.deadline
        )
    assert analysis.requirements is not None
    return {
        "as_of": AS_OF.isoformat(),
        "requirements": analysis.requirements.model_dump(mode="json"),
        "execution_window": window.model_dump(mode="json"),
        "task_record": record.model_dump(mode="json"),
        "research_rounds": 1,
        "agent_tool_results": [],
        "messages": [],
    }


def tool_graph():  # type: ignore[no-untyped-def]
    g: StateGraph[Any, AgentContext, Any, Any] = StateGraph(
        SprintMatchAgentState, context_schema=AgentContext
    )
    g.add_node("tool_execution", build_tool_node())
    g.add_edge(START, "tool_execution")
    g.add_edge("tool_execution", END)
    return g.compile()


def agent_context(container: AppContainer, ctx: RequestContext, **limits: Any) -> AgentContext:
    return AgentContext(container=container, request=ctx, limits=AgentLimits(**limits))


def call(name: str, call_id: str | None = None, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id or f"c_{name}", "type": "tool_call"}


def execute(
    agent_ctx: AgentContext,
    state: dict[str, Any],
    *calls: dict[str, Any],
    use_async: bool = False,
) -> tuple[list[ToolMessage], list[dict[str, Any]]]:
    """Run tool calls through the ToolNode; returns (tool messages in call order, tool execution records)."""
    graph = tool_graph()
    prior = list(state.get("messages", []))
    payload = {**state, "messages": [*prior, AIMessage(content="", tool_calls=list(calls))]}
    if use_async:
        out = asyncio.run(graph.ainvoke(payload, context=agent_ctx), loop_factory=asyncio.SelectorEventLoop)
    else:
        out = graph.invoke(payload, context=agent_ctx)
    messages = [m for m in out["messages"] if isinstance(m, ToolMessage)][-len(calls) :]
    order = {c["id"]: i for i, c in enumerate(calls)}
    messages.sort(key=lambda m: order.get(m.tool_call_id, 99))
    records = sorted(
        out["agent_tool_results"][len(state.get("agent_tool_results", [])) :], key=lambda r: r["sequence"]
    )
    return messages, records


def content(message: ToolMessage) -> dict[str, Any]:
    data = json.loads(message.content if isinstance(message.content, str) else json.dumps(message.content))
    assert isinstance(data, dict)
    return data

"""Group B - workforce tools (attendance, leave, schedules, assignments, workload, capacity, availability).

All numbers come from the deterministic workforce engine. Window arguments default to the execution window
of the recommendation being researched.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from langchain_core.tools import tool
from pydantic import Field

from app.agents.tools.base import AgentRuntimeArg, run_query
from app.agents.tools.employee_tools import EmployeeId

WindowStart = Annotated[
    datetime | None, Field(description="Window start (ISO-8601 with offset); default: the task's window")
]
WindowEnd = Annotated[
    datetime | None, Field(description="Window end (ISO-8601 with offset); default: the task's window")
]
StartDate = Annotated[date | None, Field(description="First day (inclusive)")]
EndDate = Annotated[date | None, Field(description="Last day (inclusive)")]


@tool("get_employee_attendance")
def get_employee_attendance(
    employee_id: EmployeeId, runtime: AgentRuntimeArg, start_date: StartDate = None, end_date: EndDate = None
) -> str:
    """Retrieve attendance evidence (check-in/check-out records) and presence at evaluation time.
    Default range: the last 14 days. Presence today does not imply capacity for the task."""
    return run_query(runtime, lambda q: q.attendance(employee_id, start_date, end_date))


@tool("get_employee_leave")
def get_employee_leave(
    employee_id: EmployeeId, runtime: AgentRuntimeArg, start_date: StartDate = None, end_date: EndDate = None
) -> str:
    """Retrieve approved and requested leave overlapping a date range (default: next 14 days), with the
    approved and pending leave hours inside the range."""
    return run_query(runtime, lambda q: q.leaves(employee_id, start_date, end_date))


@tool("get_employee_schedule")
def get_employee_schedule(
    employee_id: EmployeeId, runtime: AgentRuntimeArg, start_date: StartDate = None, end_date: EndDate = None
) -> str:
    """Retrieve the weekly working schedule, company holidays and scheduled hours for a date range
    (default: the next 7 days)."""
    return run_query(runtime, lambda q: q.schedule(employee_id, start_date, end_date))


@tool("get_employee_assignments")
def get_employee_assignments(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Retrieve current planned/active task assignments relevant to an execution window."""
    return run_query(runtime, lambda q: q.assignments(employee_id, q.window(window_start, window_end)))


@tool("calculate_employee_workload")
def calculate_employee_workload(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Calculate remaining committed work inside an execution window per assignment (with the effort basis
    and certainty; unknown remaining effort is flagged, never assumed)."""
    return run_query(runtime, lambda q: q.workload(employee_id, q.window(window_start, window_end)))


@tool("calculate_employee_capacity")
def calculate_employee_capacity(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Calculate remaining working capacity in an execution window: scheduled, holiday and leave hours,
    committed work, other commitments and available capacity (with the formula used)."""
    return run_query(runtime, lambda q: q.capacity(employee_id, q.window(window_start, window_end)))


@tool("get_employee_availability")
def get_employee_availability(
    employee_id: EmployeeId,
    runtime: AgentRuntimeArg,
    window_start: WindowStart = None,
    window_end: WindowEnd = None,
) -> str:
    """Return the availability status (AVAILABLE, PARTIALLY_AVAILABLE, BUSY, UNAVAILABLE, UNKNOWN) for an
    execution window with the supporting hours and warnings."""
    return run_query(runtime, lambda q: q.availability(employee_id, q.window(window_start, window_end)))


WORKFORCE_TOOLS = [
    get_employee_attendance,
    get_employee_leave,
    get_employee_schedule,
    get_employee_assignments,
    calculate_employee_workload,
    calculate_employee_capacity,
    get_employee_availability,
]

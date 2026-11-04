"""The research agent's approved, read-only tools and their LangGraph ToolNode.

Nothing here can write business data, run SQL, run shell commands or assign tasks: every tool opens a
read-only, statement-timed unit of work and returns a validated, size-bounded JSON result.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode

from app.agents.tools.base import format_tool_error
from app.agents.tools.employee_tools import EMPLOYEE_TOOLS
from app.agents.tools.policy import ToolCallPolicy
from app.agents.tools.recommendation_tools import RECOMMENDATION_TOOLS
from app.agents.tools.task_tools import TASK_TOOLS
from app.agents.tools.workforce_tools import WORKFORCE_TOOLS

ALL_TOOLS: list[BaseTool] = [*EMPLOYEE_TOOLS, *WORKFORCE_TOOLS, *TASK_TOOLS, *RECOMMENDATION_TOOLS]
TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in ALL_TOOLS)
TOOL_NODE_NAME = "tool_execution"

assert len(set(TOOL_NAMES)) == len(TOOL_NAMES), "tool names must be unique"


def build_tool_node() -> ToolNode:
    policy = ToolCallPolicy()
    return ToolNode(
        ALL_TOOLS,
        name=TOOL_NODE_NAME,
        handle_tool_errors=format_tool_error,
        wrap_tool_call=policy.wrap,
        awrap_tool_call=policy.awrap,
    )


__all__ = ["ALL_TOOLS", "TOOL_NAMES", "TOOL_NODE_NAME", "build_tool_node"]

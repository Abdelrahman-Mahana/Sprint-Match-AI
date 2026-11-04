"""Agent configuration: which research mode the configured model supports, and graph execution settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langgraph.types import RetryPolicy

from app.core.config import Settings
from app.core.exceptions import SprintMatchError
from app.domain.enums import AgentMode
from app.infrastructure.llm.base import LLMError, LLMProvider, ProviderCapabilities

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentModeDecision:
    mode: AgentMode
    reason: str | None
    capabilities: ProviderCapabilities | None = None


def resolve_agent_mode(settings: Settings, llm: LLMProvider | None) -> AgentModeDecision:
    """``auto``: native tool calling when the model supports it, else validated JSON tool plans when it has
    reliable structured output, else disabled (the deterministic pipeline always runs)."""
    if settings.agent_tool_mode == "disabled":
        return AgentModeDecision(AgentMode.DISABLED, "AGENT_TOOL_MODE=disabled")
    if llm is None:
        return AgentModeDecision(AgentMode.DISABLED, "no LLM provider configured")
    if settings.agent_tool_mode == "native":
        return AgentModeDecision(AgentMode.NATIVE, "configured: AGENT_TOOL_MODE=native")
    if settings.agent_tool_mode == "json_plan":
        return AgentModeDecision(AgentMode.JSON_PLAN, "configured: AGENT_TOOL_MODE=json_plan")
    try:
        caps = llm.capabilities(probe=True)
    except LLMError as exc:
        logger.warning("capability probe failed", extra={"error_code": exc.code})
        return AgentModeDecision(AgentMode.DISABLED, f"capability probe failed: {exc.code}")
    if caps.tool_calling:
        return AgentModeDecision(AgentMode.NATIVE, "model supports native tool calling", caps)
    if caps.structured_output:
        return AgentModeDecision(
            AgentMode.JSON_PLAN, "no native tool calling; using validated JSON tool plans", caps
        )
    return AgentModeDecision(AgentMode.DISABLED, "MODEL_NOT_SUITABLE_FOR_AGENT_MODE", caps)


def node_retry_policy(settings: Settings) -> RetryPolicy:
    """LangGraph-native bounded retry of a node on *retryable* errors only (e.g. database unavailable)."""
    return RetryPolicy(
        max_attempts=settings.agent_node_max_attempts,
        initial_interval=settings.agent_retry_initial_interval_seconds,
        backoff_factor=2.0,
        max_interval=10.0,
        jitter=False,
        retry_on=lambda exc: isinstance(exc, SprintMatchError) and exc.retryable,
    )


def recursion_limit(settings: Settings) -> int:
    """Upper bound on graph steps: the linear pipeline plus two steps per research round, with headroom."""
    return 30 + 2 * settings.agent_max_tool_rounds

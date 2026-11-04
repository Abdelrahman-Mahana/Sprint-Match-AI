"""Agent runtime: compiled graphs (per checkpointer), agent-mode resolution and per-run contexts."""

from __future__ import annotations

import threading
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agents.builder import AgentGraph, build_graph
from app.agents.checkpointer import CheckpointStore
from app.agents.configuration import AgentModeDecision, node_retry_policy, recursion_limit, resolve_agent_mode
from app.agents.context import AgentContext, AgentLimits
from app.application.container import AppContainer
from app.application.context import RequestContext
from app.domain.enums import AgentMode
from app.infrastructure.llm.factory import build_chat_model


class AgentRuntime:
    def __init__(self, container: AppContainer, checkpoints: CheckpointStore) -> None:
        self.container = container
        self.settings = container.settings
        self.checkpoints = checkpoints
        self._graphs: dict[int, AgentGraph] = {}
        self._decision: AgentModeDecision | None = None
        self._chat: BaseChatModel | None = None
        self._lock = threading.Lock()
        self.limits = AgentLimits.from_settings(self.settings)

    # --- model / mode ------------------------------------------------------------------------------------
    def mode(self) -> AgentModeDecision:
        """Resolved once and cached; a failed capability probe is not cached (retried on the next run)."""
        with self._lock:
            if self._decision is not None:
                return self._decision
        decision = resolve_agent_mode(self.settings, self.container.llm)
        probe_failed = decision.reason is not None and decision.reason.startswith("capability probe failed")
        with self._lock:
            if not probe_failed:
                self._decision = decision
                llm = self.container.llm
                if decision.mode != AgentMode.DISABLED and llm is not None:
                    mode: Literal["native", "json_plan"] = (
                        "native" if decision.mode == AgentMode.NATIVE else "json_plan"
                    )
                    self._chat = build_chat_model(llm, mode)
        return decision

    def context(self, request: RequestContext, *, persist: bool = True) -> AgentContext:
        decision = self.mode()
        return AgentContext(
            container=self.container,
            request=request,
            agent_mode=decision.mode
            if self._chat is not None or decision.mode == AgentMode.DISABLED
            else AgentMode.DISABLED,
            agent_mode_reason=decision.reason,
            chat_model=self._chat,
            limits=self.limits,
            explanation_mode=self.settings.explanation_mode,
            persist=persist,
        )

    # --- graphs --------------------------------------------------------------------------------------------
    def graph_for(self, saver: BaseCheckpointSaver[Any] | None) -> AgentGraph:
        key = id(saver)
        with self._lock:
            if key not in self._graphs:
                self._graphs[key] = build_graph(
                    checkpointer=saver, retry_policy=node_retry_policy(self.settings)
                )
            return self._graphs[key]

    def sync_graph(self) -> AgentGraph:
        return self.graph_for(self.checkpoints.sync_saver())

    async def async_graph(self) -> AgentGraph:
        return self.graph_for(await self.checkpoints.async_saver())

    def config(self, thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit(self.settings)}

"""Runtime context of one agent run (LangGraph ``context_schema``) and its non-checkpointed workspace."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from langchain_core.language_models import BaseChatModel

from app.application.container import AppContainer, Services
from app.application.context import RequestContext
from app.application.pipeline import PerformanceData
from app.core.config import Settings
from app.domain.entities.employee import EmployeeProfile
from app.domain.entities.workforce import EmployeeWorkforceData
from app.domain.enums import AgentMode, ToolExecutionStatus

EXECUTED = {ToolExecutionStatus.OK.value, ToolExecutionStatus.ERROR.value, ToolExecutionStatus.TIMEOUT.value}


@dataclass(frozen=True)
class AgentLimits:
    max_tool_rounds: int = 5
    max_tool_calls: int = 20
    tool_timeout_seconds: float = 10.0
    max_result_items: int = 25
    max_result_chars: int = 12_000
    max_profile_expansions: int = 10

    @classmethod
    def from_settings(cls, s: Settings) -> AgentLimits:
        return cls(
            max_tool_rounds=s.agent_max_tool_rounds,
            max_tool_calls=s.agent_max_tool_calls,
            tool_timeout_seconds=s.agent_tool_timeout_seconds,
            max_result_items=s.agent_max_tool_result_items,
            max_result_chars=s.agent_max_tool_result_chars,
            max_profile_expansions=s.agent_max_profile_expansions,
        )


def call_signature(name: str, args: dict[str, Any]) -> str:
    normalized = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{name}:{normalized}".encode()).hexdigest()[:24]


class RunWorkspace:
    """Per-invocation scratch space, never checkpointed.

    Heavy domain objects (full profiles, raw workforce records, outcomes) live here; the checkpointed graph
    state only holds identifiers and computed summaries. After a resume in another process the workspace is
    empty and nodes reload the authoritative data from PostgreSQL. Tool-policy bookkeeping (budget, duplicate
    detection, profile expansion) is rebuilt from the checkpointed tool records on first use.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.profiles: dict[UUID, EmployeeProfile] | None = None
        self.workforce: dict[UUID, EmployeeWorkforceData] | None = None
        self.holidays: set[date] | None = None
        self.performance: PerformanceData | None = None
        self._results: dict[str, Future[str]] = {}
        self._calls_used: int | None = None
        self._expanded: set[str] = set()
        self._attempts: dict[str, int] = {}

    def count_attempt(self, node: str) -> int:
        """Attempt number of ``node`` in this invocation (LangGraph's RetryPolicy re-runs the node)."""
        with self.lock:
            self._attempts[node] = self._attempts.get(node, 0) + 1
            return self._attempts[node]

    # --- tool policy ---------------------------------------------------------------------------------
    def sync_tool_state(self, records: list[dict[str, Any]], messages: list[Any]) -> None:
        with self.lock:
            if self._calls_used is not None:
                return
            self._calls_used = sum(1 for r in records if r.get("status") in EXECUTED)
            contents = {getattr(m, "tool_call_id", None): m.content for m in messages}
            for r in records:
                args = r.get("arguments_summary") or {}
                ref = args.get("employee_id")
                if isinstance(ref, str) and r.get("status") in EXECUTED:
                    self._expanded.add(ref.strip().upper())
                sig = args.get("_signature")
                content = contents.get(r.get("tool_call_id"))
                if r.get("status") == ToolExecutionStatus.OK.value and sig and isinstance(content, str):
                    fut: Future[str] = Future()
                    fut.set_result(content)
                    self._results.setdefault(sig, fut)

    def claim(self, signature: str) -> tuple[bool, Future[str]]:
        """(True, future) for the first call with this signature; (False, future of the first) otherwise."""
        with self.lock:
            if signature in self._results:
                return False, self._results[signature]
            fut: Future[str] = Future()
            self._results[signature] = fut
            return True, fut

    def acquire_call(self, limit: int) -> bool:
        with self.lock:
            used = self._calls_used or 0
            if used >= limit:
                return False
            self._calls_used = used + 1
            return True

    def expand_employee(self, ref: str, limit: int) -> bool:
        key = ref.strip().upper()
        with self.lock:
            if key in self._expanded:
                return True
            if len(self._expanded) >= limit:
                return False
            self._expanded.add(key)
            return True


@dataclass
class AgentContext:
    container: AppContainer
    request: RequestContext
    agent_mode: AgentMode = AgentMode.DISABLED
    agent_mode_reason: str | None = None
    chat_model: BaseChatModel | None = None
    limits: AgentLimits = field(default_factory=AgentLimits)
    workspace: RunWorkspace = field(default_factory=RunWorkspace)
    explanation_mode: str = "deterministic"
    persist: bool = True

    @property
    def llm_provider(self) -> str | None:
        return self.container.llm.name if self.container.llm else None

    @property
    def llm_model(self) -> str | None:
        return self.container.llm.model if self.container.llm else None

    @contextmanager
    def services(
        self, *, read_only: bool = False, statement_timeout_ms: int | None = None
    ) -> Iterator[Services]:
        """A short-lived unit of work per node/tool (thread-safe: nothing is shared between calls)."""
        with self.container.uow_factory(
            read_only=read_only, statement_timeout_ms=statement_timeout_ms
        ) as uow:
            yield self.container.services(uow)

"""LangGraph state schema, agent mode/retry configuration, run workspace context, and PostgreSQL/Memory checkpointer store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import operator
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.message import add_messages
from langgraph.types import RetryPolicy
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from sqlalchemy.engine import make_url

from app.application.services import AppContainer, PerformanceData, RequestContext, Services
from app.core import Settings, SprintMatchError
from app.domain import (
    AgentMode,
    EmployeeProfile,
    EmployeeWorkforceData,
    ToolExecutionStatus,
    UnitOfWorkFactory,
)
from app.infrastructure.llm import LLMError, LLMProvider, ProviderCapabilities

logger = logging.getLogger(__name__)


# ===========================================================================
# Agent State Schema
# ===========================================================================

class SprintMatchAgentState(TypedDict, total=False):
    # identity / request
    request_id: str
    recommendation_id: str
    thread_id: str
    company_id: str
    requested_by: str
    as_of: str  # ISO-8601 with offset
    request: dict[str, Any]  # RecommendationRequest (json)
    idempotency_key: str | None
    previous_snapshot: dict[str, Any] | None  # revalidation baseline
    revalidated_from_id: str | None
    retry_of_id: str | None

    # task
    task_record: dict[str, Any] | None
    task_input: dict[str, Any]
    task_analysis: dict[str, Any]
    requirements: dict[str, Any]  # TaskRequirements (json), only when analysis is READY
    execution_window: dict[str, Any]

    # workforce research agent
    messages: Annotated[list[AnyMessage], add_messages]
    research_rounds: int
    research: dict[str, Any]
    agent_tool_results: Annotated[list[dict[str, Any]], operator.add]

    # authoritative deterministic pipeline
    employee_ids: list[str]
    employee_profiles: dict[str, dict[str, Any]]  # minimal summaries keyed by employee id
    availability_results: dict[str, dict[str, Any]]  # AvailabilityResult (json)
    workload_data: dict[str, dict[str, Any]]
    eligibility_results: dict[str, dict[str, Any]]  # EligibilityResult (json)
    performance_results: dict[str, dict[str, Any]]
    engine_result: dict[str, Any]  # EngineResult (json)
    ranked_candidates: list[dict[str, Any]]
    data_quality: dict[str, Any]

    # outcome
    final_explanation: dict[str, Any]
    validation: dict[str, Any]
    final_recommendation: dict[str, Any]  # RecommendationResponse (json)
    agent_warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    execution_status: str | None
    persisted: bool

# ===========================================================================
# Agent Mode & Retry Policy Configuration
# ===========================================================================

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

# ===========================================================================
# Agent Runtime Context & Workspace
# ===========================================================================

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

    # tool policy
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

# ===========================================================================
# Checkpoint Persistence Store
# ===========================================================================

def libpq_url(database_url: str) -> str:
    """SQLAlchemy URL (``postgresql+psycopg://``) -> libpq conninfo URL for psycopg."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _connection_kwargs(schema: str) -> dict[str, Any]:
    # Official requirements of the Postgres savers: autocommit, dict rows, no server-side prepared statements.
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": f"-c search_path={schema}",
    }


class CheckpointStore:
    def __init__(self, settings: Settings, database_url: str | None = None) -> None:
        self.backend = settings.checkpointer
        self.schema = settings.checkpoint_schema
        self.retention_days = settings.checkpoint_retention_days
        self._url = libpq_url(database_url or settings.database_url.get_secret_value())
        self._memory = InMemorySaver() if self.backend == "memory" else None
        self._pool: ConnectionPool | None = None
        self._sync: PostgresSaver | None = None
        self._apool: AsyncConnectionPool | None = None
        self._async: AsyncPostgresSaver | None = None

    @property
    def enabled(self) -> bool:
        return self.backend != "none"

    def _ensure_schema(self, conn: Any) -> None:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))

    # sync
    def sync_saver(self) -> BaseCheckpointSaver[Any] | None:
        if self.backend == "none":
            return None
        if self._memory is not None:
            return self._memory
        if self._sync is None:
            pool = ConnectionPool(
                self._url, min_size=1, max_size=4, kwargs=_connection_kwargs(self.schema), open=True
            )
            with pool.connection() as conn:
                self._ensure_schema(conn)
            saver = PostgresSaver(pool)  # type: ignore[arg-type]
            saver.setup()
            self._pool, self._sync = pool, saver
            logger.info("checkpointer ready", extra={"backend": "postgres", "schema": self.schema})
        return self._sync

    # async (FastAPI)
    async def async_saver(self) -> BaseCheckpointSaver[Any] | None:
        if self.backend == "none":
            return None
        if self._memory is not None:
            return self._memory
        if self._async is None:
            if sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
                raise RuntimeError(
                    "psycopg async needs a selector event loop on Windows: run uvicorn with "
                    "--loop app.core.event_loop:selector_loop_factory (see README)"
                )
            pool = AsyncConnectionPool(
                self._url, min_size=1, max_size=8, kwargs=_connection_kwargs(self.schema), open=False
            )
            await pool.open()
            async with pool.connection() as conn:
                await conn.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema))
                )
            saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
            await saver.setup()
            self._apool, self._async = pool, saver
            logger.info("async checkpointer ready", extra={"backend": "postgres", "schema": self.schema})
        return self._async

    def ping(self) -> bool:
        saver = self.sync_saver()
        if saver is None or self._memory is not None:
            return saver is not None
        assert self._pool is not None
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")
        return True

    # retention
    def prune(self, uow_factory: UnitOfWorkFactory, *, now: datetime | None = None) -> int:
        """Delete checkpoint threads of runs last updated before the retention cutoff."""
        saver = self.sync_saver()
        if saver is None:
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        with uow_factory(read_only=True) as uow:
            run_ids = uow.recommendations.list_run_ids_before(cutoff)
        for run_id in run_ids:
            saver.delete_thread(str(run_id))
        logger.info("checkpoints pruned", extra={"threads": len(run_ids), "cutoff": cutoff.isoformat()})
        return len(run_ids)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool, self._sync = None, None

    async def aclose(self) -> None:
        if self._apool is not None:
            await self._apool.close()
            self._apool, self._async = None, None
        self.close()

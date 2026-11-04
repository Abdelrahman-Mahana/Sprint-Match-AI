"""Recommendation / agent-run service: the only entry point to the LangGraph agent.

* ``create`` / ``acreate``      - run the compiled graph (``invoke`` / ``ainvoke``) for a new recommendation;
* ``stream`` / ``astream``      - the same run with progress events (``stream`` / ``astream``);
* ``resume`` / ``aresume``      - continue a run that stopped on a retryable error from its checkpoint, or
                                  re-run a completed FAILED run as a new linked run;
* idempotency keys return the stored result instead of running twice.

A run that stops with an exception (after LangGraph's bounded node retries) is reported as FAILED with a
categorized error - never as a success - and is marked ``resumable`` when its checkpoint can continue it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, cast

import psycopg
from langgraph.errors import GraphRecursionError

from app.agents.checkpointer import CheckpointStore
from app.agents.context import AgentContext
from app.agents.nodes import build_response, persist_run
from app.agents.nodes.common import FAILED, error_entry
from app.agents.runtime import AgentRuntime
from app.agents.state import SprintMatchAgentState
from app.application.container import AppContainer
from app.application.context import RequestContext
from app.core.exceptions import NotFoundError, RunNotResumableError, SprintMatchError
from app.domain.enums import RecommendationStatus
from app.domain.interfaces import (
    ExecutionTraceEntry,
    RunErrorRecord,
    StoredRecommendation,
    ToolExecutionRecord,
)
from app.infrastructure.llm.usage import metered
from app.schemas.recommendations import (
    CandidateOut,
    ExecutionWindowRequest,
    RecommendationRequest,
    RecommendationResponse,
)


def runtime_for(container: AppContainer) -> AgentRuntime:
    if container.agent_runtime is None:
        container.agent_runtime = AgentRuntime(container, CheckpointStore(container.settings))
    runtime: AgentRuntime = container.agent_runtime
    return runtime


def _classify(exc: BaseException) -> tuple[str, str, bool]:
    if isinstance(exc, SprintMatchError):
        return exc.code, exc.message, exc.retryable
    if isinstance(exc, GraphRecursionError):
        return "GRAPH_RECURSION_LIMIT", "The agent exceeded its step limit", False
    if isinstance(exc, psycopg.OperationalError):
        return "CHECKPOINT_UNAVAILABLE", "Checkpoint storage is unavailable", True
    return "GRAPH_EXECUTION_ERROR", type(exc).__name__, False


def _sse_events(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for node, update in chunk.items():
        updates = update if isinstance(update, list) else [update]
        for u in updates:
            if not isinstance(u, dict):
                continue
            for t in u.get("trace", []):
                events.append(
                    {
                        "event": "node",
                        "node": t["node_name"],
                        "status": t["execution_status"],
                        "duration_ms": t["duration_ms"],
                    }
                )
            for r in u.get("agent_tool_results", []):
                events.append(
                    {
                        "event": "tool",
                        "node": node,
                        "tool": r["tool_name"],
                        "status": r["status"],
                        "duration_ms": r["duration_ms"],
                        "error_code": r.get("error_code"),
                    }
                )
    return events


class RecommendationService:
    def __init__(
        self, container: AppContainer, *, persist: bool = True, runtime: AgentRuntime | None = None
    ) -> None:
        self.container = container
        self.persist = persist
        self.runtime = runtime or runtime_for(container)

    # --- helpers --------------------------------------------------------------------------------------
    def _initial_state(
        self,
        ctx: RequestContext,
        request: RecommendationRequest,
        run_id: uuid.UUID,
        *,
        idempotency_key: str | None,
        previous_snapshot: dict[str, Any] | None,
        revalidated_from_id: uuid.UUID | None,
        retry_of_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        as_of = request.as_of or datetime.now(UTC)
        return {
            "request_id": ctx.request_id,
            "recommendation_id": str(run_id),
            "thread_id": str(run_id),
            "company_id": str(ctx.company.id),
            "requested_by": request.requested_by or ctx.actor,
            "as_of": as_of.isoformat(),
            "request": request.model_dump(mode="json"),
            "idempotency_key": idempotency_key,
            "previous_snapshot": previous_snapshot,
            "revalidated_from_id": str(revalidated_from_id) if revalidated_from_id else None,
            "retry_of_id": str(retry_of_id) if retry_of_id else None,
            "research_rounds": 0,
            "messages": [],
            "agent_tool_results": [],
            "agent_warnings": [],
            "errors": [],
            "trace": [],
        }

    def _replay(self, ctx: RequestContext, key: str | None) -> RecommendationResponse | None:
        if not key:
            return None
        with self.container.uow_factory(read_only=True) as uow:
            stored = uow.recommendations.find_by_idempotency_key(ctx.company.id, key)
        if stored is None:
            return None
        return RecommendationResponse.model_validate(stored.result_snapshot).model_copy(
            update={"idempotent_replay": True}
        )

    def _interrupted(
        self,
        state: Mapping[str, Any],
        agent_ctx: AgentContext,
        exc: BaseException,
        next_nodes: tuple[str, ...],
    ) -> RecommendationResponse:
        code, message, retryable = _classify(exc)
        resumable = retryable and self.runtime.checkpoints.enabled and bool(next_nodes)
        error = error_entry(next_nodes[0] if next_nodes else "graph", code, message, retryable)
        failed_state = {**state, "execution_status": FAILED}
        response = build_response(failed_state, agent_ctx, extra_errors=[error], resumable=resumable)
        if agent_ctx.persist:
            try:
                return persist_run(
                    {**failed_state, "errors": [*state.get("errors", []), error]}, agent_ctx, response
                )
            except SprintMatchError:
                pass  # e.g. the database itself is down: report the failure, unpersisted
        return response

    @staticmethod
    def _final(values: dict[str, Any]) -> RecommendationResponse:
        return RecommendationResponse.model_validate(values["final_recommendation"])

    # --- run (sync) -------------------------------------------------------------------------------------
    def create(
        self,
        ctx: RequestContext,
        request: RecommendationRequest,
        *,
        idempotency_key: str | None = None,
        previous_snapshot: dict[str, Any] | None = None,
        revalidated_from_id: uuid.UUID | None = None,
        retry_of_id: uuid.UUID | None = None,
    ) -> RecommendationResponse:
        replay = self._replay(ctx, idempotency_key)
        if replay is not None:
            return replay
        run_id = uuid.uuid4()
        state = self._initial_state(
            ctx,
            request,
            run_id,
            idempotency_key=idempotency_key,
            previous_snapshot=previous_snapshot,
            revalidated_from_id=revalidated_from_id,
            retry_of_id=retry_of_id,
        )
        graph = self.runtime.sync_graph()
        config = self.runtime.config(str(run_id))
        agent_ctx = self.runtime.context(ctx, persist=self.persist)
        with metered():
            try:
                final = graph.invoke(
                    cast(SprintMatchAgentState, state), config, context=agent_ctx, durability="sync"
                )
            except Exception as exc:
                values, next_nodes = self._snapshot(graph, config)
                return self._interrupted({**state, **values}, agent_ctx, exc, next_nodes)
        return self._final(final)

    def _snapshot(self, graph: Any, config: Any) -> tuple[dict[str, Any], tuple[str, ...]]:
        if not self.runtime.checkpoints.enabled:
            return {}, ()
        try:
            snap = graph.get_state(config)
        except Exception:
            return {}, ()
        return dict(snap.values or {}), tuple(snap.next)

    def stream(self, ctx: RequestContext, request: RecommendationRequest) -> Iterator[dict[str, Any]]:
        run_id = uuid.uuid4()
        state = self._initial_state(
            ctx,
            request,
            run_id,
            idempotency_key=None,
            previous_snapshot=None,
            revalidated_from_id=None,
            retry_of_id=None,
        )
        graph = self.runtime.sync_graph()
        config = self.runtime.config(str(run_id))
        agent_ctx = self.runtime.context(ctx, persist=self.persist)
        yield {"event": "started", "recommendation_id": str(run_id), "agent_mode": agent_ctx.agent_mode.value}
        final: dict[str, Any] | None = None
        with metered():
            try:
                for chunk in graph.stream(
                    cast(SprintMatchAgentState, state),
                    config,
                    context=agent_ctx,
                    stream_mode="updates",
                    durability="sync",
                ):
                    yield from _sse_events(chunk)
                    for update in chunk.values():
                        if isinstance(update, dict) and "final_recommendation" in update:
                            final = update["final_recommendation"]
                response = RecommendationResponse.model_validate(final)
            except Exception as exc:
                values, next_nodes = self._snapshot(graph, config)
                response = self._interrupted({**state, **values}, agent_ctx, exc, next_nodes)
        yield {"event": "result", "recommendation": response.model_dump(mode="json")}

    def resume(self, ctx: RequestContext, run_id: uuid.UUID) -> RecommendationResponse:
        graph = self.runtime.sync_graph()
        config = self.runtime.config(str(run_id))
        values, next_nodes = self._snapshot(graph, config)
        action = self._resume_action(ctx, run_id, values, next_nodes)
        if isinstance(action, RecommendationRequest):
            return self.create(ctx, action, retry_of_id=run_id)
        agent_ctx = self.runtime.context(ctx, persist=self.persist)
        with metered():
            try:
                final = graph.invoke(None, config, context=agent_ctx, durability="sync")
            except Exception as exc:
                after, after_next = self._snapshot(graph, config)
                return self._interrupted({**values, **after}, agent_ctx, exc, after_next)
        return self._final(final)

    def _resume_action(
        self, ctx: RequestContext, run_id: uuid.UUID, values: dict[str, Any], next_nodes: tuple[str, ...]
    ) -> RecommendationRequest | None:
        """None -> continue from the checkpoint; a request -> re-run as a new run linked to ``run_id``."""
        stored = self._stored(ctx, run_id, required=False)
        if values and values.get("company_id") != str(ctx.company.id):
            raise NotFoundError("Agent run not found")
        if values and next_nodes:
            return None
        if stored is None:
            raise NotFoundError("Agent run not found")
        if stored.status == RecommendationStatus.FAILED:
            return RecommendationRequest.model_validate(stored.request_payload)
        raise RunNotResumableError(
            "Run already completed successfully; request a new recommendation or revalidate it instead"
        )

    # --- run (async: FastAPI) ---------------------------------------------------------------------------
    async def acreate(
        self,
        ctx: RequestContext,
        request: RecommendationRequest,
        *,
        idempotency_key: str | None = None,
        previous_snapshot: dict[str, Any] | None = None,
        revalidated_from_id: uuid.UUID | None = None,
        retry_of_id: uuid.UUID | None = None,
    ) -> RecommendationResponse:
        replay = await asyncio.to_thread(self._replay, ctx, idempotency_key)
        if replay is not None:
            return replay
        run_id = uuid.uuid4()
        state = self._initial_state(
            ctx,
            request,
            run_id,
            idempotency_key=idempotency_key,
            previous_snapshot=previous_snapshot,
            revalidated_from_id=revalidated_from_id,
            retry_of_id=retry_of_id,
        )
        graph = await self.runtime.async_graph()
        config = self.runtime.config(str(run_id))
        agent_ctx = await asyncio.to_thread(self.runtime.context, ctx, persist=self.persist)
        with metered():
            try:
                final = await graph.ainvoke(
                    cast(SprintMatchAgentState, state), config, context=agent_ctx, durability="sync"
                )
            except Exception as exc:
                values, next_nodes = await self._asnapshot(graph, config)
                return await asyncio.to_thread(
                    self._interrupted, {**state, **values}, agent_ctx, exc, next_nodes
                )
        return self._final(final)

    async def _asnapshot(self, graph: Any, config: Any) -> tuple[dict[str, Any], tuple[str, ...]]:
        if not self.runtime.checkpoints.enabled:
            return {}, ()
        try:
            snap = await graph.aget_state(config)
        except Exception:
            return {}, ()
        return dict(snap.values or {}), tuple(snap.next)

    async def astream(
        self, ctx: RequestContext, request: RecommendationRequest
    ) -> AsyncIterator[dict[str, Any]]:
        run_id = uuid.uuid4()
        state = self._initial_state(
            ctx,
            request,
            run_id,
            idempotency_key=None,
            previous_snapshot=None,
            revalidated_from_id=None,
            retry_of_id=None,
        )
        graph = await self.runtime.async_graph()
        config = self.runtime.config(str(run_id))
        agent_ctx = await asyncio.to_thread(self.runtime.context, ctx, persist=self.persist)
        yield {"event": "started", "recommendation_id": str(run_id), "agent_mode": agent_ctx.agent_mode.value}
        final: dict[str, Any] | None = None
        with metered():
            try:
                async for chunk in graph.astream(
                    cast(SprintMatchAgentState, state),
                    config,
                    context=agent_ctx,
                    stream_mode="updates",
                    durability="sync",
                ):
                    for event in _sse_events(chunk):
                        yield event
                    for update in chunk.values():
                        if isinstance(update, dict) and "final_recommendation" in update:
                            final = update["final_recommendation"]
                response = RecommendationResponse.model_validate(final)
            except Exception as exc:
                values, next_nodes = await self._asnapshot(graph, config)
                response = await asyncio.to_thread(
                    self._interrupted, {**state, **values}, agent_ctx, exc, next_nodes
                )
        yield {"event": "result", "recommendation": response.model_dump(mode="json")}

    async def aresume(self, ctx: RequestContext, run_id: uuid.UUID) -> RecommendationResponse:
        graph = await self.runtime.async_graph()
        config = self.runtime.config(str(run_id))
        values, next_nodes = await self._asnapshot(graph, config)
        action = await asyncio.to_thread(self._resume_action, ctx, run_id, values, next_nodes)
        if isinstance(action, RecommendationRequest):
            return await self.acreate(ctx, action, retry_of_id=run_id)
        agent_ctx = await asyncio.to_thread(self.runtime.context, ctx, persist=self.persist)
        with metered():
            try:
                final = await graph.ainvoke(None, config, context=agent_ctx, durability="sync")
            except Exception as exc:
                after, after_next = await self._asnapshot(graph, config)
                return await asyncio.to_thread(
                    self._interrupted, {**values, **after}, agent_ctx, exc, after_next
                )
        return self._final(final)

    # --- stored runs ------------------------------------------------------------------------------------
    def _stored(
        self, ctx: RequestContext, run_id: uuid.UUID, *, required: bool = True
    ) -> StoredRecommendation | None:
        with self.container.uow_factory(read_only=True) as uow:
            stored = uow.recommendations.get_run(ctx.company.id, run_id)
        if stored is None and required:
            raise NotFoundError("Recommendation not found")
        return stored

    def get(self, ctx: RequestContext, recommendation_id: uuid.UUID) -> RecommendationResponse:
        stored = self._stored(ctx, recommendation_id)
        assert stored is not None
        return RecommendationResponse.model_validate(stored.result_snapshot)

    def get_run(self, ctx: RequestContext, run_id: uuid.UUID) -> StoredRecommendation:
        stored = self._stored(ctx, run_id)
        assert stored is not None
        return stored

    def candidates(self, ctx: RequestContext, recommendation_id: uuid.UUID) -> list[CandidateOut]:
        r = self.get(ctx, recommendation_id)
        return [
            *([r.primary_candidate] if r.primary_candidate else []),
            *r.alternatives,
            *r.excluded_candidates,
        ]

    def node_logs(self, ctx: RequestContext, run_id: uuid.UUID) -> list[ExecutionTraceEntry]:
        self._stored(ctx, run_id)
        with self.container.uow_factory(read_only=True) as uow:
            return uow.recommendations.list_node_logs(ctx.company.id, run_id)

    def tool_executions(self, ctx: RequestContext, run_id: uuid.UUID) -> list[ToolExecutionRecord]:
        self._stored(ctx, run_id)
        with self.container.uow_factory(read_only=True) as uow:
            return uow.recommendations.list_tool_executions(ctx.company.id, run_id)

    def run_errors(self, ctx: RequestContext, run_id: uuid.UUID) -> list[RunErrorRecord]:
        self._stored(ctx, run_id)
        with self.container.uow_factory(read_only=True) as uow:
            return uow.recommendations.list_run_errors(ctx.company.id, run_id)

    def checkpoints(self, ctx: RequestContext, run_id: uuid.UUID) -> list[dict[str, Any]]:
        """Sanitized checkpoint history (no state values): step, writing node, next nodes, time."""
        graph = self.runtime.sync_graph()
        if not self.runtime.checkpoints.enabled:
            return []
        config = self.runtime.config(str(run_id))
        history = list(graph.get_state_history(config))
        if not history or history[0].values.get("company_id") != str(ctx.company.id):
            raise NotFoundError("No checkpoints for this run")
        return [
            {
                "checkpoint_id": s.config["configurable"].get("checkpoint_id"),
                "step": s.metadata.get("step") if s.metadata else None,
                "source": s.metadata.get("source") if s.metadata else None,
                "next": list(s.next),
                "created_at": s.created_at,
                "execution_status": s.values.get("execution_status"),
                "research_rounds": s.values.get("research_rounds"),
                "tool_calls": len(s.values.get("agent_tool_results", [])),
            }
            for s in history
        ]

    def revalidate(
        self, ctx: RequestContext, recommendation_id: uuid.UUID, *, as_of: datetime | None = None
    ) -> RecommendationResponse:
        stored = self._stored(ctx, recommendation_id)
        assert stored is not None
        return self.create(
            ctx,
            self._revalidation_request(stored, as_of),
            previous_snapshot=stored.result_snapshot,
            revalidated_from_id=recommendation_id,
        )

    async def arevalidate(
        self, ctx: RequestContext, recommendation_id: uuid.UUID, *, as_of: datetime | None = None
    ) -> RecommendationResponse:
        stored = await asyncio.to_thread(self._stored, ctx, recommendation_id)
        assert stored is not None
        return await self.acreate(
            ctx,
            self._revalidation_request(stored, as_of),
            previous_snapshot=stored.result_snapshot,
            revalidated_from_id=recommendation_id,
        )

    @staticmethod
    def _revalidation_request(stored: StoredRecommendation, as_of: datetime | None) -> RecommendationRequest:
        """Same task and execution window, evaluated against the *current* data."""
        original = RecommendationRequest.model_validate(stored.request_payload)
        window = stored.result_snapshot.get("execution_window")
        return original.model_copy(
            update={
                "as_of": as_of or datetime.now(UTC),
                "execution_window": ExecutionWindowRequest(
                    start=datetime.fromisoformat(window["start"]),
                    deadline=datetime.fromisoformat(window["end"]),
                )
                if window
                else original.execution_window,
            }
        )


def sse(event: dict[str, Any]) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event, default=str)}\n\n"

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.agents.tools import TOOL_NAMES
from app.api.deps import ContainerDep, Viewer
from app.application.recommendations import runtime_for
from app.core.exceptions import DataSourceUnavailableError
from app.infrastructure.llm.base import ProviderCapabilities
from app.schemas.agent import AgentHealthOut, LLMHealthOut

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/ready")
def ready(container: ContainerDep, check_llm: bool = False) -> JSONResponse:
    """Readiness: the database is reachable. The LLM is optional and only reported (never required)."""
    body: dict[str, Any] = {"database": "ok"}
    code = 200
    try:
        with container.uow_factory() as uow:
            uow.ping()
    except DataSourceUnavailableError:
        body["database"] = "unavailable"
        code = 503
    if container.llm is None:
        body["llm"] = {"configured": False}
    elif check_llm:
        h = container.llm.health_check()
        body["llm"] = {
            "configured": True,
            "provider": h.provider,
            "model": h.model,
            "ok": h.ok,
            "latency_ms": h.latency_ms,
            "detail": h.detail,
        }
    else:
        body["llm"] = {"configured": True, "provider": container.llm.name, "model": container.llm.model}
    body["status"] = "ready" if code == 200 else "not_ready"
    return JSONResponse(status_code=code, content=body)


# --- /api/v1/health/* (authenticated: these may call the LLM provider) ---------------------------------
v1_router = APIRouter(prefix="/health", tags=["health"])


def _caps(c: ProviderCapabilities | None) -> dict[str, Any] | None:
    return asdict(c) if c is not None else None


@v1_router.get("/llm", response_model=LLMHealthOut)
async def llm_health(_: Viewer, container: ContainerDep, probe: bool = True) -> LLMHealthOut:
    """Provider reachability, configured model and capabilities (tool calling probed once and cached)."""
    llm = container.llm
    if llm is None:
        return LLMHealthOut(configured=False)
    h = await asyncio.to_thread(llm.health_check)
    caps = await asyncio.to_thread(llm.capabilities, probe=probe) if h.ok else llm.capabilities()
    return LLMHealthOut(
        configured=True,
        provider=h.provider,
        model=h.model,
        ok=h.ok,
        latency_ms=h.latency_ms,
        detail=h.detail,
        capabilities=_caps(caps),
    )


@v1_router.get("/agent", response_model=AgentHealthOut)
async def agent_health(_: Viewer, container: ContainerDep) -> AgentHealthOut:
    """Compiled graph, registered tools, resolved agent mode and checkpointer readiness."""
    runtime = runtime_for(container)
    decision = await asyncio.to_thread(runtime.mode)
    graph = runtime.graph_for(None).get_graph()
    store = runtime.checkpoints
    try:
        checkpoint_ok = bool(await store.async_saver()) if store.enabled else False
        checkpoint_detail = None
    except Exception as exc:  # report, never crash the health endpoint
        checkpoint_ok, checkpoint_detail = False, type(exc).__name__
    ok = checkpoint_ok or not store.enabled
    return AgentHealthOut(
        status="ok" if ok else "degraded",
        graph={
            "name": "sprintmatch_agent",
            "nodes": sorted(n for n in graph.nodes if not n.startswith("__")),
            "edges": len(graph.edges),
        },
        tools=list(TOOL_NAMES),
        agent_mode=decision.mode.value,
        agent_mode_reason=decision.reason,
        capabilities=_caps(decision.capabilities),
        checkpointer={
            "backend": store.backend,
            "schema": store.schema if store.backend == "postgres" else None,
            "ok": checkpoint_ok,
            "detail": checkpoint_detail,
            "retention_days": store.retention_days,
        },
        limits=asdict(runtime.limits),
    )

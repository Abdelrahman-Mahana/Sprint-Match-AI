"""FastAPI dependency injection, API key auth, rate limiting, and health/readiness endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from app.agents.tools import TOOL_NAMES
from app.application.runner import runtime_for
from app.application.services import AppContainer, RequestContext, Services
from app.core import (
    ApiKeyAuthenticator,
    AuthenticationError,
    DataSourceUnavailableError,
    InputValidationError,
    Principal,
    RateLimiter,
    request_id_var,
)
from app.domain import Role, WindowPreset
from app.infrastructure.llm import ProviderCapabilities
from app.schemas import AgentHealthOut, ExecutionWindowRequest, LLMHealthOut

# ===========================================================================
# Auth, Rate Limit & Container Dependencies
# ===========================================================================

def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container


def get_principal(
    request: Request, x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None
) -> Principal:
    authenticator: ApiKeyAuthenticator = request.app.state.authenticator
    return authenticator.authenticate(x_api_key)


def _context(principal: Principal, container: AppContainer) -> RequestContext:
    with container.uow_factory() as uow:
        company = uow.companies.get_by_code(principal.company_code)
    if company is None:
        raise AuthenticationError("API key is bound to an unknown company")
    return RequestContext(
        company=company, actor=principal.name, role=principal.role, request_id=request_id_var.get() or "n/a"
    )


def require_role(role: Role):  # type: ignore[no-untyped-def]
    def dependency(
        principal: Annotated[Principal, Depends(get_principal)],
        container: Annotated[AppContainer, Depends(get_container)],
    ) -> RequestContext:
        principal.require(role)
        return _context(principal, container)

    return dependency


def rate_limited(request: Request, principal: Annotated[Principal, Depends(get_principal)]) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    limiter.check(principal.name)


def services(container: Annotated[AppContainer, Depends(get_container)]) -> Iterator[Services]:
    """One unit of work per request; routes commit explicitly when they write."""
    with container.uow_factory() as uow:
        yield container.services(uow)


Viewer = Annotated[RequestContext, Depends(require_role(Role.VIEWER))]
Manager = Annotated[RequestContext, Depends(require_role(Role.MANAGER))]
ServicesDep = Annotated[Services, Depends(services)]
ContainerDep = Annotated[AppContainer, Depends(get_container)]


def window_query(
    preset: Annotated[WindowPreset | None, Query(description="Execution window preset")] = None,
    start: Annotated[datetime | None, Query(description="Window start (ISO-8601 with offset)")] = None,
    deadline: Annotated[datetime | None, Query(description="Window end (ISO-8601 with offset)")] = None,
    working_days: Annotated[int | None, Query(ge=1, le=60)] = None,
) -> ExecutionWindowRequest | None:
    if preset is None and start is None and deadline is None:
        return ExecutionWindowRequest(preset=WindowPreset.NEXT_WORKING_DAYS, working_days=working_days)
    return ExecutionWindowRequest(preset=preset, start=start, deadline=deadline, working_days=working_days)


def as_of_query(
    as_of: Annotated[datetime | None, Query(description="Evaluation instant; defaults to now")] = None,
) -> datetime:
    if as_of is None:
        return datetime.now(UTC)
    if as_of.tzinfo is None:
        raise InputValidationError("as_of must include a timezone offset")
    return as_of


WindowDep = Annotated[ExecutionWindowRequest | None, Depends(window_query)]
AsOfDep = Annotated[datetime, Depends(as_of_query)]

# ===========================================================================
# Health & Readiness Routers
# ===========================================================================

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


# /api/v1/health/* (authenticated: these may call the LLM provider)
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

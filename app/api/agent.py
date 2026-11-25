"""Agent execution, SSE streaming, recommendation runs, and outcome feedback endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import AsOfDep, ContainerDep, Manager, ServicesDep, Viewer, rate_limited
from app.application.runner import RecommendationService, sse
from app.domain import EvaluationSummary, NewTaskOutcome, RecommendationStatus
from app.schemas import (
    AgentRunOut,
    CandidateOut,
    CheckpointOut,
    CreatedOut,
    ErrorResponse,
    NodeLogOut,
    RecommendationRequest,
    RecommendationResponse,
    RunErrorOut,
    TaskOutcomeRequest,
    ToolExecutionOut,
)

# ===========================================================================
# Agent Endpoints (/agent/*)
# ===========================================================================

agent_router = APIRouter(prefix="/agent", tags=["agent"])

_ERROR_STATUS = {
    "NOT_FOUND": 404,
    "INVALID_INPUT": 422,
    "DATABASE_UNAVAILABLE": 503,
    "CHECKPOINT_UNAVAILABLE": 503,
    "PROVIDER_UNAVAILABLE": 503,
    "TIMEOUT": 504,
}
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=120,
        description="Repeat-safe key: a second request with the same key returns the stored result",
    ),
]


def recommendation_http(response: RecommendationResponse, success_code: int) -> JSONResponse:
    """Business outcomes (NEEDS_INPUT, NO_SUITABLE_CANDIDATE ...) are successful requests; FAILED maps by cause."""
    code = 200 if response.idempotent_replay else success_code
    if response.status == RecommendationStatus.FAILED:
        code = next((_ERROR_STATUS[e.code] for e in response.errors if e.code in _ERROR_STATUS), 500)
    return JSONResponse(status_code=code, content=response.model_dump(mode="json"))


_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": RecommendationResponse},
    422: {"model": ErrorResponse},
    503: {"model": RecommendationResponse},
}


@agent_router.post(
    "/recommend",
    response_model=RecommendationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited)],
    responses=_RESPONSES,
    summary="Run the LangGraph recommendation agent (async; never assigns automatically)",
)
async def recommend(
    body: RecommendationRequest, ctx: Manager, container: ContainerDep, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    result = await RecommendationService(container).acreate(ctx, body, idempotency_key=idempotency_key)
    return recommendation_http(result, status.HTTP_201_CREATED)


@agent_router.post(
    "/recommend/stream",
    dependencies=[Depends(rate_limited)],
    summary="Run the agent and stream node/tool progress as Server-Sent Events (graph.astream)",
    response_class=StreamingResponse,
)
async def recommend_stream(
    body: RecommendationRequest, ctx: Manager, container: ContainerDep
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        async for event in RecommendationService(container).astream(ctx, body):
            yield sse(event)

    return StreamingResponse(events(), media_type="text/event-stream")


@agent_router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_run(run_id: UUID, ctx: Viewer, container: ContainerDep) -> AgentRunOut:
    return AgentRunOut.from_stored(RecommendationService(container).get_run(ctx, run_id))


@agent_router.get("/runs/{run_id}/logs", response_model=list[NodeLogOut], summary="Node-level execution log")
def run_logs(run_id: UUID, ctx: Viewer, container: ContainerDep) -> list[NodeLogOut]:
    logs = RecommendationService(container).node_logs(ctx, run_id)
    return [NodeLogOut.from_entry(i, e) for i, e in enumerate(logs, start=1)]


@agent_router.get("/runs/{run_id}/tools", response_model=list[ToolExecutionOut], summary="Tool calls of the agent")
def run_tools(run_id: UUID, ctx: Viewer, container: ContainerDep) -> list[ToolExecutionOut]:
    return [
        ToolExecutionOut.from_record(r) for r in RecommendationService(container).tool_executions(ctx, run_id)
    ]


@agent_router.get("/runs/{run_id}/errors", response_model=list[RunErrorOut])
def run_errors(run_id: UUID, ctx: Viewer, container: ContainerDep) -> list[RunErrorOut]:
    return [RunErrorOut.from_record(r) for r in RecommendationService(container).run_errors(ctx, run_id)]


@agent_router.get("/runs/{run_id}/candidates", response_model=list[CandidateOut])
def run_candidates(run_id: UUID, ctx: Viewer, container: ContainerDep) -> list[CandidateOut]:
    return RecommendationService(container).candidates(ctx, run_id)


@agent_router.get(
    "/runs/{run_id}/checkpoints",
    response_model=list[CheckpointOut],
    summary="Sanitized LangGraph checkpoint history (no state values)",
)
def run_checkpoints(run_id: UUID, ctx: Viewer, container: ContainerDep) -> list[CheckpointOut]:
    return [CheckpointOut(**c) for c in RecommendationService(container).checkpoints(ctx, run_id)]


@agent_router.post(
    "/runs/{run_id}/retry",
    response_model=RecommendationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited)],
    responses={**_RESPONSES, 409: {"model": ErrorResponse}},
    summary="Resume a run from its checkpoint (or re-run a completed FAILED run as a new linked run)",
)
async def retry_run(run_id: UUID, ctx: Manager, container: ContainerDep) -> JSONResponse:
    result = await RecommendationService(container).aresume(ctx, run_id)
    return recommendation_http(result, status.HTTP_201_CREATED)

# ===========================================================================
# Recommendation Endpoints (/recommendations/*)
# ===========================================================================

recommendations_router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@recommendations_router.post(
    "",
    response_model=RecommendationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited)],
    responses={
        404: {"model": RecommendationResponse},
        503: {"model": RecommendationResponse},
        422: {"model": ErrorResponse},
    },
    summary="Recommend an employee for a task via the LangGraph agent (never assigns automatically)",
)
async def create_recommendation(
    body: RecommendationRequest, ctx: Manager, container: ContainerDep, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    result = await RecommendationService(container).acreate(ctx, body, idempotency_key=idempotency_key)
    return recommendation_http(result, status.HTTP_201_CREATED)


@recommendations_router.get("/{recommendation_id}", response_model=RecommendationResponse)
def get_recommendation(
    recommendation_id: UUID, ctx: Viewer, container: ContainerDep
) -> RecommendationResponse:
    return RecommendationService(container).get(ctx, recommendation_id)


@recommendations_router.post(
    "/{recommendation_id}/revalidate",
    response_model=RecommendationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited)],
    summary="Re-evaluate the same task and window against current workforce data",
)
async def revalidate_recommendation(
    recommendation_id: UUID, ctx: Manager, container: ContainerDep, as_of: AsOfDep
) -> JSONResponse:
    result = await RecommendationService(container).arevalidate(ctx, recommendation_id, as_of=as_of)
    return recommendation_http(result, status.HTTP_201_CREATED)


@recommendations_router.get("/{recommendation_id}/candidates", response_model=list[CandidateOut])
def list_candidates(recommendation_id: UUID, ctx: Viewer, container: ContainerDep) -> list[CandidateOut]:
    return RecommendationService(container).candidates(ctx, recommendation_id)

# ===========================================================================
# Evaluation & Outcome Endpoints (/evaluation/*)
# ===========================================================================

evaluation_router = APIRouter(tags=["evaluation"])


@evaluation_router.post("/task-outcomes", response_model=CreatedOut, status_code=status.HTTP_201_CREATED)
def record_task_outcome(body: TaskOutcomeRequest, ctx: Manager, svc: ServicesDep) -> CreatedOut:
    outcome_id = svc.uow.performance.record_outcome(
        ctx.company.id,
        NewTaskOutcome(
            **body.model_dump(exclude={"reviewed_at"}), reviewed_at=body.reviewed_at or datetime.now(UTC)
        ),
    )
    svc.uow.audit.record(ctx.company.id, ctx.actor, "task_outcome.recorded", "task_outcome", str(outcome_id))
    svc.uow.commit()
    return CreatedOut(id=outcome_id)


@evaluation_router.get("/evaluation/metrics", response_model=EvaluationSummary)
@evaluation_router.get("/evaluation/summary", response_model=EvaluationSummary)
def evaluation_summary(ctx: Viewer, svc: ServicesDep) -> EvaluationSummary:
    return svc.uow.performance.evaluation_summary(ctx.company.id)

router = APIRouter()
router.include_router(agent_router)
router.include_router(recommendations_router)
router.include_router(evaluation_router)

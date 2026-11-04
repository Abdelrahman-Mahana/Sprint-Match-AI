from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.deps import AsOfDep, ContainerDep, Manager, Viewer, rate_limited
from app.api.v1.agent import IdempotencyKey, recommendation_http
from app.application.recommendations import RecommendationService
from app.schemas.common import ErrorResponse
from app.schemas.recommendations import CandidateOut, RecommendationRequest, RecommendationResponse

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post(
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


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
def get_recommendation(
    recommendation_id: UUID, ctx: Viewer, container: ContainerDep
) -> RecommendationResponse:
    return RecommendationService(container).get(ctx, recommendation_id)


@router.post(
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


@router.get("/{recommendation_id}/candidates", response_model=list[CandidateOut])
def list_candidates(recommendation_id: UUID, ctx: Viewer, container: ContainerDep) -> list[CandidateOut]:
    return RecommendationService(container).candidates(ctx, recommendation_id)

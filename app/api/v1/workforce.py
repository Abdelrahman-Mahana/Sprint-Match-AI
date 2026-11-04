from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.deps import AsOfDep, ServicesDep, Viewer, WindowDep
from app.application.container import Services
from app.application.context import RequestContext
from app.schemas.common import AvailabilityOut, WorkforceAnalysisOut, WorkforceAnalysisRequest
from app.schemas.recommendations import ExecutionWindowRequest

router = APIRouter(prefix="/workforce", tags=["workforce"])


def _analyze(
    ctx: RequestContext,
    svc: Services,
    window_req: ExecutionWindowRequest | None,
    as_of: datetime,
    employee_ids: list | None,
) -> WorkforceAnalysisOut:
    window, warnings = svc.workforce.resolve_window(ctx.company, as_of, window_req)
    ids = employee_ids or svc.uow.employees.list_employee_ids(ctx.company.id, active_only=True)
    profiles = svc.uow.employees.get_profiles(ctx.company.id, ids)
    results = svc.workforce.availability(ctx.company, list(profiles), window, as_of)
    rows = sorted(
        (
            AvailabilityOut(
                employee_id=eid,
                employee_code=profiles[eid].employee.employee_code,
                full_name=profiles[eid].employee.full_name,
                availability=a,
            )
            for eid, a in results.items()
        ),
        key=lambda r: r.employee_code or "",
    )
    return WorkforceAnalysisOut(
        window=window,
        warnings=warnings,
        employees=rows,
        status_counts=dict(Counter(r.availability.status.value for r in rows)),
    )


@router.post("/analyze", response_model=WorkforceAnalysisOut)
def analyze_workforce(body: WorkforceAnalysisRequest, ctx: Viewer, svc: ServicesDep) -> WorkforceAnalysisOut:
    window = (
        ExecutionWindowRequest(start=body.window_start, deadline=body.window_deadline)
        if body.window_deadline
        else ExecutionWindowRequest(preset="NEXT_WORKING_DAYS", working_days=body.working_days)
    )
    return _analyze(ctx, svc, window, body.as_of or datetime.now(UTC), body.employee_ids)


@router.get("/availability", response_model=WorkforceAnalysisOut)
def workforce_availability(
    ctx: Viewer, svc: ServicesDep, window: WindowDep, as_of: AsOfDep
) -> WorkforceAnalysisOut:
    return _analyze(ctx, svc, window, as_of, None)

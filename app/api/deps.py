from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Query, Request

from app.application.container import AppContainer, Services
from app.application.context import RequestContext
from app.core.exceptions import AuthenticationError, InputValidationError
from app.core.logging import request_id_var
from app.core.security import ApiKeyAuthenticator, Principal, RateLimiter
from app.domain.enums import Role, WindowPreset
from app.schemas.recommendations import ExecutionWindowRequest


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

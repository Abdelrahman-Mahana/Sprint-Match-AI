from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Engine, create_engine

from app.application.container import AppContainer
from app.application.context import RequestContext
from app.core.exceptions import DataSourceUnavailableError, NotFoundError
from app.domain.entities.task import TaskInput
from app.domain.enums import (
    AvailabilityStatus,
    PresenceStatus,
    RemainingEffortBasis,
    SkillVerificationStatus,
    WindowPreset,
)
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.schemas.recommendations import ExecutionWindowRequest
from tests.conftest import AS_OF

pytestmark = pytest.mark.integration


def _emp(container: AppContainer, ctx: RequestContext, code: str):  # type: ignore[no-untyped-def]
    with container.uow_factory() as uow:
        page = uow.employees.list_employees(ctx.company.id, limit=200)
    return next(e for e in page.items if e.employee_code == code)


def _window(container: AppContainer, ctx: RequestContext, days: int = 3):  # type: ignore[no-untyped-def]
    with container.uow_factory() as uow:
        w, _ = container.services(uow).workforce.resolve_window(
            ctx.company,
            AS_OF,
            ExecutionWindowRequest(preset=WindowPreset.NEXT_WORKING_DAYS, working_days=days),
        )
    return w


def test_employee_360_profile(container: AppContainer, ctx: RequestContext) -> None:
    ahmed = _emp(container, ctx, "EMP-001")
    with container.uow_factory() as uow:
        view = container.services(uow).employees.get_360(
            ctx.company, ahmed.id, as_of=AS_OF, window=_window(container, ctx)
        )
    p = view.profile
    assert p.employee.department_name == "Backend Engineering" and p.employee.team_name == "Payments Core"
    assert {s.skill_name for s in p.skills} >= {"Python", "FastAPI", "PostgreSQL"}
    statuses = {s.skill_name: s.verification_status for s in p.skills}
    assert statuses["Python"] == SkillVerificationStatus.VERIFIED
    assert statuses["Docker"] == SkillVerificationStatus.SELF_DECLARED  # verified vs self-declared kept apart
    assert any(e.evidence_type.value == "TASK_OUTCOME" for e in p.evidence)
    assert p.experiences and p.credentials and p.memberships
    assert view.performance.outcome_count == 6 and view.performance.excused_delays == 1
    assert view.weekly_scheduled_hours == 35.0
    assert view.availability.status == AvailabilityStatus.AVAILABLE
    assert len(view.current_assignments) == 1


def test_profiles_have_no_sensitive_personal_fields(container: AppContainer, ctx: RequestContext) -> None:
    fields = set(_emp(container, ctx, "EMP-001").model_dump())
    for forbidden in ("gender", "religion", "ethnicity", "age", "birth_date", "marital_status", "salary"):
        assert forbidden not in fields


def test_workforce_data_includes_logged_hours_and_assignee_counts(
    container: AppContainer, ctx: RequestContext
) -> None:
    window = _window(container, ctx)
    ids = [_emp(container, ctx, c).id for c in ("EMP-014", "EMP-016", "EMP-005")]
    with container.uow_factory() as uow:
        results = container.services(uow).workforce.availability(ctx.company, ids, window, AS_OF)
    pair, estimated, busy = (results[i] for i in ids)
    assert pair.items[0].basis == RemainingEffortBasis.UNKNOWN  # two assignees, no per-employee split
    assert pair.status == AvailabilityStatus.UNKNOWN
    assert estimated.items[0].basis == RemainingEffortBasis.ALLOCATION_MINUS_LOGGED
    assert estimated.items[0].remaining_hours == 10  # 16 allocated - 6 logged
    assert busy.presence == PresenceStatus.PRESENT and busy.status == AvailabilityStatus.BUSY


def test_tenant_isolation(container: AppContainer, ctx: RequestContext) -> None:
    with container.uow_factory() as uow:
        ids = uow.employees.list_employee_ids(ctx.company.id)
        codes = {p.employee.employee_code for p in uow.employees.get_profiles(ctx.company.id, ids).values()}
        other = uow.companies.get_by_code("DLT")
        assert other is not None
        foreign = uow.employees.list_employee_ids(other.id)
        # Asking for another tenant's employees through this tenant returns nothing.
        assert uow.employees.get_profiles(ctx.company.id, foreign) == {}
        assert uow.tasks.get_task_by_code(ctx.company.id, "DLT-T-1") is None
    assert all(c.startswith("EMP-") for c in codes) and len(codes) == 30


def test_unit_of_work_rolls_back_without_commit(container: AppContainer, ctx: RequestContext) -> None:
    with container.uow_factory() as uow:
        container.services(uow).tasks.create(
            ctx.company, "TMP-ROLLBACK", TaskInput(title="Temporary", estimated_hours=4)
        )
        assert uow.tasks.get_task_by_code(ctx.company.id, "TMP-ROLLBACK") is not None
    with container.uow_factory() as uow:
        assert uow.tasks.get_task_by_code(ctx.company.id, "TMP-ROLLBACK") is None
        with pytest.raises(NotFoundError):
            container.services(uow).tasks.get(ctx.company, task_code="TMP-ROLLBACK")


def test_database_unavailable_is_translated(test_settings, ctx: RequestContext) -> None:  # type: ignore[no-untyped-def]
    dead: Engine = create_engine(
        "postgresql+psycopg://nobody:nopw@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(dead))
    with factory() as uow, pytest.raises(DataSourceUnavailableError):
        uow.employees.list_employee_ids(ctx.company.id)


def test_window_resolution_uses_company_calendar(container: AppContainer, ctx: RequestContext) -> None:
    window = _window(container, ctx, days=1)
    assert window.start.date() == (AS_OF + timedelta(days=1)).date()  # Wed after Tue reference day

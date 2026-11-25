"""Integration tests for Alembic migrations, reproducible demo seeding, and SQLAlchemy repositories."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

from app.application.services import AppContainer, RequestContext
from app.core import ConflictError, DataSourceUnavailableError, NotFoundError
from app.domain import (
    AvailabilityStatus,
    PresenceStatus,
    RemainingEffortBasis,
    SkillVerificationStatus,
    TaskInput,
    WindowPreset,
)
from app.infrastructure.orm import Base, CompanyModel, EmployeeModel, create_session_factory
from app.infrastructure.repositories import SqlAlchemyUnitOfWorkFactory
from app.schemas import ExecutionWindowRequest
from scripts.cli import ROOT, upgrade
from scripts.demo_data import SeedRefusedError, generate_demo_dataset, seed_demo_data, validate_dataset
from tests.conftest import AS_OF, REFERENCE_DATE, SEED, reset_schema

# ===========================================================================
# Migrations & Seeding Integration Tests
# ===========================================================================

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def scratch_engine(database_url: str) -> Iterator[Engine]:
    """A separate, disposable database for destructive migration tests."""
    url = make_url(database_url)
    name = f"{url.database}_migrations"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar():
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    engine = create_engine(url.set(database=name))
    yield engine
    engine.dispose()


def test_migrations_build_schema_from_empty_and_match_models(scratch_engine: Engine) -> None:
    reset_schema(scratch_engine)
    url = scratch_engine.url.render_as_string(hide_password=False)
    upgrade(url)
    tables = set(inspect(scratch_engine).get_table_names())
    assert {t.name for t in Base.metadata.sorted_tables} <= tables
    with scratch_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"models and migrations drifted: {diff}"


def test_downgrade_to_base_and_upgrade_again(scratch_engine: Engine) -> None:
    url = scratch_engine.url.render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    assert set(inspect(scratch_engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")
    assert "employees" in inspect(scratch_engine).get_table_names()


def test_seed_refuses_non_empty_database_and_production(scratch_engine: Engine) -> None:
    seed_demo_data(scratch_engine, environment="test", reference_date=REFERENCE_DATE, seed=SEED, reset=True)
    with pytest.raises(ConflictError):
        seed_demo_data(scratch_engine, environment="test", reference_date=REFERENCE_DATE, seed=SEED)
    with pytest.raises(SeedRefusedError):
        seed_demo_data(scratch_engine, environment="production", reference_date=REFERENCE_DATE, seed=SEED)


def test_reseeding_is_reproducible(scratch_engine: Engine) -> None:
    def snapshot() -> list[tuple]:
        with scratch_engine.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    select(EmployeeModel.id, EmployeeModel.employee_code).order_by(
                        EmployeeModel.employee_code
                    )
                )
            ]

    seed_demo_data(scratch_engine, environment="test", reference_date=REFERENCE_DATE, seed=SEED, reset=True)
    first = snapshot()
    seed_demo_data(scratch_engine, environment="test", reference_date=REFERENCE_DATE, seed=SEED, reset=True)
    assert snapshot() == first


def test_generator_is_deterministic_and_relative_to_reference_date() -> None:
    a = generate_demo_dataset(REFERENCE_DATE, SEED)
    b = generate_demo_dataset(REFERENCE_DATE, SEED)
    assert a.rows == b.rows
    later = generate_demo_dataset(date(2027, 3, 10), SEED)
    shift = later.reference_date - a.reference_date
    first_a = min(r["start_date"] for r in a.rows["sprints"])
    first_later = min(r["start_date"] for r in later.rows["sprints"])
    assert first_later - first_a == shift


def test_demo_dataset_meets_targets(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        nds = conn.execute(select(CompanyModel.id).where(CompanyModel.code == "NDS")).scalar_one()

        def count(sql: str) -> int:
            return int(conn.execute(text(sql), {"c": nds}).scalar_one())

        assert count("SELECT count(*) FROM employees WHERE company_id = :c") == 30
        assert count("SELECT count(*) FROM departments WHERE company_id = :c") >= 5
        assert count("SELECT count(*) FROM skills WHERE company_id = :c") >= 18
        assert count("SELECT count(*) FROM projects WHERE company_id = :c") == 6
        assert (
            10
            <= count(
                "SELECT count(*) FROM sprints s JOIN projects p ON p.id = s.project_id "
                "WHERE p.company_id = :c"
            )
            <= 12
        )
        assert count("SELECT count(*) FROM tasks WHERE company_id = :c") >= 70
        assert (
            count(
                "SELECT count(*) FROM task_assignments a JOIN tasks t ON t.id = a.task_id "
                "WHERE t.company_id = :c"
            )
            >= 100
        )
        for table in (
            "attendance_records",
            "leave_records",
            "work_schedule_days",
            "timesheet_entries",
            "task_outcomes",
            "employee_skill_evidence",
        ):
            assert count(f"SELECT count(*) FROM {table}") > 0
        assert count("SELECT count(*) FROM leave_records WHERE status = 'REQUESTED'") >= 1
        assert count("SELECT count(*) FROM leave_records WHERE status = 'APPROVED'") >= 3
        assert conn.execute(select(func.count()).select_from(CompanyModel)).scalar_one() == 2


def test_dataset_passes_all_consistency_checks(pg_engine: Engine) -> None:
    failed = [r for r in validate_dataset(pg_engine) if not r.passed]
    assert failed == []

# ===========================================================================
# Repository & Tenant Isolation Integration Tests
# ===========================================================================

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

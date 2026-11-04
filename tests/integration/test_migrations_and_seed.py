from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

from app.core.exceptions import ConflictError
from app.infrastructure.database.models import Base, CompanyModel, EmployeeModel
from app.infrastructure.demo.generator import generate_demo_dataset
from app.infrastructure.demo.seeder import SeedRefusedError, seed_demo_data
from app.infrastructure.demo.validation import validate_dataset
from scripts.init_db import ROOT, upgrade
from tests.conftest import REFERENCE_DATE, SEED, reset_schema

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
        assert count("SELECT count(*) FROM departments WHERE company_id = :c") == 5
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

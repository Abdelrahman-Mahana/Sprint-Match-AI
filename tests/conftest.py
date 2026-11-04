"""Shared fixtures.

Integration/agent/API tests need PostgreSQL. The URL comes from TEST_DATABASE_URL (environment or .env). The
test database is wiped, migrated from empty with Alembic and seeded with the fixed demo reference date, so
results are independent of the current date. Without a reachable database those tests are skipped.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import dotenv_values
from sqlalchemy import Engine, create_engine, text

from app.application.container import AppContainer
from app.application.context import RequestContext
from app.core.config import Settings
from app.domain.enums import Role
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.infrastructure.demo.seeder import seed_demo_data
from scripts.init_db import upgrade

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DATE = date(2026, 11, 3)
AS_OF = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Africa/Cairo"))
SEED = 20261103


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")


def reset_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"test database unreachable: {type(exc).__name__}")
    finally:
        engine.dispose()
    return url


@pytest.fixture(scope="session")
def pg_engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url, pool_pre_ping=True)
    reset_schema(engine)
    upgrade(database_url)  # schema exclusively from migrations
    seed_demo_data(engine, environment="test", reference_date=REFERENCE_DATE, seed=SEED)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def test_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        database_url=database_url,
        llm_provider="none",
        log_json=False,
        api_keys=[],
        checkpointer="memory",
        agent_retry_initial_interval_seconds=0.01,
    )


def make_container(settings: Settings, engine: Engine, llm=None) -> AppContainer:  # type: ignore[no-untyped-def]
    return AppContainer(settings, SqlAlchemyUnitOfWorkFactory(create_session_factory(engine)), llm)


@pytest.fixture(scope="session")
def container(test_settings: Settings, pg_engine: Engine) -> AppContainer:
    return make_container(test_settings, pg_engine)


@pytest.fixture(scope="session")
def ctx(container: AppContainer) -> RequestContext:
    with container.uow_factory() as uow:
        company = uow.companies.get_by_code("NDS")
    assert company is not None
    return RequestContext(company=company, actor="pytest-manager", role=Role.MANAGER, request_id="pytest")

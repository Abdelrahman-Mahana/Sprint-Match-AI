"""Unit of work over a discovered foreign database.

``AdaptiveUnitOfWorkFactory(engine, mapping)`` opens one connection per unit of work, with the same
read-only + statement-timeout contract the agent tools rely on. Every business-data repository is
read-only. The application's own persistence (recommendation runs, audit trail) is delegated to an
optional internal unit-of-work factory: a connected external system is never asked to store the
agent's output.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.domain.interfaces import UnitOfWorkFactory
from app.infrastructure.adaptive.ids import IdRegistry
from app.infrastructure.adaptive.repositories import (
    AdaptiveCompanyRepository,
    AdaptiveEmployeeRepository,
    AdaptivePerformanceRepository,
    AdaptiveTaskRepository,
    AdaptiveWorkforceRepository,
    InMemoryAuditRepository,
    InMemoryRecommendationRepository,
)
from app.infrastructure.adaptive.sql import dialect_of
from app.infrastructure.schema_discovery.mapping import SchemaMapping


class AdaptiveUnitOfWork:
    def __init__(
        self,
        engine: Engine,
        mapping: SchemaMapping,
        registry: IdRegistry,
        *,
        read_only: bool = True,
        statement_timeout_ms: int | None = None,
        default_timezone: str = "UTC",
        assume_default_schedule: bool = True,
        default_daily_hours: float = 8.0,
        internal_factory: UnitOfWorkFactory | None = None,
        company_code: str = "default",
    ) -> None:
        self._engine = engine
        self._mapping = mapping
        self._registry = registry
        self._read_only = read_only
        self._timeout_ms = statement_timeout_ms
        self._tz = default_timezone
        self._assume_schedule = assume_default_schedule
        self._daily_hours = default_daily_hours
        self._internal_factory = internal_factory
        self._company_code = company_code
        self._conn = None
        self._internal: Any = None

    # Declared for the Protocol; assigned in __enter__.
    companies: AdaptiveCompanyRepository
    employees: AdaptiveEmployeeRepository
    tasks: AdaptiveTaskRepository
    workforce: AdaptiveWorkforceRepository
    performance: AdaptivePerformanceRepository
    recommendations: Any
    audit: Any

    def __enter__(self) -> Self:
        self._conn = self._engine.connect()
        try:
            self._apply_guards()
        except Exception:
            self._conn.close()
            raise
        kwargs = {
            "dialect": dialect_of(self._engine),
            "company_code": self._company_code,
            "default_timezone": self._tz,
            "assume_default_schedule": self._assume_schedule,
            "default_daily_hours": self._daily_hours,
        }
        self.companies = AdaptiveCompanyRepository(self._conn, self._mapping, self._registry, **kwargs)
        self.employees = AdaptiveEmployeeRepository(self._conn, self._mapping, self._registry, **kwargs)
        self.tasks = AdaptiveTaskRepository(self._conn, self._mapping, self._registry, **kwargs)
        self.workforce = AdaptiveWorkforceRepository(self._conn, self._mapping, self._registry, **kwargs)
        self.performance = AdaptivePerformanceRepository(self._conn, self._mapping, self._registry, **kwargs)

        if self._internal_factory is not None:
            self._internal = self._internal_factory(read_only=False)
            self._internal.__enter__()
            self.recommendations = self._internal.recommendations
            self.audit = self._internal.audit
        else:
            self.recommendations = InMemoryRecommendationRepository()
            self.audit = InMemoryAuditRepository()
        return self

    def _apply_guards(self) -> None:
        assert self._conn is not None
        dialect = dialect_of(self._engine)
        if self._read_only:
            if dialect == "postgresql":
                # Enforced by the server: any write in this transaction fails.
                self._conn.execute(text("SET TRANSACTION READ ONLY"))
        if self._timeout_ms:
            ms = int(self._timeout_ms)
            if dialect == "postgresql":
                self._conn.execute(text(f"SET LOCAL statement_timeout = {ms}"))
            elif dialect == "sqlite":
                self._conn.execute(text(f"PRAGMA busy_timeout = {ms}"))

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._internal is not None:
            try:
                self._internal.__exit__(exc_type, exc, tb)
            except Exception:
                pass
        if self._conn is not None:
            try:
                self._conn.rollback()  # a read-only transaction has nothing to commit
            except Exception:
                pass
            finally:
                self._conn.close()
                self._conn = None

    def commit(self) -> None:
        if self._internal is not None:
            self._internal.commit()

    def rollback(self) -> None:
        if self._internal is not None:
            self._internal.rollback()

    def ping(self) -> None:
        assert self._conn is not None
        self._conn.execute(text("SELECT 1"))


class AdaptiveUnitOfWorkFactory:
    """Callable matching ``app.domain.interfaces.UnitOfWorkFactory``."""

    def __init__(
        self,
        engine: Engine,
        mapping: SchemaMapping,
        *,
        default_timezone: str = "UTC",
        assume_default_schedule: bool = True,
        default_daily_hours: float = 8.0,
        internal_factory: UnitOfWorkFactory | None = None,
        company_code: str = "default",
    ) -> None:
        if not mapping.is_usable():
            from app.infrastructure.adaptive.sql import MappingError

            raise MappingError(
                "The discovered schema has no employees table; review schema_mapping.json "
                "or connect a database that stores employees"
            )
        self.engine = engine
        self.mapping = mapping
        self.registry = IdRegistry()
        self._tz = default_timezone
        self._assume_schedule = assume_default_schedule
        self._daily_hours = default_daily_hours
        self._internal_factory = internal_factory
        self._company_code = company_code

    def __call__(
        self, *, read_only: bool = False, statement_timeout_ms: int | None = None
    ) -> AdaptiveUnitOfWork:
        return AdaptiveUnitOfWork(
            self.engine,
            self.mapping,
            self.registry,
            read_only=read_only or True,  # the foreign source is always opened read-only
            statement_timeout_ms=statement_timeout_ms,
            default_timezone=self._tz,
            assume_default_schedule=self._assume_schedule,
            default_daily_hours=self._daily_hours,
            internal_factory=self._internal_factory,
            company_code=self._company_code,
        )

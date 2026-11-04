from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domain.interfaces import (
    AuditRepository,
    CompanyRepository,
    EmployeeRepository,
    PerformanceRepository,
    RecommendationRepository,
    TaskRepository,
    WorkforceRepository,
)
from app.infrastructure.database.repositories.base import translate_db_errors
from app.infrastructure.database.repositories.employees import SqlCompanyRepository, SqlEmployeeRepository
from app.infrastructure.database.repositories.performance import SqlPerformanceRepository
from app.infrastructure.database.repositories.recommendations import (
    SqlAuditRepository,
    SqlRecommendationRepository,
)
from app.infrastructure.database.repositories.tasks import SqlTaskRepository
from app.infrastructure.database.repositories.workforce import SqlWorkforceRepository


class SqlAlchemyUnitOfWork:
    """PostgreSQL implementation of ``app.domain.interfaces.UnitOfWork``. Rolls back unless committed.

    ``read_only`` opens the transaction with ``SET TRANSACTION READ ONLY`` (PostgreSQL rejects any write), and
    ``statement_timeout_ms`` bounds every statement server-side. Agent tools always use both.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        read_only: bool = False,
        statement_timeout_ms: int | None = None,
    ) -> None:
        self._factory = session_factory
        self.read_only = read_only
        self._statement_timeout_ms = statement_timeout_ms

    companies: CompanyRepository
    employees: EmployeeRepository
    tasks: TaskRepository
    workforce: WorkforceRepository
    performance: PerformanceRepository
    recommendations: RecommendationRepository
    audit: AuditRepository

    def __enter__(self) -> Self:
        self.session = self._factory()
        self.companies = SqlCompanyRepository(self.session)
        self.employees = SqlEmployeeRepository(self.session)
        self.tasks = SqlTaskRepository(self.session)
        self.workforce = SqlWorkforceRepository(self.session)
        self.performance = SqlPerformanceRepository(self.session)
        self.recommendations = SqlRecommendationRepository(self.session)
        self.audit = SqlAuditRepository(self.session)
        if self.read_only or self._statement_timeout_ms:
            try:
                with translate_db_errors():
                    if self.read_only:
                        self.session.execute(text("SET TRANSACTION READ ONLY"))
                    if self._statement_timeout_ms:
                        ms = int(self._statement_timeout_ms)
                        self.session.execute(text(f"SET LOCAL statement_timeout = {ms}"))
            except Exception:
                self.session.close()
                raise
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            self.session.rollback()  # no-op after a successful commit
        except Exception:  # noqa: S110 - closing must never mask the original error
            pass
        finally:
            self.session.close()

    def commit(self) -> None:
        with translate_db_errors():
            self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def ping(self) -> None:
        with translate_db_errors():
            self.session.execute(text("SELECT 1"))


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __call__(
        self, *, read_only: bool = False, statement_timeout_ms: int | None = None
    ) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            self._session_factory, read_only=read_only, statement_timeout_ms=statement_timeout_ms
        )

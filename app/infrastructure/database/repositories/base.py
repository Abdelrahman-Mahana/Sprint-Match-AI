from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session

from app.core.exceptions import DataSourceUnavailableError


def to_float(value: Decimal | float | int | None) -> float | None:
    return float(value) if value is not None else None


@contextmanager
def translate_db_errors() -> Iterator[None]:
    """Map driver-level connectivity failures to the data-source-agnostic DataSourceUnavailableError."""
    try:
        yield
    except (sa_exc.OperationalError, sa_exc.InterfaceError) as exc:
        raise DataSourceUnavailableError(
            "The database is unavailable",
            details={"error": type(exc.orig).__name__ if exc.orig else "unknown"},
        ) from exc
    except sa_exc.DisconnectionError as exc:
        raise DataSourceUnavailableError("The database connection was lost") from exc


class SqlRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def _execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        with translate_db_errors():
            return self._s.execute(stmt, params or {})

"""Small, safe SQL helpers for reading a foreign schema.

Identifiers are validated against a strict pattern and then double-quoted (accepted by PostgreSQL and
SQLite); values always travel as bound parameters, never interpolated.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core.exceptions import SprintMatchError

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")


class MappingError(SprintMatchError):
    """The schema mapping cannot address the requested data (missing table, unusable column)."""

    code = "SCHEMA_MAPPING_ERROR"
    http_status = 422


class ReadOnlySourceError(SprintMatchError):
    """A write was attempted against the connected external database, which is read-only."""

    code = "READ_ONLY_SOURCE"
    http_status = 403


def quote(identifier: str) -> str:
    if not _IDENTIFIER.match(identifier):
        raise MappingError(f"Unsafe SQL identifier: {identifier[:60]!r}")
    return f'"{identifier}"'


def run(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]


def run_one(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = run(conn, sql, params)
    return rows[0] if rows else None


def dialect_of(engine: Engine) -> str:
    return engine.dialect.name

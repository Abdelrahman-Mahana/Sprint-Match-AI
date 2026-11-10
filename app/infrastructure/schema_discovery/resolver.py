"""Decides *how* to talk to the configured database and builds the matching unit-of-work factory.

Three cases:

* **native** - the database has the application's own schema (its own PostgreSQL store). The ORM
  repositories are used: full feature set, including writes and the recommendation history.
* **adaptive** - any other SQL database: schema discovery resolves the tables/columns, and the
  read-only adaptive repositories serve the same ``UnitOfWorkFactory`` protocol. The application's
  own output is persisted through the *internal* factory (the app's store), never the foreign one.
* **auto** (default) - native when the schema matches, adaptive otherwise.

A saved mapping (``schema_mapping.json``) is reused when it exists and the target tables are still
present, so a connected system is discovered once and stays fast.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.domain.interfaces import UnitOfWorkFactory
from app.infrastructure.adaptive.unit_of_work import AdaptiveUnitOfWorkFactory
from app.infrastructure.adaptive.sql import MappingError
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.infrastructure.schema_discovery.discovery import SchemaDiscovery
from app.infrastructure.schema_discovery.mapping import (
    SchemaMapping,
    load_mapping,
    save_mapping,
)

logger = logging.getLogger(__name__)

# Columns the application's ORM needs on its core tables. Present together they mean "our schema".
_NATIVE_MARKERS: dict[str, tuple[str, ...]] = {
    "employees": ("employee_code", "company_id", "employment_status"),
    "tasks": ("task_code", "company_id", "title"),
    "companies": ("code", "timezone"),
    "skills": ("name", "category"),
}

# A saved mapping is reused only while the tables it names still exist.
_MAPPING_FRESHNESS_TABLES = 3


@dataclass
class ResolvedDataSource:
    kind: str  # "native" | "adaptive"
    factory: UnitOfWorkFactory
    mapping: SchemaMapping | None = None
    reason: str = ""

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "reason": self.reason}
        if self.mapping is not None:
            out["discovered_concepts"] = self.mapping.available_concepts()
            out["mapping_file"] = str(_mapping_path())
        return out


def _mapping_path(settings: Settings | None = None) -> Path:
    if settings is not None:
        return Path(settings.schema_mapping_path)
    return Path("schema_mapping.json")


def looks_like_native_schema(engine: Engine) -> bool:
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        for table, required in _NATIVE_MARKERS.items():
            if table not in tables:
                return False
            columns = {c["name"].lower() for c in inspector.get_columns(table)}
            if not set(required) <= columns:
                return False
        return True
    except Exception as exc:
        logger.warning("native-schema probe failed: %s", exc)
        return False


def _saved_mapping_is_usable(mapping: SchemaMapping | None, engine: Engine) -> bool:
    if mapping is None or not mapping.is_usable():
        return False
    try:
        tables = set(inspect(engine).get_table_names())
    except Exception:
        return False
    present = [
        getattr(mapping, concept).actual_table
        for concept in mapping.available_concepts()
    ]
    return sum(1 for t in present if t in tables) >= _MAPPING_FRESHNESS_TABLES


def resolve(
    engine: Engine,
    settings: Settings,
    *,
    db_url_masked: str = "",
    internal_factory: UnitOfWorkFactory | None = None,
    force: str | None = None,
) -> ResolvedDataSource:
    """Build the factory for ``engine`` according to ``settings.database_adapter``."""
    adapter = force or settings.database_adapter

    if adapter in ("native", "auto") and looks_like_native_schema(engine):
        logger.info("data source resolved as native (application schema)")
        return ResolvedDataSource(
            kind="native",
            factory=SqlAlchemyUnitOfWorkFactory(create_session_factory(engine)),
            reason="the database has the application's own schema",
        )
    if adapter == "native":
        logger.warning("database_adapter=native but the schema does not match; falling back to adaptive")

    mapping = load_mapping(_mapping_path(settings))
    if not _saved_mapping_is_usable(mapping, engine):
        try:
            mapping = SchemaDiscovery(engine).discover(db_url_masked=db_url_masked)
        except Exception as exc:
            raise MappingError(
                "Schema discovery failed for the connected database",
                details={"error": type(exc).__name__},
            ) from exc
        if mapping.is_usable():
            try:
                save_mapping(mapping, _mapping_path(settings))
            except OSError as exc:  # read-only filesystem: keep the in-memory mapping
                logger.warning("could not save schema mapping: %s", exc)
    if not mapping.is_usable():
        raise MappingError(
            "No employees table could be identified in the connected database. "
            f"Edit {_mapping_path(settings)} to map the right tables and retry."
        )

    factory = AdaptiveUnitOfWorkFactory(
        engine,
        mapping,
        default_timezone=settings.adaptive_default_timezone,
        assume_default_schedule=settings.adaptive_assume_default_schedule,
        default_daily_hours=settings.adaptive_default_daily_hours,
        internal_factory=internal_factory,
    )
    logger.info(
        "data source resolved as adaptive: %s",
        ", ".join(mapping.available_concepts()),
    )
    return ResolvedDataSource(
        kind="adaptive",
        factory=factory,
        mapping=mapping,
        reason="schema discovered from the connected database",
    )

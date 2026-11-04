"""LangGraph checkpoint storage (graph execution state - not recommendation history).

* ``postgres``: the official ``PostgresSaver`` (sync paths: scripts, ``invoke``/``stream``) and
  ``AsyncPostgresSaver`` (FastAPI: ``ainvoke``/``astream``) on psycopg connection pools. Tables are created
  by the savers' own ``setup()`` inside a dedicated schema (``CHECKPOINT_SCHEMA``, default
  ``agent_checkpoints``), separate from the Alembic-managed business tables in ``public``.
* ``memory``: ``InMemorySaver`` (tests, single-process demos).
* ``none``: no checkpointing (runs cannot be resumed or inspected).

Thread id = recommendation id: one independent thread per run. Retention: ``prune`` deletes the threads of
runs older than ``CHECKPOINT_RETENTION_DAYS``; the business records in ``recommendation_runs`` are kept.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.domain.interfaces import UnitOfWorkFactory

logger = logging.getLogger(__name__)


def libpq_url(database_url: str) -> str:
    """SQLAlchemy URL (``postgresql+psycopg://``) -> libpq conninfo URL for psycopg."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _connection_kwargs(schema: str) -> dict[str, Any]:
    # Official requirements of the Postgres savers: autocommit, dict rows, no server-side prepared statements.
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": f"-c search_path={schema}",
    }


class CheckpointStore:
    def __init__(self, settings: Settings, database_url: str | None = None) -> None:
        self.backend = settings.checkpointer
        self.schema = settings.checkpoint_schema
        self.retention_days = settings.checkpoint_retention_days
        self._url = libpq_url(database_url or settings.database_url.get_secret_value())
        self._memory = InMemorySaver() if self.backend == "memory" else None
        self._pool: ConnectionPool | None = None
        self._sync: PostgresSaver | None = None
        self._apool: AsyncConnectionPool | None = None
        self._async: AsyncPostgresSaver | None = None

    @property
    def enabled(self) -> bool:
        return self.backend != "none"

    def _ensure_schema(self, conn: Any) -> None:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))

    # --- sync -----------------------------------------------------------------------------------------
    def sync_saver(self) -> BaseCheckpointSaver[Any] | None:
        if self.backend == "none":
            return None
        if self._memory is not None:
            return self._memory
        if self._sync is None:
            pool = ConnectionPool(
                self._url, min_size=1, max_size=4, kwargs=_connection_kwargs(self.schema), open=True
            )
            with pool.connection() as conn:
                self._ensure_schema(conn)
            saver = PostgresSaver(pool)  # type: ignore[arg-type]
            saver.setup()
            self._pool, self._sync = pool, saver
            logger.info("checkpointer ready", extra={"backend": "postgres", "schema": self.schema})
        return self._sync

    # --- async (FastAPI) ---------------------------------------------------------------------------------
    async def async_saver(self) -> BaseCheckpointSaver[Any] | None:
        if self.backend == "none":
            return None
        if self._memory is not None:
            return self._memory
        if self._async is None:
            if sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
                raise RuntimeError(
                    "psycopg async needs a selector event loop on Windows: run uvicorn with "
                    "--loop app.core.event_loop:selector_loop_factory (see README)"
                )
            pool = AsyncConnectionPool(
                self._url, min_size=1, max_size=8, kwargs=_connection_kwargs(self.schema), open=False
            )
            await pool.open()
            async with pool.connection() as conn:
                await conn.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema))
                )
            saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
            await saver.setup()
            self._apool, self._async = pool, saver
            logger.info("async checkpointer ready", extra={"backend": "postgres", "schema": self.schema})
        return self._async

    def ping(self) -> bool:
        saver = self.sync_saver()
        if saver is None or self._memory is not None:
            return saver is not None
        assert self._pool is not None
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")
        return True

    # --- retention --------------------------------------------------------------------------------------
    def prune(self, uow_factory: UnitOfWorkFactory, *, now: datetime | None = None) -> int:
        """Delete checkpoint threads of runs last updated before the retention cutoff."""
        saver = self.sync_saver()
        if saver is None:
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        with uow_factory(read_only=True) as uow:
            run_ids = uow.recommendations.list_run_ids_before(cutoff)
        for run_id in run_ids:
            saver.delete_thread(str(run_id))
        logger.info("checkpoints pruned", extra={"threads": len(run_ids), "cutoff": cutoff.isoformat()})
        return len(run_ids)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool, self._sync = None, None

    async def aclose(self) -> None:
        if self._apool is not None:
            await self._apool.close()
            self._apool, self._async = None, None
        self.close()

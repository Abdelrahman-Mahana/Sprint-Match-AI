"""Delete LangGraph checkpoint threads older than CHECKPOINT_RETENTION_DAYS (business records are kept).

Usage:  uv run python -m scripts.prune_checkpoints
"""

from __future__ import annotations

from app.agents.checkpointer import CheckpointStore
from app.core.config import get_settings
from app.infrastructure.database.session import create_db_engine, create_session_factory
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory

if __name__ == "__main__":
    settings = get_settings()
    store = CheckpointStore(settings)
    try:
        uow = SqlAlchemyUnitOfWorkFactory(create_session_factory(create_db_engine(settings)))
        n = store.prune(uow)
    finally:
        store.close()
    print(f"Pruned checkpoint threads of {n} run(s) older than {settings.checkpoint_retention_days} days.")

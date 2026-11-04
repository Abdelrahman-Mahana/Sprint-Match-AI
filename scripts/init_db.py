"""Create/upgrade the database schema to the latest Alembic revision and prepare LangGraph checkpoint storage.

Usage:  uv run python -m scripts.init_db
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]


def upgrade(database_url: str | None = None, revision: str = "head") -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    if database_url:
        cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, revision)


def setup_checkpoints() -> str:
    """Create the LangGraph checkpoint tables with the official ``PostgresSaver.setup()``."""
    from app.agents.checkpointer import CheckpointStore
    from app.core.config import get_settings

    settings = get_settings()
    store = CheckpointStore(settings)
    try:
        store.sync_saver()
    finally:
        store.close()
    return settings.checkpointer


if __name__ == "__main__":
    upgrade()
    print("Database schema is at the latest revision.")
    backend = setup_checkpoints()
    print(f"Checkpoint storage ready (backend: {backend}).")

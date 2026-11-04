from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from app.core.config import Settings, get_settings
from app.infrastructure.database.session import create_db_engine


def engine_and_settings(database_url: str | None = None) -> tuple[Engine, Settings]:
    settings = get_settings()
    return create_db_engine(settings, url=database_url), settings


def reference_date(settings: Settings, override: str | None) -> date:
    if override:
        return date.fromisoformat(override)
    if settings.demo_reference_date:
        return settings.demo_reference_date
    return datetime.now(ZoneInfo("Africa/Cairo")).date()

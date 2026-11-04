"""Loading, resetting and inspecting the demo dataset."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, SprintMatchError
from app.infrastructure.database.models import Base, CompanyModel
from app.infrastructure.demo.generator import TABLE_ORDER, DemoDataset, generate_demo_dataset


class SeedRefusedError(SprintMatchError):
    code = "SEED_REFUSED"


def guard_environment(environment: str) -> None:
    if environment == "production":
        raise SeedRefusedError("Demo data must never be loaded into a production environment")


def has_data(engine: Engine) -> bool:
    with Session(engine) as s:
        return s.execute(select(func.count()).select_from(CompanyModel)).scalar_one() > 0


def reset_all_data(engine: Engine) -> None:
    """Remove all application rows (schema and alembic_version are kept)."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


def load_dataset(engine: Engine, dataset: DemoDataset) -> dict[str, int]:
    tables = Base.metadata.tables
    with Session(engine) as s, s.begin():
        for name in TABLE_ORDER:
            rows = dataset.rows[name]
            if rows:
                s.execute(insert(tables[name]), rows)
    return dataset.counts()


def seed_demo_data(
    engine: Engine, *, environment: str, reference_date: date, seed: int, reset: bool = False
) -> DemoDataset:
    guard_environment(environment)
    if has_data(engine):
        if not reset:
            raise ConflictError("Database already contains data; use --reset to replace it")
        reset_all_data(engine)
    dataset = generate_demo_dataset(reference_date, seed)
    load_dataset(engine, dataset)
    return dataset

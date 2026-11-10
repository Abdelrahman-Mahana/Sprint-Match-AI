"""Adaptive access to any connected SQL database.

``AdaptiveUnitOfWorkFactory`` implements the same ``UnitOfWorkFactory`` protocol as the built-in
PostgreSQL store, but reads the tables/columns resolved by ``app.infrastructure.schema_discovery``.
Engines, scoring, the graph and the API never learn which system they are talking to.
"""

from app.infrastructure.adaptive.sql import MappingError, ReadOnlySourceError
from app.infrastructure.adaptive.unit_of_work import (
    AdaptiveUnitOfWork,
    AdaptiveUnitOfWorkFactory,
)

__all__ = [
    "AdaptiveUnitOfWork",
    "AdaptiveUnitOfWorkFactory",
    "MappingError",
    "ReadOnlySourceError",
]

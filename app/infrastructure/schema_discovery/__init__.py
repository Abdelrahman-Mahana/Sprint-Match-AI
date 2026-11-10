"""Schema discovery: inspects any SQL database and maps its tables/columns to domain concepts."""

from app.infrastructure.schema_discovery.discovery import SchemaDiscovery
from app.infrastructure.schema_discovery.mapping import (
    ColumnMap,
    SchemaMapping,
    TableMapping,
    load_mapping,
    save_mapping,
)
from app.infrastructure.schema_discovery.resolver import resolve

__all__ = [
    "SchemaDiscovery",
    "SchemaMapping",
    "TableMapping",
    "ColumnMap",
    "load_mapping",
    "save_mapping",
    "resolve",
]

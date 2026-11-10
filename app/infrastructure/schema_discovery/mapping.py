"""Schema mapping: domain concept -> actual table/column names in a connected database.

A SchemaMapping is produced by SchemaDiscovery (auto-detected heuristics) and can be saved to a
JSON file so the user can review and correct it once per system. On the next connection the saved
mapping is loaded instead of re-running discovery.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Every domain concept the discovery may map. Order is the discovery order (parents first).
CONCEPTS: tuple[str, ...] = (
    "companies",
    "employees",
    "skills",
    "departments",
    "teams",
    "projects",
    "tasks",
    "leaves",
    "attendance",
    "employee_skills",
    "task_assignments",
    "task_required_skills",
    "project_members",
    "work_schedule",
    "holidays",
    "sprints",
    "task_outcomes",
    "timesheets",
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ColumnMap:
    """Maps one domain field to a real column name (or None if absent in this DB)."""

    domain_field: str
    actual_column: str | None
    confidence: float = 1.0  # 0-1
    notes: str = ""


@dataclass
class TableMapping:
    """Maps one domain concept (e.g. 'employees') to a real table and its columns."""

    domain_concept: str
    actual_table: str | None
    confidence: float = 1.0
    columns: dict[str, ColumnMap] = field(default_factory=dict)
    notes: str = ""

    # Convenience: return the mapped column name or the fallback
    def col(self, domain_field: str, fallback: str | None = None) -> str | None:
        cm = self.columns.get(domain_field)
        return cm.actual_column if cm else fallback


@dataclass
class SchemaMapping:
    """Complete mapping for all domain concepts the agent needs."""

    # Core domain tables
    employees: TableMapping = field(
        default_factory=lambda: TableMapping("employees", None)
    )
    skills: TableMapping = field(
        default_factory=lambda: TableMapping("skills", None)
    )
    employee_skills: TableMapping = field(
        default_factory=lambda: TableMapping("employee_skills", None)
    )
    departments: TableMapping = field(
        default_factory=lambda: TableMapping("departments", None)
    )
    teams: TableMapping = field(
        default_factory=lambda: TableMapping("teams", None)
    )
    tasks: TableMapping = field(
        default_factory=lambda: TableMapping("tasks", None)
    )
    task_assignments: TableMapping = field(
        default_factory=lambda: TableMapping("task_assignments", None)
    )
    projects: TableMapping = field(
        default_factory=lambda: TableMapping("projects", None)
    )
    companies: TableMapping = field(
        default_factory=lambda: TableMapping("companies", None)
    )
    leaves: TableMapping = field(
        default_factory=lambda: TableMapping("leaves", None)
    )
    attendance: TableMapping = field(
        default_factory=lambda: TableMapping("attendance", None)
    )

    # Optional concepts: when absent the agent degrades gracefully (documented per repository method).
    work_schedule: TableMapping = field(
        default_factory=lambda: TableMapping("work_schedule", None)
    )
    holidays: TableMapping = field(
        default_factory=lambda: TableMapping("holidays", None)
    )
    project_members: TableMapping = field(
        default_factory=lambda: TableMapping("project_members", None)
    )
    task_required_skills: TableMapping = field(
        default_factory=lambda: TableMapping("task_required_skills", None)
    )
    sprints: TableMapping = field(
        default_factory=lambda: TableMapping("sprints", None)
    )
    task_outcomes: TableMapping = field(
        default_factory=lambda: TableMapping("task_outcomes", None)
    )
    timesheets: TableMapping = field(
        default_factory=lambda: TableMapping("timesheets", None)
    )

    # Discovery metadata
    source_db_url_masked: str = ""
    discovered_at: str = ""
    discovery_version: str = "1.0"
    manually_reviewed: bool = False
    notes: str = ""

    # ---------------------------------------------------------------------------
    def available_concepts(self) -> list[str]:
        """Domain concepts for which a real table was found."""
        return [
            concept
            for concept in CONCEPTS
            if getattr(self, concept).actual_table is not None
        ]

    def is_usable(self) -> bool:
        """Minimum: employees table must exist."""
        return self.employees.actual_table is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaMapping:
        m = cls()
        for concept in CONCEPTS:
            if concept in data and isinstance(data[concept], dict):
                raw = data[concept]
                cols = {
                    k: ColumnMap(**v) if isinstance(v, dict) else v
                    for k, v in raw.get("columns", {}).items()
                }
                raw_copy = {**raw, "columns": cols}
                setattr(m, concept, TableMapping(**raw_copy))
        for meta_key in (
            "source_db_url_masked", "discovered_at", "discovery_version",
            "manually_reviewed", "notes",
        ):
            if meta_key in data:
                setattr(m, meta_key, data[meta_key])
        return m


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_DEFAULT_MAPPING_FILE = Path("schema_mapping.json")


def save_mapping(mapping: SchemaMapping, path: Path | str | None = None) -> Path:
    p = Path(path) if path else _DEFAULT_MAPPING_FILE
    p.write_text(json.dumps(mapping.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("schema mapping saved to %s", p)
    return p


def load_mapping(path: Path | str | None = None) -> SchemaMapping | None:
    p = Path(path) if path else _DEFAULT_MAPPING_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        m = SchemaMapping.from_dict(data)
        logger.info("schema mapping loaded from %s", p)
        return m
    except Exception as exc:
        logger.warning("could not load schema mapping from %s: %s", p, exc)
        return None

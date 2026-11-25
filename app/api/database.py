from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, SecretStr
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.api.deps import ContainerDep
from app.infrastructure.adaptive import looks_like_native_schema, resolve
from app.infrastructure.discovery import (
    SchemaDiscovery,
    SchemaMapping,
    load_mapping,
    save_mapping,
)
from app.infrastructure.discovery import quote as quote_ident

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/database", tags=["database"])


class DatabaseConnectionRequest(BaseModel):
    database_url: str | None = None
    engine_type: str = "postgresql"  # postgresql, sqlite
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None


def normalize_database_url(req: DatabaseConnectionRequest) -> str:
    if req.database_url and req.database_url.strip():
        url_str = req.database_url.strip()
        # Ensure psycopg driver for postgresql
        if url_str.startswith("postgresql://"):
            url_str = "postgresql+psycopg://" + url_str[len("postgresql://") :]
        elif url_str.startswith("postgres://"):
            url_str = "postgresql+psycopg://" + url_str[len("postgres://") :]
        return url_str

    if req.engine_type == "sqlite":
        db = req.database or "sprintmatch.db"
        return f"sqlite:///{db}"

    # Build postgresql URL from parts
    user = req.username or "sprintmatch"
    pwd = req.password or ""
    host = req.host or "localhost"
    port = req.port or 55432
    db = req.database or "sprintmatch"
    pwd_part = f":{pwd}" if pwd else ""
    return f"postgresql+psycopg://{user}{pwd_part}@{host}:{port}/{db}"


def mask_url(url_str: str) -> str:
    try:
        u = make_url(url_str)
        return u.render_as_string(hide_password=True)
    except Exception:
        return re.sub(r":([^@]+)@", ":••••••••@", url_str)


def _uow_connection_and_bind(uow: Any) -> tuple[Any, Any, bool]:
    """Return (sqlalchemy_executor, engine_or_bind, is_read_only) for native or adaptive UoW."""
    session = getattr(uow, "session", None)
    if session is not None:
        return session, session.get_bind(), False
    conn = getattr(uow, "connection", None)
    if conn is not None:
        return conn, getattr(uow, "engine", conn), True
    return None, None, True


def _engine_kwargs_for_url(url_str: str, settings: Any) -> dict[str, Any]:
    if url_str.startswith("sqlite"):
        return {"echo": False, "pool_pre_ping": True}
    return {
        "echo": False,
        "pool_size": settings.database_pool_size,
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": settings.database_connect_timeout_seconds}
        if "postgresql" in url_str
        else {},
    }


def _adapter_summary(container: Any) -> dict[str, Any]:
    kind = getattr(container, "adapter_kind", "native")
    mapping = getattr(container, "schema_mapping", None)
    out: dict[str, Any] = {
        "kind": kind,
        "read_only": kind == "adaptive",
        "discovered_concepts": mapping.available_concepts() if mapping is not None else [],
        "concept_tables": {},
    }
    if mapping is not None:
        out["concept_tables"] = {
            c: {
                "table": tm.actual_table,
                "confidence": tm.confidence,
                "mapped_columns": sum(1 for cm in tm.columns.values() if cm.actual_column is not None),
            }
            for c in out["discovered_concepts"]
            if (tm := getattr(mapping, c)) is not None
        }
    return out


@router.get("/info")
def get_database_info(container: ContainerDep) -> dict[str, Any]:
    url_str = container.settings.database_url.get_secret_value()
    try:
        url_obj = make_url(url_str)
        dialect = url_obj.get_backend_name()
        host = url_obj.host or "local"
        port = url_obj.port or (5432 if dialect == "postgresql" else None)
        database = url_obj.database or "default"
        username = url_obj.username or "default"
    except Exception:
        dialect = "unknown"
        host = "localhost"
        port = 55432
        database = "sprintmatch"
        username = "sprintmatch"

    masked = mask_url(url_str)

    latency_ms = None
    connected = False
    stats: dict[str, Any] = {
        "employees_count": 0,
        "departments_count": 0,
        "skills_count": 0,
        "tasks_count": 0,
        "tables_count": 0,
        "tables": [],
    }

    t0 = time.perf_counter()
    try:
        with container.uow_factory() as uow:
            uow.ping()
            connected = True
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)

            executor, bind, _ = _uow_connection_and_bind(uow)
            mapping = getattr(container, "schema_mapping", None)
            if executor is not None and bind is not None:
                concept_targets: list[tuple[str, str | None]] = [
                    ("employees_count", mapping.employees.actual_table if mapping else "employees"),
                    ("departments_count", mapping.departments.actual_table if mapping else "departments"),
                    ("skills_count", mapping.skills.actual_table if mapping else "skills"),
                    ("tasks_count", mapping.tasks.actual_table if mapping else "tasks"),
                ]
                for key, table in concept_targets:
                    if not table:
                        continue
                    try:
                        stats[key] = (
                            executor.execute(text(f"SELECT COUNT(*) FROM {quote_ident(table)}")).scalar() or 0
                        )
                    except Exception as e:
                        logger.debug("Failed counting %s: %s", table, e)

                if stats["skills_count"] == 0:
                    try:
                        company = uow.companies.get_by_code(
                            container.settings.api_keys[0].company_code
                            if container.settings.api_keys
                            else "default"
                        )
                        if company is not None:
                            stats["skills_count"] = len(uow.tasks.get_skill_catalog(company.id))
                    except Exception:
                        pass

                try:
                    inspector = inspect(bind)
                    tables = inspector.get_table_names()
                    stats["tables_count"] = len(tables)
                    stats["tables"] = [t for t in tables if not t.startswith("alembic_")][:15]
                except Exception as e:
                    logger.debug("Failed inspecting tables: %s", e)
    except Exception as exc:
        connected = False
        logger.warning(f"Database ping failed: {exc}")

    return {
        "connected": connected,
        "dialect": dialect,
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "masked_url": masked,
        "latency_ms": latency_ms,
        "stats": stats,
        "adapter": _adapter_summary(container),
    }


@router.post("/test")
def test_database_connection(req: DatabaseConnectionRequest) -> dict[str, Any]:
    url_str = normalize_database_url(req)
    masked = mask_url(url_str)

    t0 = time.perf_counter()
    try:
        engine = create_engine(
            url_str,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5} if "postgresql" in url_str else {},
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            inspector = inspect(conn)
            tables = inspector.get_table_names()

        native_schema = looks_like_native_schema(engine)
        discovered = SchemaDiscovery(engine).discover(db_url_masked=masked)
        emp_table = discovered.employees.actual_table
        emp_count = None
        if emp_table and emp_table in tables:
            with engine.connect() as conn:
                emp_count = conn.execute(text(f"SELECT COUNT(*) FROM {quote_ident(emp_table)}")).scalar()
        engine.dispose()

        has_core = all(tbl in tables for tbl in ["employees", "skills", "departments"])
        schema_status = (
            "READY"
            if (has_core or discovered.is_usable())
            else ("EMPTY" if len(tables) == 0 else "PARTIAL")
        )

        db_name = make_url(url_str).database or "database"
        msg = f"Connection successful! Database '{db_name}' is reachable."
        if emp_count is not None and emp_table:
            msg += f" Detected {emp_count} employee record(s) in '{emp_table}'."

        concept_tables = {
            c: {
                "table": tm.actual_table,
                "confidence": tm.confidence,
            }
            for c in discovered.available_concepts()
            if (tm := getattr(discovered, c)) is not None
        }

        return {
            "ok": True,
            "latency_ms": latency_ms,
            "message": msg,
            "masked_url": masked,
            "database": db_name,
            "tables_count": len(tables),
            "tables_found": tables[:15],
            "schema_status": schema_status,
            "adapter_preview": {
                "kind": "native" if native_schema else "adaptive",
                "usable": native_schema or discovered.is_usable(),
                "employees_table": emp_table,
                "employees_count": emp_count,
                "discovered_concepts": discovered.available_concepts(),
                "concept_tables": concept_tables,
            },
        }
    except Exception as exc:
        err_msg = str(exc)
        if "Connection refused" in err_msg:
            err_msg = (
                "Connection refused: Host is unreachable or database server is not running on this port."
            )
        elif "password authentication failed" in err_msg:
            err_msg = "Authentication failed: Invalid username or password for database."
        elif "does not exist" in err_msg:
            err_msg = "Database not found: Specified database does not exist on target server."
        return {
            "ok": False,
            "latency_ms": None,
            "message": f"Connection test failed: {err_msg}",
            "masked_url": masked,
        }


@router.post("/connect")
def connect_database(req: DatabaseConnectionRequest, container: ContainerDep) -> dict[str, Any]:
    url_str = normalize_database_url(req)
    masked = mask_url(url_str)

    test_res = test_database_connection(req)
    if not test_res.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=test_res.get("message", "Database connection test failed"),
        )

    try:
        new_engine = create_engine(url_str, **_engine_kwargs_for_url(url_str, container.settings))
        resolved = resolve(
            new_engine,
            container.settings,
            db_url_masked=masked,
            internal_factory=container.internal_uow_factory,
            llm=container.llm,
        )
    except Exception as exc:
        logger.error(f"Failed to prepare database '{masked}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not adapt to this database: {exc}",
        ) from exc

    container.uow_factory = resolved.factory
    container.adapter_kind = resolved.kind
    container.schema_mapping = resolved.mapping
    container.settings.database_url = SecretStr(url_str)
    _persist_database_url_to_env(url_str)
    logger.info(f"Database switched to {masked} ({resolved.kind})")

    return {
        "ok": True,
        "message": (
            f"Connected to database '{test_res.get('database')}'. "
            + (
                "The agent automatically adapted to this database's schema and is ready."
                if resolved.kind == "adaptive"
                else "The database uses the native schema."
            )
        ),
        "masked_url": masked,
        "database": test_res.get("database"),
        "latency_ms": test_res.get("latency_ms"),
        "tables_count": test_res.get("tables_count"),
        "schema_status": test_res.get("schema_status"),
        "adapter": resolved.describe(),
    }


@router.get("/schema-mapping")
def get_schema_mapping(container: ContainerDep) -> dict[str, Any]:
    """The mapping the agent uses to read the connected database (editable, then PUT it back)."""
    path = Path(container.settings.schema_mapping_path)
    mapping = load_mapping(path)
    if mapping is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No schema mapping yet. Connect a database first.",
        )
    return {"mapping_file": str(path), "mapping": mapping.to_dict()}


@router.put("/schema-mapping")
def update_schema_mapping(body: dict[str, Any], container: ContainerDep) -> dict[str, Any]:
    """Apply a corrected mapping. Reconnect afterwards so the running container picks it up."""
    try:
        mapping = SchemaMapping.from_dict(body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid schema mapping: {exc}",
        ) from exc
    if not mapping.is_usable():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The mapping must resolve an employees table with an id column",
        )
    path = save_mapping(mapping, Path(container.settings.schema_mapping_path))
    return {"ok": True, "mapping_file": str(path), "saved": True}


@router.post("/remap")
def rediscover(container: ContainerDep) -> dict[str, Any]:
    """Forget the saved mapping and discover the connected database again."""
    path = Path(container.settings.schema_mapping_path)
    if path.exists():
        path.unlink()
    url_str = container.settings.database_url.get_secret_value()
    masked = mask_url(url_str)
    engine = create_engine(url_str, **_engine_kwargs_for_url(url_str, container.settings))
    try:
        resolved = resolve(
            engine,
            container.settings,
            db_url_masked=masked,
            internal_factory=container.internal_uow_factory,
            force="adaptive",
            llm=container.llm,
        )
    except Exception as exc:
        engine.dispose()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Schema re-discovery failed: {exc}",
        ) from exc
    container.uow_factory = resolved.factory
    container.adapter_kind = resolved.kind
    container.schema_mapping = resolved.mapping
    assert resolved.mapping is not None
    return {
        "ok": True,
        "mapping_file": str(path),
        "mapping": resolved.mapping.to_dict(),
        "adapter": resolved.describe(),
    }


def _persist_database_url_to_env(new_url: str) -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("DATABASE_URL="):
                new_lines.append(f"DATABASE_URL={new_url}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"DATABASE_URL={new_url}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not persist new DATABASE_URL to .env: {e}")


def _get_stem(w: str) -> str:
    w = w.lower().strip()
    if len(w) <= 3:
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith("ees"):
        return w[:-1]
    if w.endswith("sses"):
        return w[:-2]
    if any(w.endswith(x) for x in ["xes", "shes", "ches"]):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and not w.endswith("us") and not w.endswith("is"):
        return w[:-1]
    return w


def _format_stem_label(stem: str) -> str:
    label_map = {
        "employee": "Workforce & Employees",
        "task": "Tasks & Workflow",
        "project": "Projects & Sprints",
        "recommendation": "Recommendations & AI",
        "agent": "Agent & Intelligence",
        "performance": "Performance & Metrics",
        "audit": "Audit & History",
        "res": "Partners & Master Data",
        "sale": "Sales & Invoices",
        "purchase": "Purchasing",
        "stock": "Inventory & Stock",
        "product": "Products & Catalog",
        "order": "Orders & Fulfillment",
        "customer": "Customers & CRM",
        "user": "Users & Auth",
        "auth": "Security & Auth",
        "hr": "Human Resources",
        "account": "Accounting & Finance",
        "inv": "Invoices & Billing",
        "pos": "Point of Sale",
        "mrp": "Manufacturing",
        "mail": "Messages & Communication",
        "ir": "System & Metadata",
        "wp": "WordPress Core",
        "post": "Posts & Content",
        "comment": "Comments & Feedback",
        "term": "Taxonomies & Tags",
    }
    if stem in label_map:
        return label_map[stem]
    clean = stem.replace("_", " ").strip().title()
    if not clean.endswith("s") and not clean.endswith("data") and not clean.endswith("ware"):
        clean += "s"
    return clean


def categorize_database_tables(
    inspector: Any, table_names: list[str]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    tables = [
        t
        for t in sorted(table_names)
        if not t.startswith("alembic_")
        and not t.startswith("pg_")
        and not t.startswith("sqlite_")
        and not t.startswith("information_schema")
        and t not in ("spatial_ref_sys",)
    ]
    if not tables:
        return {}, []

    cleaned_names: dict[str, str] = {}
    tokens_map: dict[str, list[str]] = {}
    for t in tables:
        cleaned_names[t] = t
        tokens_map[t] = t.split("_")

    # Detect if >= 75% of tables share the first token (e.g. 'wp' in WordPress)
    first_tokens = [tokens_map[t][0].lower() for t in tables if len(tokens_map[t]) > 1]
    if len(first_tokens) >= 3 and len(tables) >= 4:
        most_common, count = Counter(first_tokens).most_common(1)[0]
        if count / len(tables) >= 0.75:
            for t in tables:
                if t.lower().startswith(f"{most_common}_"):
                    cleaned_names[t] = t[len(most_common) + 1 :]
                    tokens_map[t] = cleaned_names[t].split("_")

    fk_graph: dict[str, list[str]] = {}
    for t in tables:
        try:
            fks = inspector.get_foreign_keys(t)
            fk_graph[t] = [f["referred_table"] for f in fks if f.get("referred_table") in tables]
        except Exception:
            fk_graph[t] = []

    prefix_map: dict[str, list[str]] = defaultdict(list)
    for t in tables:
        head = tokens_map[t][0]
        stem = _get_stem(head)
        prefix_map[stem].append(t)

    # Check compound suffix without underscore (e.g. postmeta, usermeta, termmeta)
    all_stems = list(prefix_map.keys())
    for stem in all_stems:
        if len(stem) >= 3:
            for t in list(tables):
                cname = cleaned_names[t].lower()
                if (
                    cname != stem
                    and cname.startswith(stem)
                    and any(sfx in cname for sfx in ["meta", "detail", "line", "rel", "hist"])
                ):
                    old_stem = _get_stem(tokens_map[t][0])
                    if old_stem != stem and t in prefix_map[old_stem]:
                        prefix_map[old_stem].remove(t)
                        prefix_map[stem].append(t)

    table_categories: dict[str, str] = {}
    cluster_tables: dict[str, list[str]] = defaultdict(list)

    # Group stems with >= 2 tables
    for stem, tbls in prefix_map.items():
        if len(tbls) >= 2:
            label = _format_stem_label(stem)
            for t in tbls:
                table_categories[t] = label
                cluster_tables[label].append(t)

    # Unassigned tables - attach via FK
    unassigned = [t for t in tables if t not in table_categories]
    for t in list(unassigned):
        targets = fk_graph.get(t, [])
        for target in targets:
            if target in table_categories:
                label = table_categories[target]
                table_categories[t] = label
                cluster_tables[label].append(t)
                unassigned.remove(t)
                break

    # Domain semantic fallback for remaining tables
    for t in list(unassigned):
        t_low = t.lower()
        if any(k in t_low for k in ["audit", "log", "event", "history", "calendar", "holiday"]):
            label = "Audit & System Events"
        elif any(k in t_low for k in ["company", "companies", "department", "team", "skill", "organization"]):
            label = "Organization & Structure"
        elif any(k in t_low for k in ["user", "account", "profile", "role", "permission", "auth"]):
            label = "Users & Security"
        elif any(k in t_low for k in ["setting", "config", "option", "preference", "meta"]):
            label = "Settings & Configuration"
        else:
            label = "General & Master Data"

        table_categories[t] = label
        cluster_tables[label].append(t)
        unassigned.remove(t)

    categories_summary: list[dict[str, Any]] = []
    for cat_name, tbls in cluster_tables.items():
        categories_summary.append(
            {
                "name": cat_name,
                "count": len(tbls),
            }
        )
    categories_summary.sort(key=lambda c: (-int(c["count"]), str(c["name"])))

    return table_categories, categories_summary


@router.get("/tables")
def list_database_tables(container: ContainerDep) -> dict[str, Any]:
    with container.uow_factory() as uow:
        executor, bind, read_only = _uow_connection_and_bind(uow)
        if executor is None or bind is None:
            return {
                "database": "unknown",
                "total_tables": 0,
                "categories": [],
                "tables": [],
                "read_only": True,
                "adapter": _adapter_summary(container),
            }
        inspector = inspect(bind)
        table_names = inspector.get_table_names()

        cat_map, categories_summary = categorize_database_tables(inspector, table_names)
        mapping = getattr(container, "schema_mapping", None)
        table_to_concept: dict[str, str] = {}
        if mapping is not None:
            for concept in mapping.available_concepts():
                tm = getattr(mapping, concept)
                if tm and tm.actual_table:
                    table_to_concept[tm.actual_table] = concept

        tables_data = []
        for name in sorted(cat_map.keys()):
            try:
                row_count = executor.execute(text(f"SELECT COUNT(*) FROM {quote_ident(name)}")).scalar() or 0
            except Exception:
                row_count = 0

            try:
                cols = inspector.get_columns(name)
                cols_count = len(cols)
            except Exception:
                cols_count = 0

            tables_data.append(
                {
                    "table_name": name,
                    "row_count": row_count,
                    "columns_count": cols_count,
                    "category": cat_map.get(name, "General & Master Data"),
                    "mapped_concept": table_to_concept.get(name),
                }
            )

        db_name = "database"
        try:
            db_name = make_url(container.settings.database_url.get_secret_value()).database or "database"
        except Exception:
            pass

        return {
            "database": db_name,
            "total_tables": len(tables_data),
            "categories": categories_summary,
            "tables": tables_data,
            "read_only": read_only,
            "adapter": _adapter_summary(container),
        }


@router.get("/tables/{table_name}")
def get_table_details(
    table_name: str,
    container: ContainerDep,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
) -> dict[str, Any]:
    with container.uow_factory() as uow:
        executor, bind, read_only = _uow_connection_and_bind(uow)
        if executor is None or bind is None:
            raise HTTPException(status_code=500, detail="Database connection unavailable.")
        inspector = inspect(bind)

        if table_name not in inspector.get_table_names():
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found in active database.")

        raw_columns = inspector.get_columns(table_name)
        pk_constraint = inspector.get_pk_constraint(table_name)
        pk_cols = set(pk_constraint.get("constrained_columns", []))

        fk_constraints = inspector.get_foreign_keys(table_name)
        fk_map = {}
        for fk in fk_constraints:
            for local_col, remote_col in zip(
                fk.get("constrained_columns", []), fk.get("referred_columns", [])
            ):
                fk_map[local_col] = f"{fk.get('referred_table')}.{remote_col}"

        columns = []
        for c in raw_columns:
            columns.append(
                {
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": c.get("nullable", True),
                    "primary_key": c["name"] in pk_cols,
                    "foreign_key": fk_map.get(c["name"]),
                    "default": str(c.get("default", "")) if c.get("default") is not None else None,
                }
            )

        quoted_table = quote_ident(table_name)
        try:
            total_rows = executor.execute(text(f"SELECT COUNT(*) FROM {quoted_table}")).scalar() or 0
        except Exception:
            total_rows = 0

        query_sql = f"SELECT * FROM {quoted_table}"
        params: dict[str, Any] = {"limit": min(limit, 100), "offset": max(offset, 0)}

        if search and search.strip():
            text_cols = [
                c["name"] for c in columns if any(t in c["type"].lower() for t in ["char", "text", "str"])
            ]
            if text_cols:
                clauses = [f"LOWER(CAST({quote_ident(col)} AS TEXT)) LIKE LOWER(:search)" for col in text_cols[:5]]
                query_sql += f" WHERE {' OR '.join(clauses)}"
                params["search"] = f"%{search.strip()}%"

        query_sql += " LIMIT :limit OFFSET :offset"

        try:
            result = executor.execute(text(query_sql), params)
            rows = [dict(r._mapping) for r in result]
            clean_rows = [_serialize_row(r) for r in rows]
        except Exception as e:
            logger.warning(f"Error querying table {table_name}: {e}")
            clean_rows = []

        return {
            "table_name": table_name,
            "total_rows": total_rows,
            "limit": limit,
            "offset": offset,
            "read_only": read_only,
            "columns": columns,
            "columns_order": [c["name"] for c in columns],
            "rows": clean_rows,
        }


class UpdateRowRequest(BaseModel):
    pk_column: str
    pk_value: Any
    updates: dict[str, Any]


class InsertRowRequest(BaseModel):
    row: dict[str, Any]


class DeleteRowRequest(BaseModel):
    pk_column: str
    pk_value: Any


class ExecuteSqlRequest(BaseModel):
    sql: str
    params: dict[str, Any] | None = None


def _serialize_row(r: dict[str, Any]) -> dict[str, Any]:
    clean_r: dict[str, Any] = {}
    for k, v in r.items():
        if isinstance(v, (datetime, date)):
            clean_r[k] = v.isoformat()
        elif isinstance(v, UUID):
            clean_r[k] = str(v)
        elif isinstance(v, Decimal):
            clean_r[k] = float(v)
        elif isinstance(v, bytes):
            clean_r[k] = v.hex()
        else:
            clean_r[k] = v
    return clean_r


@router.put("/tables/{table_name}/rows")
def update_table_row(
    table_name: str,
    req: UpdateRowRequest,
    container: ContainerDep,
) -> dict[str, Any]:
    """Update a specific record in any database table directly."""
    with container.uow_factory() as uow:
        session = getattr(uow, "session", None)
        if not session:
            raise HTTPException(status_code=500, detail="Database session unavailable.")
        bind = session.get_bind()
        inspector = inspect(bind)

        if table_name not in inspector.get_table_names():
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found.")

        valid_cols = {c["name"] for c in inspector.get_columns(table_name)}
        if req.pk_column not in valid_cols:
            raise HTTPException(status_code=400, detail=f"Primary key column '{req.pk_column}' not found in table.")

        if not req.updates:
            raise HTTPException(status_code=400, detail="No column updates provided.")

        set_clauses: list[str] = []
        sql_params: dict[str, Any] = {"pk_val": req.pk_value}

        for i, (col, val) in enumerate(req.updates.items()):
            if col not in valid_cols:
                raise HTTPException(status_code=400, detail=f"Column '{col}' does not exist in table '{table_name}'.")
            if col == req.pk_column:
                continue  # skip mutating the primary key
            param_key = f"val_{i}"
            set_clauses.append(f'"{col}" = :{param_key}')
            sql_params[param_key] = val

        if not set_clauses:
            return {"ok": True, "message": "No fields to update.", "rows_affected": 0}

        update_sql = f'UPDATE "{table_name}" SET {", ".join(set_clauses)} WHERE "{req.pk_column}" = :pk_val'
        try:
            res = session.execute(text(update_sql), sql_params)
            session.commit()
            rows_affected = res.rowcount
        except Exception as exc:
            session.rollback()
            logger.error("Failed to update row in %s: %s", table_name, exc)
            raise HTTPException(status_code=400, detail=f"Database update failed: {exc}") from exc

        return {
            "ok": True,
            "message": f"Successfully updated {rows_affected} record(s) in '{table_name}'.",
            "rows_affected": rows_affected,
        }


@router.post("/tables/{table_name}/rows")
def insert_table_row(
    table_name: str,
    req: InsertRowRequest,
    container: ContainerDep,
) -> dict[str, Any]:
    """Insert a new record into any database table directly."""
    with container.uow_factory() as uow:
        session = getattr(uow, "session", None)
        if not session:
            raise HTTPException(status_code=500, detail="Database session unavailable.")
        bind = session.get_bind()
        inspector = inspect(bind)

        if table_name not in inspector.get_table_names():
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found.")

        valid_cols = {c["name"] for c in inspector.get_columns(table_name)}
        filtered_row = {k: v for k, v in req.row.items() if k in valid_cols and v is not None}

        if not filtered_row:
            raise HTTPException(status_code=400, detail="No valid column values provided for insertion.")

        col_names = list(filtered_row.keys())
        col_placeholders = [f":val_{i}" for i in range(len(col_names))]
        sql_params = {f"val_{i}": filtered_row[col] for i, col in enumerate(col_names)}

        cols_str = ", ".join(f'"{c}"' for c in col_names)
        vals_str = ", ".join(col_placeholders)
        insert_sql = f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({vals_str})'

        try:
            res = session.execute(text(insert_sql), sql_params)
            session.commit()
            rows_affected = res.rowcount
        except Exception as exc:
            session.rollback()
            logger.error("Failed to insert row in %s: %s", table_name, exc)
            raise HTTPException(status_code=400, detail=f"Database insert failed: {exc}") from exc

        return {
            "ok": True,
            "message": f"Successfully inserted new record into '{table_name}'.",
            "rows_affected": rows_affected,
        }


@router.delete("/tables/{table_name}/rows")
def delete_table_row(
    table_name: str,
    req: DeleteRowRequest,
    container: ContainerDep,
) -> dict[str, Any]:
    """Delete a record from any database table by primary key."""
    with container.uow_factory() as uow:
        session = getattr(uow, "session", None)
        if not session:
            raise HTTPException(status_code=500, detail="Database session unavailable.")
        bind = session.get_bind()
        inspector = inspect(bind)

        if table_name not in inspector.get_table_names():
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found.")

        valid_cols = {c["name"] for c in inspector.get_columns(table_name)}
        if req.pk_column not in valid_cols:
            raise HTTPException(status_code=400, detail=f"Primary key column '{req.pk_column}' not found.")

        delete_sql = f'DELETE FROM "{table_name}" WHERE "{req.pk_column}" = :pk_val'
        try:
            res = session.execute(text(delete_sql), {"pk_val": req.pk_value})
            session.commit()
            rows_affected = res.rowcount
        except Exception as exc:
            session.rollback()
            logger.error("Failed to delete row in %s: %s", table_name, exc)
            raise HTTPException(status_code=400, detail=f"Database delete failed: {exc}") from exc

        return {
            "ok": True,
            "message": f"Successfully deleted {rows_affected} record(s) from '{table_name}'.",
            "rows_affected": rows_affected,
        }


@router.post("/execute-sql")
def execute_sql_query(
    req: ExecuteSqlRequest,
    container: ContainerDep,
) -> dict[str, Any]:
    """Execute SQL statements against native DB, or read-only SELECT queries against adaptive sources."""
    sql = req.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL statement cannot be empty.")

    t0 = time.perf_counter()
    with container.uow_factory() as uow:
        executor, _, read_only = _uow_connection_and_bind(uow)
        if executor is None:
            raise HTTPException(status_code=500, detail="Database session unavailable.")

        if read_only:
            first_kw = sql.lstrip("(").split(None, 1)[0].upper() if sql.strip() else ""
            if first_kw not in ("SELECT", "WITH", "PRAGMA", "EXPLAIN", "SHOW"):
                raise HTTPException(
                    status_code=403,
                    detail="Connected external database is read-only: only SELECT queries are permitted.",
                )

        try:
            res = executor.execute(text(sql), req.params or {})
            is_select = res.returns_rows
            clean_rows: list[dict[str, Any]] = []
            columns: list[str] = []
            rows_affected = res.rowcount

            if is_select:
                columns = list(res.keys())
                for r in res.fetchmany(100):
                    clean_rows.append(_serialize_row(dict(r._mapping)))
            elif not read_only:
                executor.commit()

            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            return {
                "ok": True,
                "is_select": is_select,
                "columns": columns,
                "rows": clean_rows,
                "rows_affected": rows_affected,
                "latency_ms": latency_ms,
                "message": (
                    f"Returned {len(clean_rows)} row(s)"
                    if is_select
                    else f"Executed successfully ({rows_affected} row(s) affected)"
                ),
            }
        except Exception as exc:
            if not read_only and hasattr(executor, "rollback"):
                executor.rollback()
            logger.warning("SQL execution failed: %s", exc)
            raise HTTPException(status_code=400, detail=f"SQL Error: {exc}") from exc

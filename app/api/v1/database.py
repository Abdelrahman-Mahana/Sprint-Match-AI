from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, SecretStr
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.api.deps import ContainerDep
from app.infrastructure.schema_discovery.discovery import SchemaDiscovery
from app.infrastructure.schema_discovery.mapping import (
    SchemaMapping,
    load_mapping,
    save_mapping,
)
from app.infrastructure.schema_discovery.resolver import resolve

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
            url_str = "postgresql+psycopg://" + url_str[len("postgresql://"):]
        elif url_str.startswith("postgres://"):
            url_str = "postgresql+psycopg://" + url_str[len("postgres://"):]
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
    stats = {
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

            if hasattr(uow, "session"):
                session = uow.session
                try:
                    stats["employees_count"] = session.execute(text("SELECT COUNT(*) FROM employees")).scalar() or 0
                except Exception:
                    pass
                try:
                    stats["departments_count"] = session.execute(text("SELECT COUNT(*) FROM departments")).scalar() or 0
                except Exception:
                    pass
                try:
                    stats["skills_count"] = session.execute(text("SELECT COUNT(*) FROM skills")).scalar() or 0
                except Exception:
                    pass
                try:
                    stats["tasks_count"] = session.execute(text("SELECT COUNT(*) FROM tasks")).scalar() or 0
                except Exception:
                    pass
                try:
                    bind = session.get_bind()
                    inspector = inspect(bind)
                    tables = inspector.get_table_names()
                    stats["tables_count"] = len(tables)
                    stats["tables"] = [t for t in tables if not t.startswith("alembic_")][:15]
                except Exception:
                    pass
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

            emp_count = None
            if "employees" in tables:
                emp_count = conn.execute(text("SELECT COUNT(*) FROM employees")).scalar()

        engine.dispose()

        has_core = all(tbl in tables for tbl in ["employees", "skills", "departments"])
        schema_status = "READY" if has_core else ("EMPTY" if len(tables) == 0 else "PARTIAL")

        db_name = make_url(url_str).database or "database"
        msg = f"Connection successful! Database '{db_name}' is reachable."
        if emp_count is not None:
            msg += f" Found {emp_count} employees in database."

        return {
            "ok": True,
            "latency_ms": latency_ms,
            "message": msg,
            "masked_url": masked,
            "database": db_name,
            "tables_count": len(tables),
            "tables_found": tables[:10],
            "schema_status": schema_status,
        }
    except Exception as exc:
        err_msg = str(exc)
        if "Connection refused" in err_msg:
            err_msg = "Connection refused: Host is unreachable or database server is not running on this port."
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

    # 1. First test connection to make sure it's valid
    test_res = test_database_connection(req)
    if not test_res.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=test_res.get("message", "Database connection test failed"),
        )

    # 2. Build the engine, then decide how this database is read: native schema or discovered one.
    try:
        new_engine = create_engine(
            url_str,
            echo=False,
            pool_size=container.settings.database_pool_size,
            pool_pre_ping=True,
            connect_args={"connect_timeout": container.settings.database_connect_timeout_seconds} if "postgresql" in url_str else {},
        )
        resolved = resolve(
            new_engine,
            container.settings,
            db_url_masked=masked,
            internal_factory=container.internal_uow_factory,
        )
    except Exception as exc:
        logger.error(f"Failed to prepare database '{masked}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not adapt to this database: {exc}",
        ) from exc

    # 3. Hot-swap the data source in the running container.
    container.uow_factory = resolved.factory
    container.settings.database_url = SecretStr(url_str)
    _persist_database_url_to_env(url_str)
    logger.info(f"Database switched to {masked} ({resolved.kind})")

    return {
        "ok": True,
        "message": (
            f"Connected to database '{test_res.get('database')}'. "
            + (
                "The agent discovered this system's schema and is ready to search it."
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
    engine = create_engine(
        container.settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": container.settings.database_connect_timeout_seconds}
        if "postgresql" in container.settings.database_url.get_secret_value()
        else {},
    )
    try:
        mapping = SchemaDiscovery(engine).discover(
            db_url_masked=mask_url(container.settings.database_url.get_secret_value())
        )
    finally:
        engine.dispose()
    if not mapping.is_usable():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Discovery could not identify an employees table; edit the mapping by hand.",
        )
    save_mapping(mapping, path)
    return {"ok": True, "mapping_file": str(path), "mapping": mapping.to_dict()}


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
    if w.endswith('ies'): return w[:-3] + 'y'
    if w.endswith('ees'): return w[:-1]
    if w.endswith('sses'): return w[:-2]
    if any(w.endswith(x) for x in ['xes', 'shes', 'ches']): return w[:-2]
    if w.endswith('s') and not w.endswith('ss') and not w.endswith('us') and not w.endswith('is'): return w[:-1]
    return w


def _format_stem_label(stem: str) -> str:
    label_map = {
        'employee': 'Workforce & Employees',
        'task': 'Tasks & Workflow',
        'project': 'Projects & Sprints',
        'recommendation': 'Recommendations & AI',
        'agent': 'Agent & Intelligence',
        'performance': 'Performance & Metrics',
        'audit': 'Audit & History',
        'res': 'Partners & Master Data',
        'sale': 'Sales & Invoices',
        'purchase': 'Purchasing',
        'stock': 'Inventory & Stock',
        'product': 'Products & Catalog',
        'order': 'Orders & Fulfillment',
        'customer': 'Customers & CRM',
        'user': 'Users & Auth',
        'auth': 'Security & Auth',
        'hr': 'Human Resources',
        'account': 'Accounting & Finance',
        'inv': 'Invoices & Billing',
        'pos': 'Point of Sale',
        'mrp': 'Manufacturing',
        'mail': 'Messages & Communication',
        'ir': 'System & Metadata',
        'wp': 'WordPress Core',
        'post': 'Posts & Content',
        'comment': 'Comments & Feedback',
        'term': 'Taxonomies & Tags',
    }
    if stem in label_map:
        return label_map[stem]
    clean = stem.replace('_', ' ').strip().title()
    if not clean.endswith('s') and not clean.endswith('data') and not clean.endswith('ware'):
        clean += 's'
    return clean


def categorize_database_tables(inspector: Any, table_names: list[str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    tables = [
        t for t in sorted(table_names)
        if not t.startswith('alembic_')
        and not t.startswith('pg_')
        and not t.startswith('sqlite_')
        and not t.startswith('information_schema')
        and t not in ('spatial_ref_sys',)
    ]
    if not tables:
        return {}, []

    cleaned_names: dict[str, str] = {}
    tokens_map: dict[str, list[str]] = {}
    for t in tables:
        cleaned_names[t] = t
        tokens_map[t] = t.split('_')

    # Detect if >= 75% of tables share the first token (e.g. 'wp' in WordPress)
    first_tokens = [tokens_map[t][0].lower() for t in tables if len(tokens_map[t]) > 1]
    if len(first_tokens) >= 3 and len(tables) >= 4:
        most_common, count = Counter(first_tokens).most_common(1)[0]
        if count / len(tables) >= 0.75:
            for t in tables:
                if t.lower().startswith(f'{most_common}_'):
                    cleaned_names[t] = t[len(most_common) + 1:]
                    tokens_map[t] = cleaned_names[t].split('_')

    fk_graph: dict[str, list[str]] = {}
    for t in tables:
        try:
            fks = inspector.get_foreign_keys(t)
            fk_graph[t] = [f['referred_table'] for f in fks if f.get('referred_table') in tables]
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
                if cname != stem and cname.startswith(stem) and any(sfx in cname for sfx in ['meta', 'detail', 'line', 'rel', 'hist']):
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
        if any(k in t_low for k in ['audit', 'log', 'event', 'history', 'calendar', 'holiday']):
            label = 'Audit & System Events'
        elif any(k in t_low for k in ['company', 'companies', 'department', 'team', 'skill', 'organization']):
            label = 'Organization & Structure'
        elif any(k in t_low for k in ['user', 'account', 'profile', 'role', 'permission', 'auth']):
            label = 'Users & Security'
        elif any(k in t_low for k in ['setting', 'config', 'option', 'preference', 'meta']):
            label = 'Settings & Configuration'
        else:
            label = 'General & Master Data'

        table_categories[t] = label
        cluster_tables[label].append(t)
        unassigned.remove(t)

    categories_summary = []
    for cat_name, tbls in cluster_tables.items():
        categories_summary.append({
            'name': cat_name,
            'count': len(tbls),
        })
    categories_summary.sort(key=lambda c: (-c['count'], c['name']))

    return table_categories, categories_summary


@router.get("/tables")
def list_database_tables(container: ContainerDep) -> dict[str, Any]:
    with container.uow_factory() as uow:
        session = getattr(uow, "session", None)
        if not session:
            return {"database": "unknown", "total_tables": 0, "categories": [], "tables": []}
        bind = session.get_bind()
        inspector = inspect(bind)
        table_names = inspector.get_table_names()

        cat_map, categories_summary = categorize_database_tables(inspector, table_names)

        tables_data = []
        for name in sorted(cat_map.keys()):
            try:
                row_count = session.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0
            except Exception:
                row_count = 0

            try:
                cols = inspector.get_columns(name)
                cols_count = len(cols)
            except Exception:
                cols_count = 0

            tables_data.append({
                "table_name": name,
                "row_count": row_count,
                "columns_count": cols_count,
                "category": cat_map.get(name, "General & Master Data"),
            })

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
        session = getattr(uow, "session", None)
        if not session:
            raise HTTPException(status_code=500, detail="Database session unavailable.")
        bind = session.get_bind()
        inspector = inspect(bind)

        if table_name not in inspector.get_table_names():
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found in active database.")

        raw_columns = inspector.get_columns(table_name)
        pk_constraint = inspector.get_pk_constraint(table_name)
        pk_cols = set(pk_constraint.get("constrained_columns", []))

        fk_constraints = inspector.get_foreign_keys(table_name)
        fk_map = {}
        for fk in fk_constraints:
            for local_col, remote_col in zip(fk.get("constrained_columns", []), fk.get("referred_columns", [])):
                fk_map[local_col] = f"{fk.get('referred_table')}.{remote_col}"

        columns = []
        for c in raw_columns:
            columns.append({
                "name": c["name"],
                "type": str(c["type"]),
                "nullable": c.get("nullable", True),
                "primary_key": c["name"] in pk_cols,
                "foreign_key": fk_map.get(c["name"]),
                "default": str(c.get("default", "")) if c.get("default") is not None else None,
            })

        try:
            total_rows = session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0
        except Exception:
            total_rows = 0

        query_sql = f'SELECT * FROM "{table_name}"'
        params: dict[str, Any] = {"limit": min(limit, 100), "offset": max(offset, 0)}

        if search and search.strip():
            text_cols = [c["name"] for c in columns if any(t in c["type"].lower() for t in ["char", "text", "str"])]
            if text_cols:
                clauses = [f'CAST("{col}" AS TEXT) ILIKE :search' for col in text_cols[:5]]
                query_sql += f" WHERE {' OR '.join(clauses)}"
                params["search"] = f"%{search.strip()}%"

        query_sql += ' LIMIT :limit OFFSET :offset'

        try:
            result = session.execute(text(query_sql), params)
            rows = [dict(r._mapping) for r in result]
            clean_rows = []
            for r in rows:
                clean_r = {}
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
                clean_rows.append(clean_r)
        except Exception as e:
            logger.warning(f"Error querying table {table_name}: {e}")
            clean_rows = []

        return {
            "table_name": table_name,
            "total_rows": total_rows,
            "limit": limit,
            "offset": offset,
            "columns": columns,
            "columns_order": [c["name"] for c in columns],
            "rows": clean_rows,
        }

"""FastAPI application factory.

Run with:  uvicorn app.main:create_app --factory
(Windows + PostgreSQL checkpointer: add  --loop app.core.event_loop:selector_loop_factory)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy.engine import make_url

from app.agents.checkpointer import CheckpointStore
from app.agents.runtime import AgentRuntime
from app.api import health
from app.api.v1 import agent, database, employees, evaluation, recommendations, tasks, workforce
from app.application.container import AppContainer
from app.core.config import Settings, get_settings
from app.core.exceptions import SprintMatchError
from app.core.logging import configure_logging, register_secrets, request_id_var
from app.core.security import ApiKeyAuthenticator, RateLimiter
from app.infrastructure.database.session import create_db_engine, create_session_factory
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.infrastructure.llm.factory import build_llm_provider
from app.infrastructure.schema_discovery.resolver import resolve

logger = logging.getLogger(__name__)
_REQUEST_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def mask_database_url(url: str) -> str:
    """URL with the password hidden, safe for logs and API responses."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable database url>"


def _error(code: str, message: str, status: int, details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id_var.get(),
                "details": details or {},
            }
        },
    )


def create_app(settings: Settings | None = None, container: AppContainer | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    register_secrets(settings.secret_values())

    if container is None:
        engine = create_db_engine(settings)
        internal_factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        resolved = resolve(
            engine,
            settings,
            db_url_masked=mask_database_url(settings.database_url.get_secret_value()),
            internal_factory=internal_factory,
        )
        uow_factory = resolved.factory
        if resolved.kind == "native":
            internal_factory = uow_factory
        logger.info("data source ready", extra={"adapter": resolved.kind})
        container = AppContainer(
            settings,
            uow_factory,
            build_llm_provider(settings),
            internal_uow_factory=internal_factory,
        )

    if container.agent_runtime is None:
        # Checkpoints must land in the app's own store (never the external foreign source).
        checkpoint_url = (
            settings.internal_database_url.get_secret_value()
            if settings.internal_database_url
            else settings.database_url.get_secret_value()
        )
        container.agent_runtime = AgentRuntime(container, CheckpointStore(settings, checkpoint_url))
    runtime: AgentRuntime = container.agent_runtime

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "starting",
            extra={
                "environment": settings.environment,
                "llm_provider": settings.llm_provider,
                "checkpointer": settings.checkpointer,
            },
        )
        try:
            await runtime.checkpoints.async_saver()  # checkpoint tables ready before the first run
        except Exception as exc:  # the API still serves; runs report CHECKPOINT_UNAVAILABLE
            logger.error("checkpointer initialization failed", extra={"error": type(exc).__name__})
        yield
        await runtime.checkpoints.aclose()
        if container.llm is not None:
            container.llm.close()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Explainable employee-task recommendation for Scrum teams. Recommendations are advisory: "
            "the system never assigns tasks automatically. Authenticate with the `X-API-Key` header."
        ),
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.authenticator = ApiKeyAuthenticator(settings.api_keys)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if 0 < len(incoming) <= 64 and set(incoming) <= _REQUEST_ID_OK else uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(SprintMatchError)
    async def handle_domain_error(_: Request, exc: SprintMatchError) -> JSONResponse:
        if exc.http_status >= 500:
            logger.error("request failed", extra={"error_code": exc.code})
        return _error(exc.code, exc.message, exc.http_status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = {"errors": [{"loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]}
        return _error("INVALID_INPUT", "Request validation failed", 422, details)

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return _error("INTERNAL_ERROR", "An unexpected error occurred", 500)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    ui_dir = Path(__file__).resolve().parent / "ui"
    static_dir = Path(__file__).resolve().parent / "static"

    if ui_dir.exists():
        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def test_ui() -> HTMLResponse:
        ui_index = ui_dir / "index.html"
        if ui_index.exists():
            return HTMLResponse(content=ui_index.read_text(encoding="utf-8"))
        static_index = static_dir / "index.html"
        if static_index.exists():
            return HTMLResponse(content=static_index.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>SprintMatch AI</h1><p>UI not found</p>", status_code=404)

    app.include_router(health.router)
    app.include_router(health.v1_router, prefix="/api/v1")
    for router in (
        agent.router,
        database.router,
        employees.router,
        tasks.router,
        workforce.router,
        recommendations.router,
        evaluation.router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app

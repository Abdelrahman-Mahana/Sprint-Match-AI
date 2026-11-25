"""Core infrastructure: settings, Windows asyncio loop, exceptions, logging, and API auth."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import sys
import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain import ErrorCategory, Role

# ===========================================================================
# Configuration
# ===========================================================================

class ApiKeyConfig(BaseModel):
    """One API client. Only the SHA-256 hex digest of the key is configured, never the key itself."""

    name: str
    role: Role
    company_code: str
    key_sha256: str = Field(min_length=64, max_length=64)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SprintMatch AI"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    # Database
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://sprintmatch:sprintmatch@localhost:5432/sprintmatch"
    )
    database_echo: bool = False
    database_pool_size: int = 5
    database_connect_timeout_seconds: int = 5

    # Data-source adaptation
    # auto: use the native schema when the database has it, otherwise discover and adapt to it.
    database_adapter: Literal["auto", "native", "adaptive"] = "auto"
    schema_mapping_path: str = "schema_mapping.json"
    # Used when the connected source does not state a timezone / schedule of its own.
    adaptive_default_timezone: str = "UTC"
    adaptive_assume_default_schedule: bool = True
    adaptive_default_daily_hours: float = Field(default=8.0, gt=0, le=16)
    # Where the agent's own output (runs, audit, checkpoints) is stored. Defaults to database_url;
    # set it when database_url points at an external system that must never receive agent writes.
    internal_database_url: SecretStr | None = None

    # LLM provider (fully optional; the deterministic core never needs it)
    llm_provider: Literal["none", "mock", "openai_compatible", "ollama"] = "none"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_retry_wait_seconds: float = Field(default=20.0, ge=0, le=120)
    llm_structured_output_mode: Literal["json_schema", "json_object"] = "json_schema"
    explanation_mode: Literal["deterministic", "llm"] = "deterministic"

    # LangGraph agent
    # auto: native tool calling when the model supports it, else a validated JSON tool plan, else disabled.
    agent_tool_mode: Literal["auto", "native", "json_plan", "disabled"] = "auto"
    agent_max_tool_rounds: int = Field(default=5, ge=0, le=20)
    agent_max_tool_calls: int = Field(default=20, ge=0, le=100)
    agent_tool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    agent_max_tool_result_items: int = Field(default=25, ge=1, le=500)
    agent_max_tool_result_chars: int = Field(default=12_000, ge=500, le=200_000)
    agent_max_profile_expansions: int = Field(default=10, ge=1, le=100)
    agent_node_max_attempts: int = Field(default=2, ge=1, le=5)
    agent_retry_initial_interval_seconds: float = Field(default=0.5, ge=0, le=30)

    # LangGraph checkpoints (graph execution state; separate from recommendation history)
    checkpointer: Literal["postgres", "memory", "none"] = "postgres"
    checkpoint_schema: str = Field(default="agent_checkpoints", pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    checkpoint_retention_days: int = Field(default=30, ge=1, le=3650)

    # API security
    api_keys: list[ApiKeyConfig] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=30, ge=1)

    # Recommendation defaults
    default_horizon_working_days: int = Field(default=5, ge=1, le=60)
    accept_llm_effort_estimates: bool = False

    # Demo data
    demo_reference_date: date | None = None
    demo_seed: int = 20261103

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("llm_base_url", "llm_model", mode="before")
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def secret_values(self) -> list[str]:
        """Values that must be redacted wherever they might appear in logs."""
        secrets = [self.database_url.get_secret_value()]
        if self.llm_api_key is not None:
            secrets.append(self.llm_api_key.get_secret_value())
        return [s for s in secrets if s]


@lru_cache
def get_settings() -> Settings:
    return Settings()

# ===========================================================================
# Windows Event Loop
# ===========================================================================

def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Called by uvicorn for a custom ``--loop`` import string: must return a new event loop."""
    return asyncio.SelectorEventLoop()

# ===========================================================================
# Exceptions
# ===========================================================================



_CATEGORY_BY_CODE: dict[str, ErrorCategory] = {
    "INVALID_INPUT": ErrorCategory.VALIDATION_ERROR,
    "NOT_FOUND": ErrorCategory.VALIDATION_ERROR,
    "CONFLICT": ErrorCategory.VALIDATION_ERROR,
    "UNAUTHENTICATED": ErrorCategory.VALIDATION_ERROR,
    "FORBIDDEN": ErrorCategory.VALIDATION_ERROR,
    "RATE_LIMITED": ErrorCategory.VALIDATION_ERROR,
    "RUN_NOT_RESUMABLE": ErrorCategory.VALIDATION_ERROR,
    "DATABASE_UNAVAILABLE": ErrorCategory.DATABASE_ERROR,
    "DATA_INTEGRITY_ERROR": ErrorCategory.DATABASE_ERROR,
    "PERSISTENCE_FAILED": ErrorCategory.DATABASE_ERROR,
    "CHECKPOINT_UNAVAILABLE": ErrorCategory.DATABASE_ERROR,
    "PROVIDER_UNAVAILABLE": ErrorCategory.PROVIDER_UNAVAILABLE,
    "LLM_NOT_CONFIGURED": ErrorCategory.PROVIDER_UNAVAILABLE,
    "PROVIDER_TIMEOUT": ErrorCategory.TIMEOUT,
    "TIMEOUT": ErrorCategory.TIMEOUT,
    "TOOL_TIMEOUT": ErrorCategory.TIMEOUT,
    "INVALID_RESULT": ErrorCategory.GRAPH_EXECUTION_ERROR,
    "GRAPH_EXECUTION_ERROR": ErrorCategory.GRAPH_EXECUTION_ERROR,
    "GRAPH_RECURSION_LIMIT": ErrorCategory.GRAPH_EXECUTION_ERROR,
    "FINAL_VALIDATION_FAILED": ErrorCategory.GRAPH_EXECUTION_ERROR,
    "INCOMPLETE_DATA": ErrorCategory.INCOMPLETE_DATA,
    "NO_SUITABLE_CANDIDATE": ErrorCategory.NO_SUITABLE_CANDIDATE,
    "INTERNAL_ERROR": ErrorCategory.INTERNAL_ERROR,
}


def categorize(code: str) -> ErrorCategory:
    """Map a specific error code to its standardized category (prefix rules for open-ended families)."""
    if code in _CATEGORY_BY_CODE:
        return _CATEGORY_BY_CODE[code]
    if code.startswith(("LLM_", "PROVIDER_")):
        return ErrorCategory.LLM_PROVIDER_ERROR
    if code.startswith(("TOOL_", "UNKNOWN_TOOL", "INVALID_TOOL")):
        return ErrorCategory.TOOL_EXECUTION_ERROR
    return ErrorCategory.INTERNAL_ERROR


class SprintMatchError(Exception):
    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    @property
    def category(self) -> ErrorCategory:
        return categorize(self.code)


class NotFoundError(SprintMatchError):
    code = "NOT_FOUND"
    http_status = 404


class InputValidationError(SprintMatchError):
    code = "INVALID_INPUT"
    http_status = 422


class ConflictError(SprintMatchError):
    code = "CONFLICT"
    http_status = 409


class AuthenticationError(SprintMatchError):
    code = "UNAUTHENTICATED"
    http_status = 401


class AuthorizationError(SprintMatchError):
    code = "FORBIDDEN"
    http_status = 403


class RateLimitExceededError(SprintMatchError):
    code = "RATE_LIMITED"
    http_status = 429


class DataSourceUnavailableError(SprintMatchError):
    """The underlying data source (PostgreSQL today, Odoo later) could not be reached."""

    code = "DATABASE_UNAVAILABLE"
    http_status = 503
    retryable = True






class RunNotResumableError(SprintMatchError):
    """A retry was requested for an agent run that has no resumable checkpoint."""

    code = "RUN_NOT_RESUMABLE"
    http_status = 409


class IncompleteDataError(SprintMatchError):
    """The data needed to answer is missing (e.g. a task without usable requirements)."""

    code = "INCOMPLETE_DATA"
    http_status = 422

# ===========================================================================
# Logging & Secret Redaction
# ===========================================================================

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REDACTED = "***REDACTED***"
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?[^\s,;\"']+"), rf"\1{_REDACTED}"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), f"Bearer {_REDACTED}"),
    (
        re.compile(
            r"(?i)((?:x-)?api[_-]?key[\"']?\s*[:=]\s*[\"']?|(?:password|secret|token)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+"
        ),
        rf"\1{_REDACTED}",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), _REDACTED),
    # user:password@ inside connection URLs
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), rf"\1{_REDACTED}@"),
]

_extra_secrets: set[str] = set()


def register_secrets(values: list[str]) -> None:
    """Register literal secret values (API keys, DSNs) that must never be logged."""
    _extra_secrets.update(v for v in values if v and len(v) >= 4)


def redact(text: str) -> str:
    for secret in _extra_secrets:
        text = text.replace(secret, _REDACTED)
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    _RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_output else PlainFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Never let HTTP client debug logs leak request headers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

# ===========================================================================
# API Authentication & Rate Limiting
# ===========================================================================

@dataclass(frozen=True)
class Principal:
    name: str
    role: Role
    company_code: str

    def require(self, role: Role) -> None:
        if self.role.rank < role.rank:
            raise AuthorizationError(f"This action requires the {role.value} role")


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKeyAuthenticator:
    def __init__(self, keys: list[ApiKeyConfig]) -> None:
        self._keys = keys

    def authenticate(self, presented: str | None) -> Principal:
        if not presented:
            raise AuthenticationError("Missing X-API-Key header")
        digest = hash_api_key(presented)
        match = None
        for cfg in self._keys:  # compare against every key: constant work regardless of position
            if hmac.compare_digest(digest, cfg.key_sha256.lower()):
                match = cfg
        if match is None:
            raise AuthenticationError("Invalid API key")
        return Principal(name=match.name, role=match.role, company_code=match.company_code)


class RateLimiter:
    """Sliding one-minute window per principal. Suitable for a single process; use a shared store when scaling."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                raise RateLimitExceededError("Rate limit exceeded; retry later")
            hits.append(now)

"""Application exception hierarchy.

Each error carries a stable machine-readable ``code`` (specific) and a standardized ``category`` (coarse, used
by clients and dashboards): see ``ErrorCategory`` and ``categorize``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    GRAPH_EXECUTION_ERROR = "GRAPH_EXECUTION_ERROR"
    INCOMPLETE_DATA = "INCOMPLETE_DATA"
    NO_SUITABLE_CANDIDATE = "NO_SUITABLE_CANDIDATE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


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


class DataIntegrityError(SprintMatchError):
    """Source data violates a business invariant (e.g. a schedule ending before it starts)."""

    code = "DATA_INTEGRITY_ERROR"
    http_status = 500


class OperationTimeoutError(SprintMatchError):
    code = "TIMEOUT"
    http_status = 504
    retryable = True


class RunNotResumableError(SprintMatchError):
    """A retry was requested for an agent run that has no resumable checkpoint."""

    code = "RUN_NOT_RESUMABLE"
    http_status = 409


class IncompleteDataError(SprintMatchError):
    """The data needed to answer is missing (e.g. a task without usable requirements)."""

    code = "INCOMPLETE_DATA"
    http_status = 422

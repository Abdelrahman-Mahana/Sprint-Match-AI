"""Structured JSON logging with request correlation and secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

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

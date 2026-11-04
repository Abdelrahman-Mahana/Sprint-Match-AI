"""Shared HTTP plumbing for network-based adapters (error classification, no secret leakage)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.core.logging import redact
from app.infrastructure.llm.base import (
    LLMConfig,
    LLMNotConfiguredError,
    LLMProvider,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)


def _retry_after_seconds(value: str | None) -> float | None:
    """``Retry-After`` in seconds (HTTP-date values are ignored: providers send seconds for rate limits)."""
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


class HttpLLMProvider(LLMProvider):
    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(config, sleep=sleep)
        if not config.base_url:
            raise LLMNotConfiguredError(f"LLM_BASE_URL is required for provider {self.name}")
        if not config.model:
            raise LLMNotConfiguredError(f"LLM_MODEL is required for provider {self.name}")
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(config.timeout_seconds),
            headers=self._headers(),
            transport=transport,
        )

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._client.request(method, path.lstrip("/"), json=json_body)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"{self.name} request timed out") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailableError(f"{self.name} is unreachable: {type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            details: dict[str, Any] = {"status": response.status_code}
            retry_after = _retry_after_seconds(response.headers.get("retry-after"))
            if retry_after is not None:
                details["retry_after"] = retry_after
            raise LLMUnavailableError(f"{self.name} returned HTTP {response.status_code}", details=details)
        if response.status_code >= 400:
            raise LLMRequestError(
                f"{self.name} rejected the request with HTTP {response.status_code}",
                details={"status": response.status_code, "body": redact(response.text)[:300]},
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMUnavailableError(f"{self.name} returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise LLMUnavailableError(f"{self.name} returned an unexpected payload")
        return data

"""Provider-independent LLM interface.

Business logic depends only on ``LLMProvider``. Adapters implement ``_complete`` (text / structured output),
optionally ``_chat`` (native tool calling) and ``health_check``; retry, timeout classification, JSON extraction,
schema validation (with one bounded repair attempt), capability reporting and usage accounting live here.
"""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ValidationError

from app.core.exceptions import SprintMatchError
from app.core.logging import redact
from app.infrastructure.llm.usage import record_usage

logger = logging.getLogger(__name__)


class LLMError(SprintMatchError):
    code = "LLM_ERROR"
    http_status = 502


class LLMNotConfiguredError(LLMError):
    code = "LLM_NOT_CONFIGURED"
    http_status = 503


class LLMUnavailableError(LLMError):
    """Transient failure (network, timeout, 429, 5xx). Retried with bounded backoff."""

    code = "PROVIDER_UNAVAILABLE"
    http_status = 503
    retryable = True


class LLMTimeoutError(LLMUnavailableError):
    code = "PROVIDER_TIMEOUT"


class LLMRequestError(LLMError):
    """Non-retryable rejection by the provider (4xx other than 429)."""

    code = "PROVIDER_REQUEST_REJECTED"


class LLMInvalidOutputError(LLMError):
    code = "LLM_INVALID_OUTPUT"


class LLMToolCallingUnsupportedError(LLMError):
    code = "LLM_TOOL_CALLING_UNSUPPORTED"


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    timeout_seconds: float = 45.0
    max_retries: int = 2
    max_retry_wait_seconds: float = 20.0  # upper bound for a provider-requested Retry-After
    structured_output_mode: Literal["json_schema", "json_object"] = "json_schema"


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class LLMResult[T]:
    value: T
    provider: str
    model: str
    latency_ms: int
    attempts: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ToolCallData:
    """One tool call requested by a model. ``arguments`` is None when the model sent unparsable JSON."""

    id: str
    name: str
    arguments: dict[str, Any] | None
    raw_arguments: str = ""


@dataclass(frozen=True)
class ChatTurn:
    """One assistant turn of a tool-enabled chat (provider-neutral)."""

    content: str
    tool_calls: tuple[ToolCallData, ...]
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    """What the configured model can do. ``tool_calling`` is None until known (declared, probed or configured)."""

    tool_calling: bool | None
    structured_output: bool
    json_mode: bool
    streaming: bool
    source: str = "declared"  # declared | probed | configured
    detail: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    provider: str
    model: str
    latency_ms: int
    detail: str | None = None


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse a JSON object from model output, tolerating a surrounding markdown code fence."""
    stripped = text.strip()
    match = _FENCE.match(stripped)
    if match:
        stripped = match.group(1)
    if not stripped.startswith("{"):
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


class LLMProvider(ABC):
    name: ClassVar[str]
    supports_tool_calling: ClassVar[bool] = False
    repair_attempts: ClassVar[int] = 1

    def __init__(self, config: LLMConfig, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self.config.model

    # --- adapter contract -------------------------------------------------------------------------
    @abstractmethod
    def _complete(
        self, messages: list[ChatMessage], *, json_schema: dict[str, Any] | None, schema_name: str | None
    ) -> Completion: ...

    @abstractmethod
    def health_check(self) -> ProviderHealth: ...

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> ChatTurn:
        """Native tool-calling chat turn. ``messages`` use the OpenAI chat format (the neutral wire format)."""
        raise LLMToolCallingUnsupportedError(f"provider {self.name} has no native tool-calling adapter")

    def capabilities(self, *, probe: bool = False) -> ProviderCapabilities:
        """Declared capabilities; adapters refine them (``probe=True`` may perform a small live request)."""
        return ProviderCapabilities(
            tool_calling=self.supports_tool_calling,
            structured_output=True,
            json_mode=True,
            streaming=False,
        )

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release network resources."""

    # --- shared behaviour -------------------------------------------------------------------------
    def _with_retries[R](self, fn: Callable[[], R]) -> tuple[R, int]:
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn(), attempt
            except LLMUnavailableError as exc:
                if attempt > self.config.max_retries:
                    raise
                # Exponential backoff, or the provider's Retry-After (rate limits) when longer - bounded.
                requested = float(exc.details.get("retry_after") or 0.0)
                delay = max(0.5 * (2 ** (attempt - 1)), min(requested, self.config.max_retry_wait_seconds))
                logger.warning(
                    "llm transient failure, retrying",
                    extra={
                        "provider": self.name,
                        "attempt": attempt,
                        "error_code": exc.code,
                        "delay_s": delay,
                    },
                )
                self._sleep(delay)

    def generate_structured[T: BaseModel](
        self, messages: list[ChatMessage], schema: type[T], *, schema_name: str
    ) -> LLMResult[T]:
        started = time.perf_counter()
        json_schema = schema.model_json_schema()
        conversation = list(messages)
        total_attempts = 0
        last_error = ""
        for repair in range(self.repair_attempts + 1):
            completion, attempts = self._with_retries(
                partial(self._complete, list(conversation), json_schema=json_schema, schema_name=schema_name)
            )
            total_attempts += attempts
            try:
                value = schema.model_validate(extract_json(completion.text))
            except (ValueError, ValidationError) as exc:
                last_error = redact(str(exc))[:500]
                logger.warning(
                    "llm structured output rejected",
                    extra={"provider": self.name, "schema": schema_name, "repair_round": repair},
                )
                conversation = [
                    *conversation,
                    ChatMessage(role="assistant", content=completion.text[:4000]),
                    ChatMessage(
                        role="user",
                        content=(
                            "Your previous answer did not match the required JSON schema. "
                            f"Validation error: {last_error}. Reply with ONLY a corrected JSON object."
                        ),
                    ),
                ]
                continue
            latency = int((time.perf_counter() - started) * 1000)
            self._log_call(schema_name, completion, latency, total_attempts)
            return LLMResult(
                value=value,
                provider=self.name,
                model=completion.model,
                latency_ms=latency,
                attempts=total_attempts,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
            )
        raise LLMInvalidOutputError(
            "The LLM returned output that does not match the required schema",
            details={"schema": schema_name, "error": last_error},
        )

    def generate_text(self, messages: list[ChatMessage]) -> LLMResult[str]:
        started = time.perf_counter()
        completion, attempts = self._with_retries(
            partial(self._complete, messages, json_schema=None, schema_name=None)
        )
        latency = int((time.perf_counter() - started) * 1000)
        self._log_call("text", completion, latency, attempts)
        return LLMResult(
            value=completion.text.strip(),
            provider=self.name,
            model=completion.model,
            latency_ms=latency,
            attempts=attempts,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult[ChatTurn]:
        """One tool-enabled assistant turn with bounded retries (transient errors only)."""
        started = time.perf_counter()
        turn, attempts = self._with_retries(partial(self._chat, messages, tools))
        latency = int((time.perf_counter() - started) * 1000)
        self._log_call("tool_chat", turn, latency, attempts)
        return LLMResult(
            value=turn,
            provider=self.name,
            model=turn.model,
            latency_ms=latency,
            attempts=attempts,
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
        )

    def _log_call(
        self, purpose: str, completion: Completion | ChatTurn, latency_ms: int, attempts: int
    ) -> None:
        record_usage(completion.prompt_tokens, completion.completion_tokens)
        logger.info(
            "llm call completed",
            extra={
                "provider": self.name,
                "model": completion.model,
                "purpose": purpose,
                "latency_ms": latency_ms,
                "attempts": attempts,
                "prompt_tokens": completion.prompt_tokens,
                "completion_tokens": completion.completion_tokens,
            },
        )

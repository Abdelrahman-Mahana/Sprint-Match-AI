"""Provider-independent LLM client layer (OpenAI-compatible, Ollama, Mock), usage metering, and LangChain ChatModel adapter."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from typing import Any, ClassVar, Literal

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core import Settings, SprintMatchError, redact

logger = logging.getLogger(__name__)


# ===========================================================================
# Usage Metering
# ===========================================================================

@dataclass
class UsageMeter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt_tokens or 0
            self.completion_tokens += completion_tokens or 0

    def snapshot(self) -> tuple[int, int, int]:
        with self._lock:
            return self.calls, self.prompt_tokens, self.completion_tokens


_current: ContextVar[UsageMeter | None] = ContextVar("sprintmatch_llm_usage", default=None)


@contextmanager
def metered(meter: UsageMeter | None = None) -> Iterator[UsageMeter]:
    meter = meter or UsageMeter()
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


def current_meter() -> UsageMeter | None:
    return _current.get()


def record_usage(prompt_tokens: int | None, completion_tokens: int | None) -> None:
    meter = _current.get()
    if meter is not None:
        meter.add(prompt_tokens, completion_tokens)

# ===========================================================================
# Base Provider & JSON Repair
# ===========================================================================

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

    # adapter contract
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

    # shared behaviour
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

# ===========================================================================
# HTTP Provider Client
# ===========================================================================

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

# ===========================================================================
# OpenAI-Compatible Provider
# ===========================================================================

_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "report_status",
        "description": "Report a status value.",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
    },
}


def parse_tool_calls(raw_calls: Any) -> tuple[ToolCallData, ...]:
    calls = []
    for i, raw in enumerate(raw_calls or []):
        fn = (raw or {}).get("function") or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            args: dict[str, Any] | None = raw_args
            raw_text = json.dumps(raw_args)
        else:
            raw_text = str(raw_args or "")
            try:
                parsed = json.loads(raw_text) if raw_text.strip() else {}
                args = parsed if isinstance(parsed, dict) else None
            except ValueError:
                args = None
        calls.append(
            ToolCallData(
                id=str(raw.get("id") or f"call_{i}"),
                name=str(fn.get("name") or ""),
                arguments=args,
                raw_arguments=raw_text[:2000],
            )
        )
    return tuple(calls)


class OpenAICompatibleProvider(HttpLLMProvider):
    name = "openai_compatible"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._probe_lock = threading.Lock()
        self._probed: ProviderCapabilities | None = None

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _complete(
        self, messages: list[ChatMessage], *, json_schema: dict[str, Any] | None, schema_name: str | None
    ) -> Completion:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self.config.temperature,
        }
        if json_schema is not None:
            if self.config.structured_output_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name or "output", "schema": json_schema, "strict": False},
                }
            else:
                body["response_format"] = {"type": "json_object"}
        data = self._request("POST", "chat/completions", body)
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailableError("openai_compatible response has no message content") from exc
        usage = data.get("usage") or {}
        return Completion(
            text=content,
            model=str(data.get("model") or self.config.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> ChatTurn:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = self._request("POST", "chat/completions", body)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailableError("openai_compatible response has no message") from exc
        usage = data.get("usage") or {}
        return ChatTurn(
            content=str(message.get("content") or ""),
            tool_calls=parse_tool_calls(message.get("tool_calls")),
            model=str(data.get("model") or self.config.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=choice.get("finish_reason"),
        )

    def capabilities(self, *, probe: bool = False) -> ProviderCapabilities:
        """Tool-calling support differs per model behind the same API: probe it once and cache the answer."""
        if self._probed is not None:
            return self._probed
        declared = ProviderCapabilities(
            tool_calling=None,
            structured_output=self.config.structured_output_mode == "json_schema",
            json_mode=True,
            streaming=True,
            detail="tool calling not probed yet",
        )
        if not probe:
            return declared
        with self._probe_lock:
            if self._probed is None:
                self._probed = self._probe(declared)
        return self._probed

    def _probe(self, declared: ProviderCapabilities) -> ProviderCapabilities:
        messages = [
            {"role": "system", "content": "You are a connectivity probe. Always use the provided tool."},
            {"role": "user", "content": "Call report_status with status 'ok'."},
        ]
        try:
            turn = self._chat(messages, [_PROBE_TOOL])
        except LLMRequestError as exc:  # the server/model rejects the tools parameter
            return ProviderCapabilities(
                tool_calling=False,
                structured_output=declared.structured_output,
                json_mode=True,
                streaming=True,
                source="probed",
                detail=f"tools rejected: {exc.code}",
            )
        supported = any(c.name == "report_status" for c in turn.tool_calls)
        return ProviderCapabilities(
            tool_calling=supported,
            structured_output=declared.structured_output,
            json_mode=True,
            streaming=True,
            source="probed",
            detail=None if supported else "model answered without calling the tool",
        )

    def health_check(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            data = self._request("GET", "models")
            ids = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
            ok = not ids or self.config.model in ids
            detail = None if ok else "configured model not listed by provider"
        except LLMError as exc:
            ok, detail = False, exc.code
        return ProviderHealth(
            ok=ok,
            provider=self.name,
            model=self.config.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail=detail,
        )

# ===========================================================================
# Ollama Provider
# ===========================================================================

def to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-format chat messages -> Ollama ``/api/chat`` messages (tool calls carry dict arguments)."""
    names_by_call: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function") or {}
                names_by_call[str(c.get("id"))] = str(fn.get("name"))
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    args = {}
                calls.append({"function": {"name": fn.get("name"), "arguments": args}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "content": m.get("content") or "",
                    "tool_name": names_by_call.get(str(m.get("tool_call_id")), ""),
                }
            )
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


class OllamaProvider(HttpLLMProvider):
    name = "ollama"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self._caps: ProviderCapabilities | None = None

    def _complete(
        self, messages: list[ChatMessage], *, json_schema: dict[str, Any] | None, schema_name: str | None
    ) -> Completion:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.model_dump() for m in messages],
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }
        if json_schema is not None:
            body["format"] = json_schema
        data = self._request("POST", "api/chat", body)
        message = data.get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise LLMUnavailableError("ollama response has no message content")
        return Completion(
            text=str(message["content"]),
            model=str(data.get("model") or self.config.model),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> ChatTurn:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": to_ollama_messages(messages),
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }
        if tools:
            body["tools"] = tools
        data = self._request("POST", "api/chat", body)
        message = data.get("message")
        if not isinstance(message, dict):
            raise LLMUnavailableError("ollama response has no message")
        return ChatTurn(
            content=str(message.get("content") or ""),
            tool_calls=parse_tool_calls(message.get("tool_calls")),
            model=str(data.get("model") or self.config.model),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            finish_reason=data.get("done_reason"),
        )

    def capabilities(self, *, probe: bool = False) -> ProviderCapabilities:
        """Ollama reports model capabilities (``tools``) in ``/api/show``; structured output is always supported."""
        if self._caps is not None:
            return self._caps
        if not probe:
            return ProviderCapabilities(
                tool_calling=None,
                structured_output=True,
                json_mode=True,
                streaming=True,
                detail="model capabilities not queried yet",
            )
        with self._lock:
            if self._caps is None:
                try:
                    data = self._request("POST", "api/show", {"model": self.config.model})
                    caps = data.get("capabilities")
                    tool_calling = "tools" in caps if isinstance(caps, list) else None
                    detail = None if isinstance(caps, list) else "server does not report capabilities"
                except LLMError as exc:
                    tool_calling, detail = None, f"capability query failed: {exc.code}"
                self._caps = ProviderCapabilities(
                    tool_calling=tool_calling,
                    structured_output=True,
                    json_mode=True,
                    streaming=True,
                    source="probed",
                    detail=detail,
                )
        return self._caps

    def health_check(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            data = self._request("GET", "api/tags")
            names = {m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)}
            wanted = self.config.model
            ok = any(n == wanted or n.split(":")[0] == wanted for n in names)
            detail = None if ok else "model not pulled on the Ollama server"
        except LLMError as exc:
            ok, detail = False, exc.code
        return ProviderHealth(
            ok=ok,
            provider=self.name,
            model=self.config.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail=detail,
        )

# ===========================================================================
# Deterministic Mock Provider
# ===========================================================================

Responder = Callable[[list[ChatMessage], str | None], str]
Scripted = str | Exception | Responder
ChatResponder = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], ChatTurn]
ChatScripted = ChatTurn | Exception | ChatResponder


def default_responder(messages: list[ChatMessage], schema_name: str | None) -> str:
    if schema_name == "task_analysis":
        return json.dumps(
            {
                "task_category": None,
                "required_skills": [],
                "complexity": None,
                "estimated_hours": None,
                "missing_information": ["mock provider performs no analysis"],
                "analysis_confidence": 0.1,
            }
        )
    return "Mock explanation generated without a language model."


def default_chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> ChatTurn:
    """No tool calls: an empty, valid research summary."""
    return ChatTurn(
        content=json.dumps({"relevant_employee_codes": [], "findings": [], "missing_information": []}),
        tool_calls=(),
        model="mock-model",
    )


def tool_call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCallData:
    return ToolCallData(
        id=call_id or f"call_{name}", name=name, arguments=arguments, raw_arguments=json.dumps(arguments)
    )


def calls_turn(*calls: ToolCallData, content: str = "") -> ChatTurn:
    return ChatTurn(content=content, tool_calls=tuple(calls), model="mock-model", prompt_tokens=50)


def final_turn(summary: dict[str, Any] | str) -> ChatTurn:
    text = summary if isinstance(summary, str) else json.dumps(summary)
    return ChatTurn(content=text, tool_calls=(), model="mock-model", prompt_tokens=80, completion_tokens=40)


class MockLLMProvider(LLMProvider):
    name = "mock"

    def __init__(
        self,
        responses: Sequence[Scripted] | None = None,
        *,
        chat_responses: Sequence[ChatScripted] | None = None,
        model: str = "mock-model",
        healthy: bool = True,
        tool_calling: bool | None = True,
        structured_output: bool = True,
    ) -> None:
        super().__init__(LLMConfig(provider="mock", model=model, max_retries=1), sleep=lambda _s: None)
        self._responses: list[Scripted] = list(responses) if responses else [default_responder]
        self._chat_responses: list[ChatScripted] = list(chat_responses) if chat_responses else [default_chat]
        self._healthy = healthy
        self._tool_calling = tool_calling
        self._structured = structured_output
        self.calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []

    def _complete(
        self, messages: list[ChatMessage], *, json_schema: dict[str, Any] | None, schema_name: str | None
    ) -> Completion:
        index = min(len(self.calls), len(self._responses) - 1)
        self.calls.append({"messages": messages, "schema_name": schema_name})
        item = self._responses[index]
        if isinstance(item, Exception):
            raise item
        text = item(messages, schema_name) if callable(item) else item
        return Completion(text=text, model=self.config.model, prompt_tokens=0, completion_tokens=0)

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> ChatTurn:
        index = min(len(self.chat_calls), len(self._chat_responses) - 1)
        self.chat_calls.append({"messages": messages, "tools": tools})
        item = self._chat_responses[index]
        if isinstance(item, Exception):
            raise item
        return item(messages, tools) if callable(item) else item

    def capabilities(self, *, probe: bool = False) -> ProviderCapabilities:
        return ProviderCapabilities(
            tool_calling=self._tool_calling,
            structured_output=self._structured,
            json_mode=self._structured,
            streaming=False,
            source="configured",
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=self._healthy, provider=self.name, model=self.config.model, latency_ms=0)

# ===========================================================================
# LangChain BaseChatModel Adapter
# ===========================================================================

MALFORMED_ARGUMENTS_KEY = "_malformed_arguments"


def to_openai_messages(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.content if isinstance(m.content, str) else json.dumps(m.content, default=str)
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": content})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": content})
        elif isinstance(m, AIMessage):
            msg: dict[str, Any] = {"role": "assistant", "content": content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c["args"], default=str)},
                    }
                    for c in m.tool_calls
                ]
            out.append(msg)
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": content})
        else:  # pragma: no cover - the agent only produces the message types above
            out.append({"role": "user", "content": content})
    return out


def turn_to_message(turn: ChatTurn, provider: str) -> AIMessage:
    tool_calls = []
    for c in turn.tool_calls:
        # Unparsable arguments still become a call so the policy layer can answer with a structured error
        # (the model gets feedback instead of a silently dropped request).
        args = c.arguments if c.arguments is not None else {MALFORMED_ARGUMENTS_KEY: c.raw_arguments[:500]}
        tool_calls.append({"name": c.name, "args": args, "id": c.id, "type": "tool_call"})
    usage = None
    if turn.prompt_tokens is not None or turn.completion_tokens is not None:
        prompt, completion = turn.prompt_tokens or 0, turn.completion_tokens or 0
        usage = {"input_tokens": prompt, "output_tokens": completion, "total_tokens": prompt + completion}
    return AIMessage(
        content=turn.content,
        tool_calls=tool_calls,
        usage_metadata=usage,
        response_metadata={
            "model_name": turn.model,
            "finish_reason": turn.finish_reason,
            "provider": provider,
        },
    )


# JSON tool plan
class PlannedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolPlan(BaseModel):
    """Structured substitute for native tool calling."""

    model_config = ConfigDict(extra="ignore")
    action: Literal["call_tools", "finish"]
    tool_calls: list[PlannedToolCall] = Field(default_factory=list, max_length=8)
    final_answer: dict[str, Any] | None = None


_PLAN_INSTRUCTIONS = """You cannot call functions natively. Instead reply with ONE JSON object:
{{"action": "call_tools", "tool_calls": [{{"name": "<tool name>", "arguments": {{...}}}}]}}
to request tools, or
{{"action": "finish", "final_answer": {{...}}}}
when you have enough evidence. Only these tools exist (name: description; JSON schema of arguments):
{catalog}"""


def _plan_messages(messages: Sequence[BaseMessage], tools: list[dict[str, Any]]) -> list[ChatMessage]:
    catalog = "\n".join(
        f"- {t['function']['name']}: {t['function'].get('description', '')}; "
        f"{json.dumps(t['function'].get('parameters', {}), separators=(',', ':'))}"
        for t in tools
    )
    out = [ChatMessage(role="system", content=_PLAN_INSTRUCTIONS.format(catalog=catalog))]
    for m in messages:
        content = m.content if isinstance(m.content, str) else json.dumps(m.content, default=str)
        if isinstance(m, SystemMessage):
            out.append(ChatMessage(role="system", content=content))
        elif isinstance(m, AIMessage):
            requested = [{"id": c["id"], "name": c["name"], "arguments": c["args"]} for c in m.tool_calls]
            text = content or ""
            if requested:
                text = (text + "\n" if text else "") + "TOOL_REQUESTS: " + json.dumps(requested, default=str)
            out.append(ChatMessage(role="assistant", content=text or "{}"))
        elif isinstance(m, ToolMessage):
            out.append(
                ChatMessage(role="user", content=f"TOOL_RESULT (call {m.tool_call_id}, {m.name}): {content}")
            )
        else:
            out.append(ChatMessage(role="user", content=content))
    return out


class ProviderChatModel(BaseChatModel):
    provider: Any = Field(exclude=True)  # LLMProvider (kept Any: pydantic field on a LangChain model)
    mode: Literal["native", "json_plan"] = "native"

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return f"sprintmatch-{self._provider.name}"

    @property
    def _provider(self) -> LLMProvider:
        provider: LLMProvider = self.provider
        return provider

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"provider": self._provider.name, "model": self._provider.model, "mode": self.mode}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools: list[dict[str, Any]] = kwargs.get("tools") or []
        if self.mode == "native":
            turn = self._provider.chat(to_openai_messages(messages), tools or None).value
            message = turn_to_message(turn, self._provider.name)
        else:
            message = self._generate_plan(messages, tools)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate_plan(self, messages: list[BaseMessage], tools: list[dict[str, Any]]) -> AIMessage:
        result = self._provider.generate_structured(
            _plan_messages(messages, tools), ToolPlan, schema_name="tool_plan"
        )
        plan = result.value
        allowed = {t["function"]["name"] for t in tools}
        usage = {
            "input_tokens": result.prompt_tokens or 0,
            "output_tokens": result.completion_tokens or 0,
            "total_tokens": (result.prompt_tokens or 0) + (result.completion_tokens or 0),
        }
        metadata = {"model_name": result.model, "provider": self._provider.name, "mode": "json_plan"}
        if plan.action == "finish" or not plan.tool_calls:
            return AIMessage(
                content=json.dumps(plan.final_answer or {}),
                usage_metadata=usage,
                response_metadata=metadata,
            )
        n_previous = sum(1 for m in messages if isinstance(m, AIMessage))
        tool_calls, invalid = [], []
        for i, call in enumerate(plan.tool_calls):
            call_id = f"plan_{n_previous}_{i}"
            if call.name in allowed:
                tool_calls.append(
                    {"name": call.name, "args": call.arguments, "id": call_id, "type": "tool_call"}
                )
            else:  # explicit allowlist: unknown names never reach the ToolNode
                invalid.append(
                    {
                        "name": call.name,
                        "args": json.dumps(call.arguments, default=str)[:500],
                        "id": call_id,
                        "error": "UNKNOWN_TOOL",
                        "type": "invalid_tool_call",
                    }
                )
        return AIMessage(
            content="",
            tool_calls=tool_calls,
            invalid_tool_calls=invalid,
            usage_metadata=usage,
            response_metadata=metadata,
        )

# ===========================================================================
# Provider & ChatModel Factory
# ===========================================================================

_ADAPTERS: dict[str, type[OpenAICompatibleProvider] | type[OllamaProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
}


def llm_config_from_settings(settings: Settings) -> LLMConfig:
    return LLMConfig(
        provider=settings.llm_provider,
        model=settings.llm_model or "",
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_retry_wait_seconds=settings.llm_max_retry_wait_seconds,
        structured_output_mode=settings.llm_structured_output_mode,
    )


def build_llm_provider(settings: Settings) -> LLMProvider | None:
    """None means "no LLM configured": every workflow then runs on deterministic paths only."""
    if settings.llm_provider == "none":
        return None
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    adapter = _ADAPTERS.get(settings.llm_provider)
    if adapter is None:  # pragma: no cover - guarded by the Settings Literal
        raise LLMNotConfiguredError(f"Unknown LLM provider {settings.llm_provider!r}")
    return adapter(llm_config_from_settings(settings))


def build_chat_model(provider: LLMProvider, mode: Literal["native", "json_plan"]) -> ProviderChatModel:
    """LangChain chat model for the research agent (tool binding happens in the agent node)."""
    return ProviderChatModel(provider=provider, mode=mode)

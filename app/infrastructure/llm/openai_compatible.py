"""Adapter for any OpenAI-compatible Chat Completions API (cloud providers, vLLM, LM Studio, ...)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from app.infrastructure.llm.base import (
    ChatMessage,
    ChatTurn,
    Completion,
    LLMError,
    LLMRequestError,
    LLMUnavailableError,
    ProviderCapabilities,
    ProviderHealth,
    ToolCallData,
)
from app.infrastructure.llm.http_provider import HttpLLMProvider

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

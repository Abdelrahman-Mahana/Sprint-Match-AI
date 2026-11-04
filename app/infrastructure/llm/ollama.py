"""Native Ollama adapter (``/api/chat``; structured outputs via the ``format`` JSON schema field; tools)."""

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
    LLMUnavailableError,
    ProviderCapabilities,
    ProviderHealth,
)
from app.infrastructure.llm.http_provider import HttpLLMProvider
from app.infrastructure.llm.openai_compatible import parse_tool_calls


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

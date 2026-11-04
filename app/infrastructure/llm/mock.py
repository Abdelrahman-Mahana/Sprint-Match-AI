"""Deterministic in-process provider for tests and offline demos. Never performs network I/O.

``responses`` script text/structured completions; ``chat_responses`` script tool-enabled chat turns (real tool
calls that the LangGraph ToolNode then executes). Items are returned in order (the last one repeats);
exceptions are raised and callables are invoked with the request.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from app.infrastructure.llm.base import (
    ChatMessage,
    ChatTurn,
    Completion,
    LLMConfig,
    LLMProvider,
    ProviderCapabilities,
    ProviderHealth,
    ToolCallData,
)

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

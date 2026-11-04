"""LangChain chat-model adapter over any ``LLMProvider``.

``ProviderChatModel`` is a real ``BaseChatModel``: ``bind_tools`` binds LangChain tools and every call returns an
``AIMessage`` whose ``tool_calls`` LangGraph's ``ToolNode`` executes. Two modes:

* ``native``    - the provider's own tool-calling API (OpenAI-compatible ``tools``, Ollama ``tools``).
* ``json_plan`` - for models without native tool calling but with reliable structured output: the model
  returns a validated JSON *tool plan*; tool names are checked against the explicit allowlist of bound tools
  and the plan is converted into ordinary tool calls, so the graph and ToolNode are identical in both modes.

Transport, retries, redaction and usage accounting stay in the provider layer.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.llm.base import ChatMessage, ChatTurn, LLMProvider

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


# --- JSON tool plan ------------------------------------------------------------------------------------
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

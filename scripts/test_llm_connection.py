"""Live check of the configured LLM provider (uses the real provider from .env; costs a few tokens).

1. health check (reachability, configured model);
2. capabilities (tool calling probed with one tiny request);
3. a structured-output call validated against a schema;
4. one native tool call through the LangChain chat-model adapter (when supported).

Usage:  uv run python -m scripts.test_llm_connection
"""

from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.configuration import resolve_agent_mode
from app.agents.tools import ALL_TOOLS
from app.core.config import get_settings
from app.core.logging import register_secrets
from app.domain.enums import AgentMode
from app.infrastructure.llm.base import ChatMessage, LLMError
from app.infrastructure.llm.factory import build_chat_model, build_llm_provider


class Echo(BaseModel):
    status: str
    number: int


def main() -> int:
    settings = get_settings()
    register_secrets(settings.secret_values())
    llm = build_llm_provider(settings)
    if llm is None:
        print("LLM_PROVIDER=none: nothing to test (the deterministic engine does not need an LLM).")
        return 0
    print(f"provider={llm.name} model={llm.model}")
    health = llm.health_check()
    print(f"1. health: ok={health.ok} latency_ms={health.latency_ms} detail={health.detail}")
    if not health.ok:
        return 1
    decision = resolve_agent_mode(settings, llm)
    print(f"2. capabilities: {decision.capabilities} -> agent mode {decision.mode.value} ({decision.reason})")
    try:
        result = llm.generate_structured(
            [ChatMessage(role="user", content='Return {"status": "ok", "number": 7} as JSON.')],
            Echo,
            schema_name="echo",
        )
        print(
            f"3. structured output: {result.value} ({result.latency_ms} ms, tokens {result.prompt_tokens}/{result.completion_tokens})"
        )
    except LLMError as exc:
        print(f"3. structured output FAILED: {exc.code}")
        return 1
    if decision.mode == AgentMode.DISABLED:
        print("4. tool calling: skipped (agent mode disabled)")
        return 0
    model = build_chat_model(llm, "native" if decision.mode == AgentMode.NATIVE else "json_plan")
    message = model.bind_tools(ALL_TOOLS).invoke(
        [
            SystemMessage("You can call tools. Call exactly one tool."),
            HumanMessage("Look up the skills of employee EMP-001."),
        ]
    )
    calls = [(c["name"], c["args"]) for c in message.tool_calls]
    print(f"4. tool calling ({decision.mode.value}): {calls}")
    return 0 if calls else 1


if __name__ == "__main__":
    sys.exit(main())

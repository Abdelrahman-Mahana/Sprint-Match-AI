"""Unit tests for LLM providers, retries, structured output, and native/json_plan tool calling."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.core import Settings
from app.domain import ScoringConfig, Skill, TaskInput
from app.infrastructure.llm import (
    MALFORMED_ARGUMENTS_KEY,
    ChatMessage,
    LLMConfig,
    LLMInvalidOutputError,
    LLMNotConfiguredError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
    MockLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderChatModel,
    build_llm_provider,
    calls_turn,
    extract_json,
    final_turn,
    metered,
    to_ollama_messages,
    to_openai_messages,
    tool_call,
)
from app.intelligence.analyzer import TaskAnalyzer

# ===========================================================================
# LLM Provider & Retry Tests
# ===========================================================================

SECRET = "sk-test-secret-key-123456"


class Answer(BaseModel):
    value: int


def cfg(provider: str, **kw) -> LLMConfig:  # type: ignore[no-untyped-def]
    base = {"openai_compatible": "https://llm.example/v1", "ollama": "http://ollama.local:11434"}[provider]
    return LLMConfig(
        provider=provider, model="test-model", base_url=base, api_key=SECRET, max_retries=2, **kw
    )


def openai_body(content: str) -> dict:
    return {
        "model": "test-model",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def ollama_body(content: str) -> dict:
    return {
        "model": "test-model",
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": 10,
        "eval_count": 5,
    }


def scripted(responses: list) -> tuple[httpx.MockTransport, list[httpx.Request]]:  # type: ignore[type-arg]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses[min(len(seen) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), seen


MSG = [ChatMessage(role="user", content="answer")]


def test_openai_compatible_request_shape_and_auth() -> None:
    transport, seen = scripted([(200, openai_body('{"value": 42}'))])
    p = OpenAICompatibleProvider(cfg("openai_compatible"), transport=transport, sleep=lambda _: None)
    result = p.generate_structured(MSG, Answer, schema_name="answer")
    assert result.value.value == 42 and result.prompt_tokens == 10
    req = seen[0]
    assert str(req.url) == "https://llm.example/v1/chat/completions"
    assert req.headers["Authorization"] == f"Bearer {SECRET}"
    body = json.loads(req.content)
    assert body["response_format"]["type"] == "json_schema" and body["temperature"] == 0.0


def test_openai_compatible_json_object_mode() -> None:
    transport, seen = scripted([(200, openai_body('{"value": 1}'))])
    p = OpenAICompatibleProvider(
        cfg("openai_compatible", structured_output_mode="json_object"), transport=transport
    )
    p.generate_structured(MSG, Answer, schema_name="answer")
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}


def test_ollama_request_shape_uses_format_schema() -> None:
    transport, seen = scripted([(200, ollama_body('{"value": 7}'))])
    p = OllamaProvider(cfg("ollama"), transport=transport)
    assert p.generate_structured(MSG, Answer, schema_name="answer").value.value == 7
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "http://ollama.local:11434/api/chat"
    assert body["stream"] is False and body["format"]["properties"]["value"]["type"] == "integer"


@pytest.mark.parametrize("provider,body", [("openai_compatible", openai_body), ("ollama", ollama_body)])
def test_transient_errors_are_retried_with_bound(provider: str, body) -> None:  # type: ignore[no-untyped-def]
    cls = OpenAICompatibleProvider if provider == "openai_compatible" else OllamaProvider
    sleeps: list[float] = []
    transport, seen = scripted([(503, {}), (429, {}), (200, body('{"value": 3}'))])
    p = cls(cfg(provider), transport=transport, sleep=sleeps.append)
    assert p.generate_structured(MSG, Answer, schema_name="a").attempts == 3
    assert sleeps == [0.5, 1.0]
    transport, seen = scripted([(500, {})])
    with pytest.raises(LLMUnavailableError):
        cls(cfg(provider), transport=transport, sleep=lambda _: None).generate_text(MSG)
    assert len(seen) == 3  # 1 + max_retries, never unbounded


def test_timeout_and_rejection_classification() -> None:
    transport, seen = scripted([httpx.ReadTimeout("slow")])
    with pytest.raises(LLMTimeoutError):
        OpenAICompatibleProvider(
            cfg("openai_compatible"), transport=transport, sleep=lambda _: None
        ).generate_text(MSG)
    transport, seen = scripted([(401, {"error": f"bad key {SECRET}"})])
    with pytest.raises(LLMRequestError) as exc:
        OpenAICompatibleProvider(cfg("openai_compatible"), transport=transport).generate_text(MSG)
    assert len(seen) == 1  # 4xx is not retried
    assert SECRET not in str(exc.value.details)  # secrets redacted from error details


def test_invalid_structured_output_gets_one_repair_then_fails() -> None:
    transport, seen = scripted([(200, openai_body("not json")), (200, openai_body('{"value": "x"}'))])
    p = OpenAICompatibleProvider(cfg("openai_compatible"), transport=transport)
    with pytest.raises(LLMInvalidOutputError):
        p.generate_structured(MSG, Answer, schema_name="a")
    assert len(seen) == 2
    repair_prompt = json.loads(seen[1].content)["messages"][-1]["content"]
    assert "did not match the required JSON schema" in repair_prompt
    transport, _ = scripted([(200, openai_body("garbage")), (200, openai_body('```json\n{"value": 5}\n```'))])
    assert (
        OpenAICompatibleProvider(cfg("openai_compatible"), transport=transport)
        .generate_structured(MSG, Answer, schema_name="a")
        .value.value
        == 5
    )


def test_extract_json_variants() -> None:
    assert extract_json('Here you go: {"a": 1} thanks') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no object")


def test_health_checks() -> None:
    transport, _ = scripted([(200, {"data": [{"id": "test-model"}]})])
    assert OpenAICompatibleProvider(cfg("openai_compatible"), transport=transport).health_check().ok
    transport, _ = scripted([(200, {"models": [{"name": "test-model:latest"}]})])
    assert OllamaProvider(cfg("ollama"), transport=transport).health_check().ok
    transport, _ = scripted([httpx.ConnectError("refused")])
    h = OllamaProvider(cfg("ollama"), transport=transport, sleep=lambda _: None).health_check()
    assert not h.ok and h.detail == "PROVIDER_UNAVAILABLE"


def test_factory_selects_provider_from_configuration() -> None:
    base = {"_env_file": None, "api_keys": []}
    assert build_llm_provider(Settings(**base, llm_provider="none")) is None
    assert isinstance(build_llm_provider(Settings(**base, llm_provider="mock")), MockLLMProvider)
    p = build_llm_provider(
        Settings(**base, llm_provider="ollama", llm_base_url="http://x:11434", llm_model="m")
    )
    assert isinstance(p, OllamaProvider)
    p = build_llm_provider(
        Settings(
            **base,
            llm_provider="openai_compatible",
            llm_base_url="https://x/v1",
            llm_model="m",
            llm_api_key="",
        )
    )
    assert isinstance(p, OpenAICompatibleProvider) and p.config.api_key is None
    with pytest.raises(LLMNotConfiguredError):
        build_llm_provider(Settings(**base, llm_provider="ollama", llm_model="m"))


def test_provider_independence_same_business_result() -> None:
    """Identical model output through different adapters yields identical requirements."""
    catalog = [
        Skill(id=uuid.uuid5(uuid.NAMESPACE_DNS, n), name=n, category="x") for n in ("Python", "FastAPI")
    ]
    content = json.dumps(
        {
            "task_category": "backend_development",
            "complexity": "high",
            "estimated_hours": None,
            "analysis_confidence": 0.8,
            "missing_information": [],
            "required_skills": [
                {
                    "skill_name": "Python",
                    "minimum_level": 70,
                    "mandatory": True,
                    "evidence_quote": "Python service",
                }
            ],
        }
    )
    task = TaskInput(title="Build a Python service", description="Build a Python service", estimated_hours=8)
    results = []
    for provider, body in (("openai_compatible", openai_body), ("ollama", ollama_body)):
        cls = OpenAICompatibleProvider if provider == "openai_compatible" else OllamaProvider
        transport, _ = scripted([(200, body(content))])
        analyzer = TaskAnalyzer(ScoringConfig(), cls(cfg(provider), transport=transport))
        results.append(analyzer.analyze(task, catalog).requirements)
    mock = TaskAnalyzer(ScoringConfig(), MockLLMProvider([content])).analyze(task, catalog).requirements
    assert results[0] == results[1] == mock
    assert results[0].required_skills[0].mandatory

# ===========================================================================
# LLM Tool Calling Tests
# ===========================================================================

class EmployeeArgs(BaseModel):
    employee_id: str


def _lookup(employee_id: str) -> str:
    return employee_id


LOOKUP = StructuredTool.from_function(
    func=_lookup, name="get_employee_profile", description="Profile", args_schema=EmployeeArgs
)


def tool_cfg(provider: str) -> LLMConfig:
    base = {"openai_compatible": "https://llm.example/v1", "ollama": "http://ollama.local:11434"}[provider]
    return LLMConfig(provider=provider, model="test-model", base_url=base, api_key="sk-x", max_retries=1)


def test_openai_compatible_sends_tools_and_parses_tool_calls() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {
                                        "name": "get_employee_profile",
                                        "arguments": '{"employee_id": "EMP-001"}',
                                    },
                                },
                                {
                                    "id": "call_b",
                                    "type": "function",
                                    "function": {"name": "get_employee_profile", "arguments": "{not json"},
                                },
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 7},
            },
        )

    p = OpenAICompatibleProvider(tool_cfg("openai_compatible"), transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "get_employee_profile", "parameters": {}}}]
    with metered() as meter:
        turn = p.chat([{"role": "user", "content": "hi"}], tools).value
    assert seen[0]["tools"] == tools and seen[0]["tool_choice"] == "auto"
    assert [c.id for c in turn.tool_calls] == ["call_a", "call_b"]
    assert turn.tool_calls[0].arguments == {"employee_id": "EMP-001"}
    assert turn.tool_calls[1].arguments is None and turn.tool_calls[1].raw_arguments == "{not json"
    assert turn.finish_reason == "tool_calls" and turn.content == ""
    assert meter.snapshot() == (1, 30, 7)


def test_openai_compatible_capability_probe() -> None:
    def calls_tool(request: httpx.Request) -> httpx.Response:
        call = {"id": "c", "function": {"name": "report_status", "arguments": '{"status": "ok"}'}}
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [call]}}]})

    def rejects_tools(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "tools are not supported for this model"})

    supported = OpenAICompatibleProvider(tool_cfg("openai_compatible"), transport=httpx.MockTransport(calls_tool))
    assert supported.capabilities().tool_calling is None  # unknown until probed
    caps = supported.capabilities(probe=True)
    assert caps.tool_calling is True and caps.source == "probed"
    unsupported = OpenAICompatibleProvider(
        tool_cfg("openai_compatible"), transport=httpx.MockTransport(rejects_tools), sleep=lambda _s: None
    )
    assert unsupported.capabilities(probe=True).tool_calling is False


def test_ollama_tool_calls_and_capabilities() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        call = {"function": {"name": "get_employee_profile", "arguments": {"employee_id": "EMP-002"}}}
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "message": {"role": "assistant", "content": "", "tool_calls": [call]},
            },
        )

    p = OllamaProvider(tool_cfg("ollama"), transport=httpx.MockTransport(handler))
    turn = p.chat([{"role": "user", "content": "x"}], [{"type": "function", "function": {"name": "t"}}]).value
    assert turn.tool_calls[0].arguments == {"employee_id": "EMP-002"} and turn.tool_calls[0].id == "call_0"
    assert bodies[0]["tools"] and bodies[0]["stream"] is False
    assert p.capabilities(probe=True).tool_calling is True


def test_ollama_message_conversion_keeps_tool_names() -> None:
    converted = to_ollama_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "get_task_details", "arguments": '{"a": 1}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        ]
    )
    assert converted[0]["tool_calls"][0]["function"]["arguments"] == {"a": 1}
    assert converted[1] == {"role": "tool", "content": "{}", "tool_name": "get_task_details"}


def test_chat_model_native_mode_returns_real_tool_calls() -> None:
    provider = MockLLMProvider(
        chat_responses=[
            calls_turn(
                tool_call("get_employee_profile", "c1", employee_id="EMP-001"),
                tool_call("get_employee_profile", "c2", employee_id="EMP-004"),
            )
        ]
    )
    model = ProviderChatModel(provider=provider, mode="native").bind_tools([LOOKUP])
    msg = model.invoke([SystemMessage("sys"), HumanMessage("research")])
    assert isinstance(msg, AIMessage)
    assert [(c["name"], c["args"], c["id"]) for c in msg.tool_calls] == [
        ("get_employee_profile", {"employee_id": "EMP-001"}, "c1"),
        ("get_employee_profile", {"employee_id": "EMP-004"}, "c2"),
    ]
    sent_tools = provider.chat_calls[0]["tools"]
    assert sent_tools[0]["function"]["name"] == "get_employee_profile"
    assert "employee_id" in sent_tools[0]["function"]["parameters"]["properties"]
    assert msg.usage_metadata is not None and msg.usage_metadata["input_tokens"] == 50


def test_chat_model_marks_malformed_arguments() -> None:
    from app.infrastructure.llm import ToolCallData

    bad = ToolCallData(id="c9", name="get_employee_profile", arguments=None, raw_arguments="{oops")
    provider = MockLLMProvider(chat_responses=[calls_turn(bad)])
    msg = ProviderChatModel(provider=provider).bind_tools([LOOKUP]).invoke([HumanMessage("x")])
    assert msg.tool_calls[0]["args"] == {MALFORMED_ARGUMENTS_KEY: "{oops"}


def test_message_conversion_round_trip() -> None:
    ai = AIMessage(content="", tool_calls=[{"name": "t", "args": {"x": 1}, "id": "c1", "type": "tool_call"}])
    out = to_openai_messages([HumanMessage("q"), ai, ToolMessage(content='{"ok": 1}', tool_call_id="c1")])
    assert out[1]["tool_calls"][0]["function"] == {"name": "t", "arguments": '{"x": 1}'}
    assert out[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": 1}'}


def test_json_plan_mode_validates_against_allowlist() -> None:
    plan = {
        "action": "call_tools",
        "tool_calls": [
            {"name": "get_employee_profile", "arguments": {"employee_id": "EMP-001"}},
            {"name": "execute_sql", "arguments": {"query": "DROP TABLE employees"}},
        ],
    }
    finish = {"action": "finish", "final_answer": {"relevant_employee_codes": ["EMP-001"], "findings": []}}
    provider = MockLLMProvider([json.dumps(plan), json.dumps(finish)], tool_calling=False)
    model = ProviderChatModel(provider=provider, mode="json_plan").bind_tools([LOOKUP])
    first = model.invoke([HumanMessage("research")])
    assert [c["name"] for c in first.tool_calls] == ["get_employee_profile"]
    assert first.invalid_tool_calls[0]["name"] == "execute_sql"
    assert first.invalid_tool_calls[0]["error"] == "UNKNOWN_TOOL"
    # The tool catalog and the previous tool result are given to the model as plain text.
    tool_result = ToolMessage(content='{"employee_code": "EMP-001"}', tool_call_id=first.tool_calls[0]["id"])
    second = model.invoke([HumanMessage("research"), first, tool_result])
    assert second.tool_calls == [] and json.loads(second.content)["relevant_employee_codes"] == ["EMP-001"]
    prompt = provider.calls[1]["messages"]
    assert "get_employee_profile" in prompt[0].content and "TOOL_RESULT" in prompt[-1].content


def test_invalid_tool_plan_is_rejected_after_one_repair() -> None:
    import pytest

    from app.infrastructure.llm import LLMInvalidOutputError

    provider = MockLLMProvider(["not json", '{"action": "dance"}'])
    model = ProviderChatModel(provider=provider, mode="json_plan").bind_tools([LOOKUP])
    with pytest.raises(LLMInvalidOutputError):
        model.invoke([HumanMessage("x")])
    assert len(provider.calls) == 2


def test_final_turn_and_mock_capabilities() -> None:
    p = MockLLMProvider(chat_responses=[final_turn({"findings": []})], tool_calling=False)
    assert p.capabilities().tool_calling is False and p.capabilities().structured_output is True
    msg = ProviderChatModel(provider=p).invoke([HumanMessage("x")])
    assert msg.tool_calls == [] and json.loads(msg.content) == {"findings": []}


def test_rate_limit_retry_after_is_honored_and_bounded() -> None:
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "13"}, json={"error": "rate limited"}),
            httpx.Response(429, headers={"retry-after": "999"}, json={"error": "rate limited"}),
            httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": []}}]}),
        ]
    )
    slept: list[float] = []
    config = LLMConfig(
        provider="openai_compatible",
        model="m",
        base_url="https://llm.example/v1",
        max_retries=2,
        max_retry_wait_seconds=20,
    )
    p = OpenAICompatibleProvider(
        config, transport=httpx.MockTransport(lambda r: next(responses)), sleep=slept.append
    )
    assert p.chat([{"role": "user", "content": "x"}]).attempts == 3
    assert slept == [13.0, 20.0]  # provider-requested wait, capped by max_retry_wait_seconds

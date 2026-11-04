from __future__ import annotations

import json
import uuid

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.domain.entities.employee import Skill
from app.domain.entities.task import TaskInput
from app.domain.policies import ScoringConfig
from app.infrastructure.llm.base import (
    ChatMessage,
    LLMConfig,
    LLMInvalidOutputError,
    LLMNotConfiguredError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
    extract_json,
)
from app.infrastructure.llm.factory import build_llm_provider
from app.infrastructure.llm.mock import MockLLMProvider
from app.infrastructure.llm.ollama import OllamaProvider
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider
from app.intelligence.task_analysis.analyzer import TaskAnalyzer

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

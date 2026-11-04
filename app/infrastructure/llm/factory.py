"""Provider selection from configuration. Adding a provider = one adapter class + one entry here."""

from __future__ import annotations

from typing import Literal

from app.core.config import Settings
from app.infrastructure.llm.base import LLMConfig, LLMNotConfiguredError, LLMProvider
from app.infrastructure.llm.chat_model import ProviderChatModel
from app.infrastructure.llm.mock import MockLLMProvider
from app.infrastructure.llm.ollama import OllamaProvider
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider

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

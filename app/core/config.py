"""Application configuration loaded from environment variables (and an optional .env file).

Secrets are held as ``SecretStr`` so they never appear in reprs or logs.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.enums import Role


class ApiKeyConfig(BaseModel):
    """One API client. Only the SHA-256 hex digest of the key is configured, never the key itself."""

    name: str
    role: Role
    company_code: str
    key_sha256: str = Field(min_length=64, max_length=64)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SprintMatch AI"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    # --- Database -----------------------------------------------------------------------------
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://sprintmatch:sprintmatch@localhost:5432/sprintmatch"
    )
    database_echo: bool = False
    database_pool_size: int = 5
    database_connect_timeout_seconds: int = 5

    # --- Data-source adaptation ---------------------------------------------------------------
    # auto: use the native schema when the database has it, otherwise discover and adapt to it.
    database_adapter: Literal["auto", "native", "adaptive"] = "auto"
    schema_mapping_path: str = "schema_mapping.json"
    # Used when the connected source does not state a timezone / schedule of its own.
    adaptive_default_timezone: str = "UTC"
    adaptive_assume_default_schedule: bool = True
    adaptive_default_daily_hours: float = Field(default=8.0, gt=0, le=16)
    # Where the agent's own output (runs, audit, checkpoints) is stored. Defaults to database_url;
    # set it when database_url points at an external system that must never receive agent writes.
    internal_database_url: SecretStr | None = None

    # --- LLM provider (fully optional; the deterministic core never needs it) ----------------
    llm_provider: Literal["none", "mock", "openai_compatible", "ollama"] = "none"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_retry_wait_seconds: float = Field(default=20.0, ge=0, le=120)
    llm_structured_output_mode: Literal["json_schema", "json_object"] = "json_schema"
    explanation_mode: Literal["deterministic", "llm"] = "deterministic"

    # --- LangGraph agent ---------------------------------------------------------------------
    # auto: native tool calling when the model supports it, else a validated JSON tool plan, else disabled.
    agent_tool_mode: Literal["auto", "native", "json_plan", "disabled"] = "auto"
    agent_max_tool_rounds: int = Field(default=5, ge=0, le=20)
    agent_max_tool_calls: int = Field(default=20, ge=0, le=100)
    agent_tool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    agent_max_tool_result_items: int = Field(default=25, ge=1, le=500)
    agent_max_tool_result_chars: int = Field(default=12_000, ge=500, le=200_000)
    agent_max_profile_expansions: int = Field(default=10, ge=1, le=100)
    agent_node_max_attempts: int = Field(default=2, ge=1, le=5)
    agent_retry_initial_interval_seconds: float = Field(default=0.5, ge=0, le=30)

    # --- LangGraph checkpoints (graph execution state; separate from recommendation history) ---------
    checkpointer: Literal["postgres", "memory", "none"] = "postgres"
    checkpoint_schema: str = Field(default="agent_checkpoints", pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    checkpoint_retention_days: int = Field(default=30, ge=1, le=3650)

    # --- API security ------------------------------------------------------------------------
    api_keys: list[ApiKeyConfig] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=30, ge=1)

    # --- Recommendation defaults -------------------------------------------------------------
    default_horizon_working_days: int = Field(default=5, ge=1, le=60)
    accept_llm_effort_estimates: bool = False

    # --- Demo data ---------------------------------------------------------------------------
    demo_reference_date: date | None = None
    demo_seed: int = 20261103

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("llm_base_url", "llm_model", mode="before")
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def secret_values(self) -> list[str]:
        """Values that must be redacted wherever they might appear in logs."""
        secrets = [self.database_url.get_secret_value()]
        if self.llm_api_key is not None:
            secrets.append(self.llm_api_key.get_secret_value())
        return [s for s in secrets if s]


@lru_cache
def get_settings() -> Settings:
    return Settings()

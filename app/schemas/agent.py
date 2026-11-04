"""API contracts of the agent endpoints (run inspection, tools, logs, checkpoints, health)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.interfaces import (
    ExecutionTraceEntry,
    RunErrorRecord,
    StoredRecommendation,
    ToolExecutionRecord,
)
from app.schemas.recommendations import LLMUsageOut


class ApiOut(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRunOut(ApiOut):
    run_id: UUID
    request_id: str
    thread_id: str | None
    status: str
    task_id: UUID | None
    requested_by: str
    agent_mode: str | None
    llm_provider: str | None
    llm_model: str | None
    llm_usage: LLMUsageOut | None
    tool_calls: int
    execution_time_ms: int
    error_category: str | None
    resumable: bool
    retry_of_id: UUID | None
    idempotency_key: str | None
    generated_at: datetime
    updated_at: datetime | None
    primary_employee_code: str | None
    auto_assigned: bool = False

    @classmethod
    def from_stored(cls, s: StoredRecommendation) -> AgentRunOut:
        primary = s.result_snapshot.get("primary_candidate") or {}
        usage = (
            LLMUsageOut(
                calls=s.llm_calls,
                prompt_tokens=s.llm_prompt_tokens or 0,
                completion_tokens=s.llm_completion_tokens or 0,
            )
            if s.llm_calls or s.llm_prompt_tokens
            else None
        )
        return cls(
            run_id=s.id,
            request_id=s.request_id,
            thread_id=s.thread_id,
            status=s.status.value,
            task_id=s.task_id,
            requested_by=s.requested_by,
            agent_mode=s.agent_mode.value if s.agent_mode else None,
            llm_provider=s.llm_provider,
            llm_model=s.llm_model,
            llm_usage=usage,
            tool_calls=s.tool_calls_count,
            execution_time_ms=s.execution_time_ms,
            error_category=s.error_category.value if s.error_category else None,
            resumable=s.resumable,
            retry_of_id=s.retry_of_id,
            idempotency_key=s.idempotency_key,
            generated_at=s.generated_at,
            updated_at=s.updated_at,
            primary_employee_code=primary.get("employee_code"),
        )


class NodeLogOut(ApiOut):
    sequence: int
    node: str
    status: str
    duration_ms: int
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    llm_prompt_tokens: int | None
    llm_completion_tokens: int | None
    output_summary: dict[str, Any]
    error: str | None

    @classmethod
    def from_entry(cls, seq: int, e: ExecutionTraceEntry) -> NodeLogOut:
        return cls(
            sequence=seq,
            node=e.node_name,
            status=e.execution_status,
            duration_ms=e.duration_ms,
            attempts=e.attempts,
            started_at=e.started_at,
            finished_at=e.finished_at,
            llm_prompt_tokens=e.llm_prompt_tokens,
            llm_completion_tokens=e.llm_completion_tokens,
            output_summary=e.output_summary,
            error=e.error_message,
        )


class ToolExecutionOut(ApiOut):
    sequence: int
    research_round: int
    tool_call_id: str
    tool: str
    status: str
    arguments_summary: dict[str, Any]
    result_summary: dict[str, Any]
    error_code: str | None
    duration_ms: int

    @classmethod
    def from_record(cls, r: ToolExecutionRecord) -> ToolExecutionOut:
        return cls(
            sequence=r.sequence,
            research_round=r.research_round,
            tool_call_id=r.tool_call_id,
            tool=r.tool_name,
            status=r.status.value,
            arguments_summary=r.arguments_summary,
            result_summary=r.result_summary,
            error_code=r.error_code,
            duration_ms=r.duration_ms,
        )


class RunErrorOut(ApiOut):
    node: str | None
    code: str
    category: str
    message: str
    retryable: bool

    @classmethod
    def from_record(cls, r: RunErrorRecord) -> RunErrorOut:
        return cls(
            node=r.node_name, code=r.code, category=r.category.value, message=r.message, retryable=r.retryable
        )


class CheckpointOut(ApiOut):
    checkpoint_id: str | None
    step: int | None
    source: str | None
    next: list[str]
    created_at: str | None
    execution_status: str | None
    research_rounds: int | None
    tool_calls: int


class AgentHealthOut(ApiOut):
    status: str
    graph: dict[str, Any]
    tools: list[str]
    agent_mode: str
    agent_mode_reason: str | None
    capabilities: dict[str, Any] | None
    checkpointer: dict[str, Any]
    limits: dict[str, Any]


class LLMHealthOut(ApiOut):
    configured: bool
    provider: str | None = None
    model: str | None = None
    ok: bool | None = None
    latency_ms: int | None = None
    detail: str | None = None
    capabilities: dict[str, Any] | None = None

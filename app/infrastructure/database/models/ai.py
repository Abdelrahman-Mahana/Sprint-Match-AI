from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.exceptions import ErrorCategory
from app.domain.enums import (
    AgentMode,
    AvailabilityStatus,
    Criterion,
    RecommendationStatus,
    ToolExecutionStatus,
)
from app.infrastructure.database.models.base import Base, enum_column, uuid_pk


class TaskAnalysisModel(Base):
    __tablename__ = "task_analyses"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    analyzer_version: Mapped[str] = mapped_column(String(40))
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecommendationRunModel(Base):
    """One execution of the recommendation workflow.

    ``result_snapshot`` is the immutable API response as returned at generation time (audit artifact);
    candidates and score components are additionally normalized for querying and evaluation.
    """

    __tablename__ = "recommendation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[RecommendationStatus] = mapped_column(
        enum_column(RecommendationStatus, "recommendation_status")
    )
    requested_by: Mapped[str] = mapped_column(String(120))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    task_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    algorithm_version: Mapped[str] = mapped_column(String(40))
    scoring_config_fingerprint: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    llm_provider: Mapped[str | None] = mapped_column(String(40))
    llm_model: Mapped[str | None] = mapped_column(String(120))
    primary_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL")
    )
    data_coverage: Mapped[float | None] = mapped_column(Numeric(5, 4))
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_source: Mapped[str | None] = mapped_column(String(20))
    warnings: Mapped[list[str]] = mapped_column(ARRAY(String(80)), server_default="{}")
    revalidated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="SET NULL")
    )
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    execution_time_ms: Mapped[int] = mapped_column(Integer)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # --- agent execution metadata --------------------------------------------------------------------
    thread_id: Mapped[str | None] = mapped_column(String(80), index=True)  # LangGraph checkpoint thread
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    agent_mode: Mapped[AgentMode | None] = mapped_column(enum_column(AgentMode, "agent_mode"), nullable=True)
    research_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    llm_calls: Mapped[int] = mapped_column(Integer, server_default="0")
    llm_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    tool_calls_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_category: Mapped[ErrorCategory | None] = mapped_column(
        enum_column(ErrorCategory, "error_category"), nullable=True
    )
    resumable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    retry_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_recommendation_runs_company_task", "company_id", "task_id", "generated_at"),
        CheckConstraint("data_coverage IS NULL OR data_coverage BETWEEN 0 AND 1", name="coverage_range"),
        UniqueConstraint("company_id", "idempotency_key", name="uq_recommendation_runs_idempotency"),
        CheckConstraint("status <> 'FAILED' OR error_category IS NOT NULL", name="failed_has_category"),
        CheckConstraint("NOT resumable OR status = 'FAILED'", name="only_failed_resumable"),
    )


class RecommendationCandidateModel(Base):
    __tablename__ = "recommendation_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    suitability_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    eligible: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4))
    data_coverage: Mapped[float] = mapped_column(Numeric(5, 4))
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        enum_column(AvailabilityStatus, "availability_status")
    )
    available_capacity_hours: Mapped[float] = mapped_column(Numeric(7, 2))
    exclusion_reasons: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")

    __table_args__ = (
        CheckConstraint("rank IS NULL OR rank >= 1", name="rank_positive"),
        CheckConstraint("eligible OR rank IS NULL", name="only_eligible_ranked"),
    )


class RecommendationScoreComponentModel(Base):
    __tablename__ = "recommendation_score_components"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_candidates.id", ondelete="CASCADE"), index=True
    )
    criterion: Mapped[Criterion] = mapped_column(enum_column(Criterion, "criterion"))
    raw_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    score_used: Mapped[float] = mapped_column(Numeric(6, 2))
    weight: Mapped[float] = mapped_column(Numeric(5, 4))
    weighted_score: Mapped[float] = mapped_column(Numeric(6, 2))
    has_evidence: Mapped[bool] = mapped_column(Boolean)
    evidence_reference: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AgentExecutionLogModel(Base):
    __tablename__ = "agent_execution_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(SmallInteger)
    node_name: Mapped[str] = mapped_column(String(60))
    execution_status: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    sanitized_input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sanitized_output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    llm_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentToolExecutionModel(Base):
    """One tool call requested by the research agent and handled by the LangGraph ToolNode.

    Arguments and results are stored as sanitized summaries (identifiers, counts, statuses), never as full
    employee records.
    """

    __tablename__ = "agent_tool_executions"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(SmallInteger)
    research_round: Mapped[int] = mapped_column(SmallInteger)
    tool_call_id: Mapped[str] = mapped_column(String(80))
    tool_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[ToolExecutionStatus] = mapped_column(enum_column(ToolExecutionStatus, "tool_status"))
    arguments_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(60))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("recommendation_id", "sequence"),)


class AgentRunErrorModel(Base):
    __tablename__ = "agent_run_errors"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    node_name: Mapped[str | None] = mapped_column(String(60))
    code: Mapped[str] = mapped_column(String(60))
    category: Mapped[ErrorCategory] = mapped_column(enum_column(ErrorCategory, "error_category"))
    message: Mapped[str] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_events_entity", "entity_type", "entity_id"),)

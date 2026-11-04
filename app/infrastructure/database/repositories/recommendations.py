from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, select

from app.core.exceptions import ConflictError
from app.domain.interfaces import (
    ExecutionTraceEntry,
    RecommendationRunRecord,
    RunErrorRecord,
    StoredRecommendation,
    ToolExecutionRecord,
)
from app.infrastructure.database.models import (
    AgentExecutionLogModel,
    AgentRunErrorModel,
    AgentToolExecutionModel,
    AuditEventModel,
    RecommendationCandidateModel,
    RecommendationRunModel,
    RecommendationScoreComponentModel,
    TaskAnalysisModel,
)
from app.infrastructure.database.repositories.base import SqlRepository, translate_db_errors


class SqlRecommendationRepository(SqlRepository):
    _RUN_FIELDS = (
        "task_id",
        "request_id",
        "status",
        "requested_by",
        "as_of",
        "window_start",
        "window_end",
        "task_analysis",
        "algorithm_version",
        "scoring_config_fingerprint",
        "prompt_version",
        "llm_provider",
        "llm_model",
        "primary_employee_id",
        "data_coverage",
        "explanation",
        "explanation_source",
        "revalidated_from_id",
        "result_snapshot",
        "execution_time_ms",
        "error_details",
        "thread_id",
        "idempotency_key",
        "agent_mode",
        "research_summary",
        "llm_calls",
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "tool_calls_count",
        "error_category",
        "resumable",
        "retry_of_id",
    )

    def save_run(self, run: RecommendationRunRecord, request_payload: dict[str, Any]) -> None:
        values: dict[str, Any] = {f: getattr(run, f) for f in self._RUN_FIELDS}
        values["warnings"] = list(run.warnings)
        with translate_db_errors():
            existing = self._s.get(RecommendationRunModel, run.id)
            if existing is not None:
                if existing.company_id != run.company_id:
                    raise ConflictError("Recommendation id belongs to another company")
                # A resumed/retried run replaces its earlier (failed) record and children atomically.
                for model in (
                    RecommendationCandidateModel,
                    AgentExecutionLogModel,
                    AgentToolExecutionModel,
                    AgentRunErrorModel,
                ):
                    self._s.execute(delete(model).where(model.recommendation_id == run.id))
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.request_payload = request_payload
            else:
                self._s.add(
                    RecommendationRunModel(
                        id=run.id, company_id=run.company_id, request_payload=request_payload, **values
                    )
                )
            self._s.flush()
            self._save_children(run)
            self._s.flush()

    def _save_children(self, run: RecommendationRunRecord) -> None:
        for cand in run.candidates:
            cm = RecommendationCandidateModel(
                recommendation_id=run.id,
                employee_id=cand.employee_id,
                rank=cand.rank,
                suitability_score=cand.suitability_score,
                eligible=cand.eligible,
                confidence=cand.confidence,
                data_coverage=cand.data_coverage,
                availability_status=cand.availability_status,
                available_capacity_hours=cand.available_capacity_hours,
                exclusion_reasons=[r.code.value for r in cand.exclusion_reasons],
            )
            self._s.add(cm)
            self._s.flush()
            for c in cand.criteria:
                self._s.add(
                    RecommendationScoreComponentModel(
                        candidate_id=cm.id,
                        criterion=c.criterion,
                        raw_score=c.raw_score,
                        score_used=c.score_used,
                        weight=c.weight,
                        weighted_score=round(c.weighted_score, 2),
                        has_evidence=c.has_evidence,
                        evidence_reference={"evidence": list(c.evidence), "details": c.details},
                    )
                )
        for seq, t in enumerate(run.trace, start=1):
            self._s.add(
                AgentExecutionLogModel(
                    recommendation_id=run.id,
                    sequence=seq,
                    node_name=t.node_name,
                    execution_status=t.execution_status,
                    duration_ms=t.duration_ms,
                    attempts=t.attempts,
                    sanitized_input_summary=t.input_summary,
                    sanitized_output_summary=t.output_summary,
                    error_message=t.error_message,
                    started_at=t.started_at,
                    finished_at=t.finished_at,
                    llm_prompt_tokens=t.llm_prompt_tokens,
                    llm_completion_tokens=t.llm_completion_tokens,
                )
            )
        for te in run.tool_executions:
            self._s.add(
                AgentToolExecutionModel(
                    recommendation_id=run.id,
                    sequence=te.sequence,
                    research_round=te.research_round,
                    tool_call_id=te.tool_call_id[:80],
                    tool_name=te.tool_name[:80],
                    status=te.status,
                    arguments_summary=te.arguments_summary,
                    result_summary=te.result_summary,
                    error_code=te.error_code,
                    duration_ms=te.duration_ms,
                )
            )
        for err in run.errors:
            self._s.add(
                AgentRunErrorModel(
                    recommendation_id=run.id,
                    node_name=err.node_name,
                    code=err.code[:60],
                    category=err.category,
                    message=err.message,
                    retryable=err.retryable,
                )
            )

    @staticmethod
    def _stored(m: RecommendationRunModel) -> StoredRecommendation:
        return StoredRecommendation(
            id=m.id,
            company_id=m.company_id,
            status=m.status,
            task_id=m.task_id,
            generated_at=m.generated_at,
            result_snapshot=m.result_snapshot,
            request_payload=m.request_payload,
            request_id=m.request_id,
            requested_by=m.requested_by,
            updated_at=m.updated_at,
            execution_time_ms=m.execution_time_ms,
            thread_id=m.thread_id,
            idempotency_key=m.idempotency_key,
            agent_mode=m.agent_mode,
            llm_provider=m.llm_provider,
            llm_model=m.llm_model,
            llm_calls=m.llm_calls,
            llm_prompt_tokens=m.llm_prompt_tokens,
            llm_completion_tokens=m.llm_completion_tokens,
            tool_calls_count=m.tool_calls_count,
            error_category=m.error_category,
            resumable=m.resumable,
            retry_of_id=m.retry_of_id,
        )

    def get_run(self, company_id: UUID, recommendation_id: UUID) -> StoredRecommendation | None:
        m = self._execute(
            select(RecommendationRunModel).where(
                RecommendationRunModel.id == recommendation_id,
                RecommendationRunModel.company_id == company_id,
            )
        ).scalar_one_or_none()
        return self._stored(m) if m else None

    def find_by_idempotency_key(self, company_id: UUID, key: str) -> StoredRecommendation | None:
        m = self._execute(
            select(RecommendationRunModel).where(
                RecommendationRunModel.company_id == company_id,
                RecommendationRunModel.idempotency_key == key,
            )
        ).scalar_one_or_none()
        return self._stored(m) if m else None

    @staticmethod
    def _owned(company_id: UUID, recommendation_id: UUID) -> Select[UUID]:
        return select(RecommendationRunModel.id).where(
            RecommendationRunModel.id == recommendation_id, RecommendationRunModel.company_id == company_id
        )

    def list_node_logs(self, company_id: UUID, recommendation_id: UUID) -> list[ExecutionTraceEntry]:
        rows = self._execute(
            select(AgentExecutionLogModel)
            .where(AgentExecutionLogModel.recommendation_id.in_(self._owned(company_id, recommendation_id)))
            .order_by(AgentExecutionLogModel.sequence)
        ).scalars()
        return [
            ExecutionTraceEntry(
                node_name=m.node_name,
                execution_status=m.execution_status,
                duration_ms=m.duration_ms,
                attempts=m.attempts,
                input_summary=m.sanitized_input_summary,
                output_summary=m.sanitized_output_summary,
                error_message=m.error_message,
                started_at=m.started_at,
                finished_at=m.finished_at,
                llm_prompt_tokens=m.llm_prompt_tokens,
                llm_completion_tokens=m.llm_completion_tokens,
            )
            for m in rows
        ]

    def list_tool_executions(self, company_id: UUID, recommendation_id: UUID) -> list[ToolExecutionRecord]:
        rows = self._execute(
            select(AgentToolExecutionModel)
            .where(AgentToolExecutionModel.recommendation_id.in_(self._owned(company_id, recommendation_id)))
            .order_by(AgentToolExecutionModel.sequence)
        ).scalars()
        return [
            ToolExecutionRecord(
                sequence=m.sequence,
                research_round=m.research_round,
                tool_call_id=m.tool_call_id,
                tool_name=m.tool_name,
                status=m.status,
                arguments_summary=m.arguments_summary,
                result_summary=m.result_summary,
                error_code=m.error_code,
                duration_ms=m.duration_ms,
            )
            for m in rows
        ]

    def list_run_errors(self, company_id: UUID, recommendation_id: UUID) -> list[RunErrorRecord]:
        rows = self._execute(
            select(AgentRunErrorModel)
            .where(AgentRunErrorModel.recommendation_id.in_(self._owned(company_id, recommendation_id)))
            .order_by(AgentRunErrorModel.created_at, AgentRunErrorModel.code)
        ).scalars()
        return [
            RunErrorRecord(
                node_name=m.node_name,
                code=m.code,
                category=m.category,
                message=m.message,
                retryable=m.retryable,
            )
            for m in rows
        ]

    def list_run_ids_before(self, cutoff: datetime) -> list[UUID]:
        return list(
            self._execute(
                select(RecommendationRunModel.id).where(RecommendationRunModel.updated_at < cutoff)
            ).scalars()
        )

    def save_task_analysis(
        self, company_id: UUID, task_id: UUID | None, analysis: dict[str, Any], analyzer_version: str
    ) -> UUID:
        m = TaskAnalysisModel(
            company_id=company_id, task_id=task_id, analysis=analysis, analyzer_version=analyzer_version
        )
        with translate_db_errors():
            self._s.add(m)
            self._s.flush()
        return m.id


class SqlAuditRepository(SqlRepository):
    def record(
        self,
        company_id: UUID | None,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with translate_db_errors():
            self._s.add(
                AuditEventModel(
                    company_id=company_id,
                    actor=actor,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    event_metadata=metadata or {},
                )
            )
            self._s.flush()

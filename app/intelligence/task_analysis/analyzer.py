"""Task analysis: turns task input into validated ``TaskRequirements``.

Precedence for every field: manager/task record  >  validated LLM suggestion  >  deterministic default.
Critical information (at least one resolvable skill requirement, an effort estimate) is never invented:
when missing, the analysis returns NEEDS_INPUT (or PROVIDER_UNAVAILABLE when the LLM was needed but failed).
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities.employee import Skill
from app.domain.entities.task import RequiredSkill, TaskAnalysis, TaskInput, TaskRequirements
from app.domain.enums import RequirementSource, TaskCategory, TaskComplexity, TaskPriority
from app.domain.policies import ScoringConfig
from app.infrastructure.llm.base import LLMError, LLMProvider, LLMUnavailableError
from app.intelligence.prompts import task_analysis_messages
from app.intelligence.task_analysis import text as text_norm
from app.intelligence.task_analysis.injection import split_instruction_like

logger = logging.getLogger(__name__)

ANALYZER_VERSION = "task-analyzer-1.1.0"
TEXT_MATCH_CONFIDENCE = 0.6


class LLMSkillSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    skill_name: str = Field(min_length=1, max_length=100)
    minimum_level: int = Field(ge=0, le=100)
    mandatory: bool
    evidence_quote: str = Field(default="", max_length=500)


class LLMTaskAnalysisOutput(BaseModel):
    """Schema the LLM must satisfy. Malformed output is rejected (one repair attempt, then failure)."""

    model_config = ConfigDict(extra="ignore")
    task_category: TaskCategory | None = None
    required_skills: list[LLMSkillSuggestion] = Field(default_factory=list, max_length=12)
    complexity: TaskComplexity | None = None
    estimated_hours: float | None = Field(default=None, gt=0, le=500)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    analysis_confidence: float = Field(ge=0, le=1)


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w\u0600-\u06FF]){re.escape(term)}(?![\w\u0600-\u06FF])", re.IGNORECASE)


class SkillCatalogIndex:
    """Resolves catalog skills against free text: exact names/aliases first, then the fuzzy layer of
    ``text.py`` (case/separator folding, common-English/Arabic hints, conservative typo matching)."""

    def __init__(self, catalog: list[Skill]) -> None:
        self.catalog = catalog
        self._by_term: dict[str, Skill] = {}
        for s in catalog:
            for term in (s.name, *s.aliases):
                self._by_term[term.lower()] = s
                for variant in text_norm.variants(term):
                    self._by_term.setdefault(variant, s)

    def resolve(self, name: str) -> Skill | None:
        exact = self._by_term.get(name.strip().lower())
        if exact is not None:
            return exact
        for variant in text_norm.variants(name):
            if variant in self._by_term:
                return self._by_term[variant]
        # Typo tolerance against real catalog names only (aliases are often short abbreviations).
        for skill in self.catalog:
            if text_norm.fuzzy_equals(text_norm.normalize(skill.name).replace(" ", ""),
                                       text_norm.normalize(name).replace(" ", "")):
                return skill
        # Substring containment for compound or versioned skill names (e.g. Microservices vs Microservices Architecture)
        norm_name = text_norm.normalize(name).replace(" ", "")
        for skill in self.catalog:
            norm_skill = text_norm.normalize(skill.name).replace(" ", "")
            if len(norm_name) >= 4 and len(norm_skill) >= 4:
                if norm_name in norm_skill or norm_skill in norm_name:
                    return skill
        return None

    def mentioned_in(self, text: str) -> list[Skill]:
        found = [s for s in self.catalog if self.is_mentioned(s, text)]
        for hint in text_norm.hinted_skill_names(text):
            skill = self.resolve(hint)
            if skill is not None and skill not in found:
                found.append(skill)
        return found

    def is_mentioned(self, skill: Skill, text: str) -> bool:
        return any(text_norm.text_mentions(t, text) for t in (skill.name, *skill.aliases))


class TaskAnalyzer:
    def __init__(
        self, config: ScoringConfig, llm: LLMProvider | None = None, *, accept_llm_estimates: bool = False
    ) -> None:
        self.config = config
        self.llm = llm
        self.accept_llm_estimates = accept_llm_estimates

    def analyze(self, task: TaskInput, catalog: list[Skill]) -> TaskAnalysis:
        index = SkillCatalogIndex(catalog)
        text = task.text_corpus()
        warnings: list[str] = []
        # Grounding and keyword matching only ever use text that describes the work.
        clean_text, flagged = split_instruction_like(text)
        if flagged:
            warnings.append("POSSIBLE_PROMPT_INJECTION")
        missing: list[str] = []
        suggestions: dict[str, object] = {}
        llm_out: LLMTaskAnalysisOutput | None = None
        llm_error: str | None = None
        llm_unavailable = False
        confidence_parts: list[float] = []

        # --- skills --------------------------------------------------------------------------------
        skills: list[RequiredSkill] = []
        if task.required_skills:
            for req in task.required_skills:
                skill = index.resolve(req.skill_name)
                if skill is None:
                    missing.append(f"UNKNOWN_SKILL:{req.skill_name}")
                    continue
                skills.append(
                    RequiredSkill(
                        skill_id=skill.id,
                        skill_name=skill.name,
                        minimum_level=req.minimum_level,
                        mandatory=req.mandatory,
                        requires_verification=req.requires_verification,
                        weight=req.weight or self._default_weight(req.mandatory),
                        source=task.requirements_source,
                    )
                )
            confidence_parts.append(1.0)

        needs_llm = not task.required_skills or task.complexity is None or task.estimated_hours is None
        if needs_llm and self.llm is not None:
            try:
                llm_out = self._call_llm(text, catalog)
            except LLMUnavailableError as exc:
                llm_error, llm_unavailable = exc.code, True
                warnings.append("LLM_UNAVAILABLE_FALLBACK_USED")
            except LLMError as exc:
                llm_error = exc.code
                warnings.append(f"LLM_ANALYSIS_REJECTED:{exc.code}")

        if not task.required_skills:
            if llm_out is not None:
                skills = self._skills_from_llm(llm_out, index, clean_text, warnings, flagged=bool(flagged))
                if skills:
                    warnings.append("REQUIREMENTS_SUGGESTED_BY_LLM")
                    confidence_parts.append(min(llm_out.analysis_confidence, 0.9))
                    suggestions["llm_missing_information"] = llm_out.missing_information
            if not skills:
                skills = self._skills_from_text(index, clean_text, task.complexity)
                if skills:
                    warnings.append("REQUIREMENTS_INFERRED_FROM_TEXT")
                    confidence_parts.append(TEXT_MATCH_CONFIDENCE)
            if not skills:
                skills = self._skills_from_semantic_search(catalog, clean_text, task.complexity, warnings)
                if skills:
                    warnings.append("REQUIREMENTS_INFERRED_FROM_SEMANTIC_SEARCH")
                    confidence_parts.append(0.65)
            if not skills:
                skills = self._skills_from_domain_context(index, clean_text, task.complexity)
                if skills:
                    warnings.append("REQUIREMENTS_INFERRED_FROM_DOMAIN")
                    confidence_parts.append(0.5)
        if not skills:
            missing.append("required_skills")

        # --- complexity --------------------------------------------------------------------------
        if task.complexity is not None:
            complexity, complexity_source = task.complexity, task.requirements_source
            confidence_parts.append(1.0)
        elif llm_out is not None and llm_out.complexity is not None:
            complexity, complexity_source = llm_out.complexity, RequirementSource.LLM_SUGGESTED
            confidence_parts.append(0.8)
        else:
            complexity, complexity_source = TaskComplexity.MEDIUM, RequirementSource.DEFAULT
            warnings.append("COMPLEXITY_DEFAULTED")
            confidence_parts.append(0.5)

        # --- effort ----------------------------------------------------------------------------------
        hours: float | None = task.estimated_hours
        hours_source = task.requirements_source
        if hours is None:
            suggested = llm_out.estimated_hours if llm_out else None
            if suggested is not None:
                suggestions["estimated_hours"] = suggested
                if self.accept_llm_estimates:
                    hours, hours_source = suggested, RequirementSource.LLM_SUGGESTED
                    warnings.append("ESTIMATE_SUGGESTED_BY_LLM")
            if hours is None:
                missing.append("estimated_hours")

        category = task.task_category or (llm_out.task_category if llm_out else None)
        if category is None:
            category = TaskCategory.OTHER
            warnings.append("CATEGORY_DEFAULTED")

        critical_missing = [
            m for m in missing if m in ("required_skills", "estimated_hours") or m.startswith("UNKNOWN_SKILL")
        ]
        confidence = round(sum(confidence_parts) / len(confidence_parts), 4) if confidence_parts else 0.0
        if critical_missing:
            status = (
                "PROVIDER_UNAVAILABLE"
                if llm_unavailable and "required_skills" in critical_missing and not task.required_skills
                else "NEEDS_INPUT"
            )
            return TaskAnalysis(
                analyzer_version=ANALYZER_VERSION,
                status=status,
                requirements=None,
                missing_information=tuple(dict.fromkeys(missing)),
                warnings=tuple(warnings),
                suggestions=suggestions,
                analysis_confidence=confidence,
                llm_used=llm_out is not None,
                llm_error=llm_error,
            )
        assert hours is not None
        requirements = TaskRequirements(
            task_id=task.task_id,
            title=task.title,
            task_category=category,
            required_skills=tuple(skills),
            complexity=complexity,
            complexity_source=complexity_source,
            estimated_hours=hours,
            estimated_hours_source=hours_source,
            priority=task.priority or TaskPriority.MEDIUM,
            project_id=task.project_id,
            restricted_to_team_id=task.restricted_to_team_id,
            requires_project_membership=task.requires_project_membership,
            required_certifications=task.required_certifications,
        )
        return TaskAnalysis(
            analyzer_version=ANALYZER_VERSION,
            status="READY",
            requirements=requirements,
            missing_information=tuple(dict.fromkeys(missing)),
            warnings=tuple(warnings),
            suggestions=suggestions,
            analysis_confidence=confidence,
            llm_used=llm_out is not None,
            llm_error=llm_error,
        )

    # ------------------------------------------------------------------------------------------
    def _default_weight(self, mandatory: bool) -> float:
        p = self.config.skills
        return p.mandatory_default_weight if mandatory else p.optional_default_weight

    def _call_llm(self, text: str, catalog: list[Skill]) -> LLMTaskAnalysisOutput:
        assert self.llm is not None
        messages = task_analysis_messages(text, [s.name for s in catalog])
        return self.llm.generate_structured(
            messages, LLMTaskAnalysisOutput, schema_name="task_analysis"
        ).value

    def _skills_from_llm(
        self,
        out: LLMTaskAnalysisOutput,
        index: SkillCatalogIndex,
        text: str,
        warnings: list[str],
        *,
        flagged: bool = False,
    ) -> list[RequiredSkill]:
        """``text`` is the task text without instruction-like sentences (see ``injection``)."""
        result: dict[str, RequiredSkill] = {}
        for s in out.required_skills:
            skill = index.resolve(s.skill_name)
            if skill is None:
                warnings.append(f"LLM_SKILL_NOT_IN_CATALOG:{s.skill_name[:40]}")
                continue
            if flagged and not index.is_mentioned(skill, text):
                # Only named inside an instruction-like sentence: never a requirement.
                warnings.append(f"LLM_SKILL_IGNORED_UNTRUSTED_INSTRUCTION:{skill.name}")
                continue
            mandatory = s.mandatory
            # Mandatory only with a verbatim supporting quote from the task text that names the skill.
            quote = s.evidence_quote.strip()
            grounded = len(quote) >= 3 and quote.lower() in text.lower() and index.is_mentioned(skill, quote)
            if mandatory and not grounded:
                mandatory = False
                warnings.append(f"LLM_MANDATORY_SKILL_DOWNGRADED:{skill.name}")
            result.setdefault(
                skill.name,
                RequiredSkill(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    minimum_level=s.minimum_level,
                    mandatory=mandatory,
                    weight=self._default_weight(mandatory),
                    source=RequirementSource.LLM_SUGGESTED,
                ),
            )

        # Architectural Guardrail: Intelligent Skill Tiering
        # Prevents "all-mandatory bottleneck" where 3+ technologies are marked mandatory,
        # which mathematically disqualifies 100% of employees when dealing with full-stack tasks.
        mandatory_items = [r for r in result.values() if r.mandatory]
        if len(mandatory_items) > 2:
            core_categories = {"language", "backend_framework", "frontend_framework", "mobile"}

            def sort_key(req: RequiredSkill) -> tuple[int, int]:
                s_obj = index.resolve(req.skill_name)
                cat = getattr(s_obj, "category", "") if s_obj else ""
                is_core = 0 if cat in core_categories else 1
                return (is_core, -req.minimum_level)

            sorted_mandatory = sorted(mandatory_items, key=sort_key)
            # Retain the top 2 as mandatory; adapt remaining into high-weight preferred skills
            for extra in sorted_mandatory[2:]:
                result[extra.skill_name] = extra.model_copy(
                    update={
                        "mandatory": False,
                        "weight": self.config.skills.mandatory_default_weight,
                    }
                )
                warnings.append(f"ADAPTIVE_SKILL_TIERING:{extra.skill_name}_ADAPTED_TO_PREFERRED")

        return list(result.values())

    def _skills_from_text(
        self, index: SkillCatalogIndex, text: str, complexity: TaskComplexity | None
    ) -> list[RequiredSkill]:
        level = self.config.default_min_level_by_complexity[complexity or TaskComplexity.MEDIUM]
        return [
            RequiredSkill(
                skill_id=s.id,
                skill_name=s.name,
                minimum_level=level,
                mandatory=False,
                weight=self._default_weight(False),
                source=RequirementSource.TEXT_MATCH,
            )
            for s in index.mentioned_in(text)
        ]

    def _skills_from_semantic_search(
        self, catalog: list[Skill], text: str, complexity: TaskComplexity | None, warnings: list[str]
    ) -> list[RequiredSkill]:
        try:
            from app.intelligence.task_analysis.semantic import SemanticSkillMatcher

            matcher = SemanticSkillMatcher.get_instance()
            matches = matcher.search_skills(text, catalog, top_k=4)
            if not matches:
                return []

            level = self.config.default_min_level_by_complexity[complexity or TaskComplexity.MEDIUM]
            return [
                RequiredSkill(
                    skill_id=s.id,
                    skill_name=s.name,
                    minimum_level=level,
                    mandatory=False,
                    weight=round(min(1.0, max(0.4, score)), 2),
                    source=RequirementSource.TEXT_MATCH,
                )
                for s, score in matches
            ]
        except Exception as exc:
            logger.warning("Semantic vector search error: %s", exc)
            return []

    def _skills_from_domain_context(
        self, index: SkillCatalogIndex, text: str, complexity: TaskComplexity | None
    ) -> list[RequiredSkill]:
        domain_skills = text_norm.infer_domain_skills(text)
        if not domain_skills:
            return []
        resolved: list[Skill] = []
        for name in domain_skills:
            s = index.resolve(name)
            if s is not None and s not in resolved:
                resolved.append(s)
        if not resolved:
            return []
        level = self.config.default_min_level_by_complexity[complexity or TaskComplexity.MEDIUM]
        return [
            RequiredSkill(
                skill_id=s.id,
                skill_name=s.name,
                minimum_level=level,
                mandatory=False,
                weight=self._default_weight(False),
                source=RequirementSource.TEXT_MATCH,
            )
            for s in resolved
        ]

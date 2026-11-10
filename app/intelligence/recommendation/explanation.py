"""Evidence-grounded explanations.

The deterministic explanation is built exclusively from the structured engine result and is always available.
An optional LLM rewording receives only that structured evidence and is accepted only if it passes a grounding
check (it must name the recommended employee and may only use numbers present in the evidence).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from app.domain.entities.recommendation import CandidateEvaluation, EngineResult
from app.domain.entities.task import TaskRequirements
from app.domain.enums import Criterion, RecommendationStatus
from app.infrastructure.llm.base import LLMError, LLMProvider
from app.intelligence.prompts import explanation_messages

_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")

_CRITERION_LABEL = {
    Criterion.SKILL_MATCH: "Skills",
    Criterion.EXPERIENCE: "Relevant experience",
    Criterion.QUALITY: "Historical quality",
    Criterion.RELIABILITY: "Delivery reliability",
    Criterion.CAPACITY: "Available capacity",
    Criterion.COMPLEXITY_FIT: "Complexity fit",
    Criterion.PROJECT_FAMILIARITY: "Project familiarity",
}

_CRITERION_LABEL_AR = {
    Criterion.SKILL_MATCH: "المهارات والتقنيات المطلوبة",
    Criterion.EXPERIENCE: "الخبرة السابقة والمهام المماثلة",
    Criterion.QUALITY: "جودة العمل والتسليمات السابقة",
    Criterion.RELIABILITY: "الالتزام بمواعيد التسليم والموثوقية",
    Criterion.CAPACITY: "الساعات المتاحة وعبء العمل في الـ Sprint",
    Criterion.COMPLEXITY_FIT: "ملاءمة مستوى الأقدمية والصعوبة",
    Criterion.PROJECT_FAMILIARITY: "الإلمام بالمشروع وفريق العمل",
}

_EXCLUSION_CODE_AR = {
    "INSUFFICIENT_AVAILABLE_CAPACITY": "عدم توفر ساعات عمل كافية في الـ Sprint",
    "NO_REQUIRED_SKILL_COVERAGE": "عدم تغطية المهارات الأساسية المطلوبة",
    "ON_LEAVE_DURING_WINDOW": "الموظف في إجازة رسمية خلال فترة الـ Sprint",
    "MISSING_MANDATORY_SKILL": "نقص مهارة إلزامية مطلوبة للمهمة",
    "INACTIVE_EMPLOYEE": "الموظف غير نشط حالياً في الشركة",
    "OUTSIDE_DEPARTMENT": "الموظف ليس ضمن القسم المخصص للمهمة",
    "OVERLAPPING_COMMITTED_TASKS": "تضارب مع مهام حرجة أخرى ملتزم بها الموظف",
}

_AVAILABILITY_STATUS_AR = {
    "AVAILABLE": "متاح بالكامل (Available)",
    "PARTIALLY_AVAILABLE": "متاح جزئياً (Partially Available)",
    "BUSY": "مشغول بكامل طاقته (Busy)",
    "ON_LEAVE": "في إجازة رسمية (On Leave)",
    "UNKNOWN": "غير محدد جدول العمل",
}

_CONFIDENCE_AR = {
    "HIGH": "عالية",
    "MEDIUM": "متوسطة",
    "LOW": "منخفضة",
}


def is_arabic(text: str | None) -> bool:
    """Returns True if the string contains Arabic characters."""
    if not text:
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def translate_evidence_ar(text: str) -> str:
    """Translates individual criteria evidence metrics into clear, professional Arabic."""
    # 1. Delivery reliability
    m = re.match(r"On time in (\d+(?:\.\d+)?%) of (\d+) attributable deliveries", text)
    if m:
        return f"الالتزام التام بمواعيد التسليم بنسبة {m.group(1)} من إجمالي {m.group(2)} تسليم(ات) منسوبة للمطور"
    m = re.match(r"(\d+) delay\(s\) caused by external blockers/dependencies excluded", text)
    if m:
        return f"تم استبعاد {m.group(1)} تأخير(ات) ناتجة عن معوقات أو اعتمادات خارجية"
    if text.startswith("No delivery history"):
        return "لا يوجد سجل تسليمات سابقة للمطور"

    # 2. Experience
    m = re.match(r"(\d+) completed similar task\(s\) out of (\d+) reviewed", text)
    if m:
        return f"أنجز {m.group(1)} مهمة/مهام مماثلة بنجاح من أصل {m.group(2)} مهمة تمت مراجعتها"
    m = re.match(r"(\d+(?:\.\d+)?) year\(s\) of experience with the required technologies", text)
    if m:
        return f"خبرة عملية موثقة تبلغ {m.group(1)} سنة/سنوات في التقنيات المطلوبة"
    if text == "No task history or experience records available":
        return "لا يوجد سجل مهام سابقة أو خبرات مسجلة في ملف الموظف"

    # 3. Quality
    m = re.match(r"Mean reviewed quality (\d+(?:\.\d+)?) over (\d+) outcome\(s\)(.*)", text)
    if m:
        return f"متوسط جودة المراجعة الفنية {m.group(1)} من 100 عبر {m.group(2)} تسليم(ات) سابقة"
    m = re.match(r"Acceptance rate (\d+(?:\.\d+)?%), average rework (\d+(?:\.\d+)?)", text)
    if m:
        return f"معدل قبول التسليمات من أول مرة {m.group(1)}، ومتوسط طلبات إعادة العمل {m.group(2)}"
    if text == "No reviewed task outcomes with a quality score":
        return "لا توجد تسليمات سابقة خضعت لتقييم الجودة البرمجية"

    # 4. Capacity
    m = re.match(r"(\d+(?:\.\d+)?)h free of (\d+(?:\.\d+)?)h working time in window for a (\d+(?:\.\d+)?)h task", text)
    if m:
        return f"سعة عمل متاحة تبلغ {m.group(1)} ساعة من أصل {m.group(2)} ساعة عمل في نافذة الـ Sprint لمهمة تتطلب {m.group(3)} ساعة"
    m = re.match(r"Workload estimate is (.+)", text)
    if m:
        val = m.group(1).lower()
        ar_val = {"certain": "مؤكد", "approximate": "تقريبي", "unknown": "غير محدد"}.get(val, val)
        return f"تقدير عبء العمل: {ar_val}"

    # 5. Complexity fit
    m = re.match(r"Task complexity (\w+); capability level (\w+)", text)
    if m:
        comp_ar = {"low": "منخفض", "medium": "متوسط", "high": "مرتفع", "critical": "حرج"}.get(m.group(1).lower(), m.group(1))
        cap_ar = {"low": "مبتدئ", "medium": "متوسط", "high": "متقدم", "critical": "خبير"}.get(m.group(2).lower(), m.group(2))
        return f"صعوبة المهمة: {comp_ar}؛ ومستوى قدرة المطور المثبتة: {cap_ar}"
    m = re.match(r"Has delivered (\w+)-complexity work with good quality", text)
    if m:
        comp_ar = {"low": "منخفضة التعقيد", "medium": "متوسطة التعقيد", "high": "عالية التعقيد"}.get(m.group(1).lower(), m.group(1))
        return f"أنجز بنجاح أعمالاً {comp_ar} بجودة برمجية عالية معتمدة"
    if text == "No seniority or complexity history":
        return "لا توجد بيانات سابقة عن مستوى الأقدمية أو سوابق التعقيد"

    # 6. Project familiarity
    m = re.match(r"(\d+) completed task\(s\) on this project(, current project member)?", text)
    if m:
        extra = " (وهو عضو نشط في فريق المشروع حالياً)" if m.group(2) else ""
        return f"أنجز {m.group(1)} مهمة/مهام سابقة في هذا المشروع{extra}"
    if text == "Task has no project":
        return "المهمة غير مرتبطة بمشروع محدد"

    # 7. Skills
    m = re.match(r"(.+): declared (\d+), (\w+)(?:, last assessed (\d+) months ago)?(?:, effective ([\d.]+))? \(required (\d+), (\w+)\)", text)
    if m:
        skill = m.group(1)
        decl = m.group(2)
        v_status = {"verified": "مُعتمد ومُوثق", "self_declared": "مُعلن ذاتياً"}.get(m.group(3), m.group(3))
        req_level = m.group(6)
        kind = {"mandatory": "إلزامي", "optional": "اختياري"}.get(m.group(7), m.group(7))
        eff = f"، والفعلي {m.group(5)}" if m.group(5) else ""
        return f"{skill}: المستوى المُعلن {decl} ({v_status}{eff}) [المطلوب: {req_level}، {kind}]"

    m = re.match(r"(.+): not in profile \(required (\d+), (\w+)\)", text)
    if m:
        skill = m.group(1)
        req_level = m.group(2)
        kind = {"mandatory": "إلزامي", "optional": "اختياري"}.get(m.group(3), m.group(3))
        return f"{skill}: غير مسجلة في ملف الموظف [المطلوب: {req_level}، {kind}]"

    return text


def _reason_summary(excluded: tuple[CandidateEvaluation, ...]) -> str:
    # Number of employees per reason (an employee missing two skills counts once).
    counts = Counter(
        code for e in excluded for code in dict.fromkeys(r.code.value for r in e.exclusion_reasons)
    )
    return ", ".join(f"{code} ({n})" for code, n in counts.most_common())


def _reason_summary_ar(excluded: tuple[CandidateEvaluation, ...]) -> str:
    counts = Counter(
        code for e in excluded for code in dict.fromkeys(r.code.value for r in e.exclusion_reasons)
    )
    return ", ".join(f"{_EXCLUSION_CODE_AR.get(code, code)} ({n})" for code, n in counts.most_common())


def _deterministic_explanation_en(requirements: TaskRequirements, result: EngineResult) -> str:
    primary = result.primary
    lines: list[str] = []
    if primary is None:
        lines.append(f'No employee satisfies all hard constraints for "{requirements.title}".')
        if result.excluded:
            lines.append(f"- Exclusion reasons: {_reason_summary(result.excluded)}.")
        if "DEADLINE_INFEASIBLE" in result.warnings:
            lines.append(
                f"- The task needs {requirements.estimated_hours:g}h but no employee has that much working time in "
                "the execution window; extend the deadline or split the task."
            )
        for w in result.warnings:
            if w.startswith("NEAREST_MATCH_INSIGHT:"):
                insight = w.split(":", 1)[1]
                lines.append(f"- Smart Advisory: Nearest potential candidate is {insight}.")
        lines.append("- No employee was forced into a recommendation.")
        return "\n".join(lines)

    lines.append(
        f'{primary.full_name} ({primary.employee_code}) is recommended for "{requirements.title}" with a '
        f"suitability score of {primary.suitability_score:.1f}/100 "
        f"(confidence {primary.confidence_level.value.lower()}, data coverage {primary.data_coverage:.0%})."
    )
    if requirements.mandatory_skills:
        lines.append("- Satisfies every mandatory skill requirement.")
    for c in primary.criteria:
        if not c.applicable or not c.evidence:
            continue
        label = _CRITERION_LABEL[c.criterion]
        suffix = "" if c.has_evidence else " (no evidence; neutral default used)"
        lines.append(f"- {label}: {'; '.join(c.evidence)}{suffix}.")
    lines.append(f"- Availability status in the execution window: {primary.availability_status.value}.")
    if len(result.ranked) > 1:
        runner = result.ranked[1]
        lines.append(
            f"- Next best eligible candidate: {runner.full_name} ({runner.suitability_score:.1f}/100)."
            + (
                " Scores are close; manager judgement is advised."
                if "CLOSE_CANDIDATES" in result.warnings
                else ""
            )
        )
    if result.excluded:
        lines.append(f"- {len(result.excluded)} employee(s) excluded: {_reason_summary(result.excluded)}.")
    if primary.missing_information:
        lines.append(f"- Data gaps: {', '.join(primary.missing_information)}.")
    lines.append("- This is a recommendation only; the project manager decides the assignment.")
    return "\n".join(lines)


def _deterministic_explanation_ar(requirements: TaskRequirements, result: EngineResult) -> str:
    primary = result.primary
    lines: list[str] = []
    if primary is None:
        lines.append(f'لا يوجد موظف يستوفي كافة الشروط الإلزامية لمهمة "{requirements.title}".')
        if result.excluded:
            lines.append(f"- أسباب الاستبعاد الرئيسية: {_reason_summary_ar(result.excluded)}.")
        if "DEADLINE_INFEASIBLE" in result.warnings:
            lines.append(
                f"- تتطلب المهمة {requirements.estimated_hours:g} ساعة عمل، ولا يتوفر أي مطور لديه هذه الساعات ضمن نافذة الـ Sprint المحددة؛ يُرجى تمديد المهلة أو تجزئة المهمة."
            )
        for w in result.warnings:
            if w.startswith("NEAREST_MATCH_INSIGHT:"):
                insight = w.split(":", 1)[1]
                lines.append(f"- استشارة ذكية للمدير: أقرب مرشح بديل مناسب هو {insight}.")
        lines.append("- لم يتم فرض أي مرشح غير مؤهل حفاظاً على معايير الجودة والتسليم.")
        return "\n".join(lines)

    conf_label = _CONFIDENCE_AR.get(primary.confidence_level.value, primary.confidence_level.value)
    lines.append(
        f'تم ترشيح المطور {primary.full_name} ({primary.employee_code}) لتنفيذ مهمة "{requirements.title}" '
        f"بنسبة ملاءمة وتوافق بلغت {primary.suitability_score:.1f}/100 "
        f"(مستوى الثقة: {conf_label}، اكتمال بيانات التقييم: {primary.data_coverage:.0%})."
    )
    if requirements.mandatory_skills:
        lines.append("- يستوفي جميع المهارات الإلزامية المطلوبة للمهمة بنجاح.")
    for c in primary.criteria:
        if not c.applicable or not c.evidence:
            continue
        label = _CRITERION_LABEL_AR.get(c.criterion, c.criterion.value)
        translated_items = [translate_evidence_ar(ev) for ev in c.evidence]
        suffix = "" if c.has_evidence else " (لا توجد بيانات سابقة؛ تم استخدام التقييم المحايد الافتراضي)"
        lines.append(f"- {label}: {'; '.join(translated_items)}{suffix}.")
    avail_status = _AVAILABILITY_STATUS_AR.get(primary.availability_status.value, primary.availability_status.value)
    lines.append(f"- حالة التوفر في فترة التنفيذ: {avail_status}.")
    if len(result.ranked) > 1:
        runner = result.ranked[1]
        lines.append(
            f"- البديل الأنسب التالي: {runner.full_name} (بنسبة توافق {runner.suitability_score:.1f}/100)."
            + (
                " الفارق بين المرشحين متقارب؛ يُنصح بتقدير مدير المشروع."
                if "CLOSE_CANDIDATES" in result.warnings
                else ""
            )
        )
    if result.excluded:
        lines.append(f"- تم استبعاد {len(result.excluded)} موظف(ين): {_reason_summary_ar(result.excluded)}.")
    if primary.missing_information:
        lines.append(f"- فجوات البيانات الإحصائية: {', '.join(primary.missing_information)}.")
    lines.append("- هذا التقرير استرشادي لدعم القرار؛ والاعتماد النهائي يخضع لتقدير مدير المشروع.")
    return "\n".join(lines)


def deterministic_explanation(
    requirements: TaskRequirements, result: EngineResult, *, lang: str | None = None
) -> str:
    use_ar = (lang == "ar") or (lang in (None, "auto") and is_arabic(requirements.title))
    if use_ar:
        return _deterministic_explanation_ar(requirements, result)
    return _deterministic_explanation_en(requirements, result)


def explanation_evidence(requirements: TaskRequirements, result: EngineResult) -> dict[str, Any]:
    primary = result.primary
    evidence: dict[str, Any] = {
        "task": {
            "title": requirements.title,
            "estimated_hours": requirements.estimated_hours,
            "complexity": requirements.complexity.value,
        },
        "status": result.status.value,
        "warnings": list(result.warnings),
    }
    if primary is not None:
        evidence["recommended"] = {
            "name": primary.full_name,
            "suitability_score": primary.suitability_score,
            "availability_status": primary.availability_status.value,
            "available_capacity_hours": primary.available_capacity_hours,
            "data_coverage": primary.data_coverage,
            "criteria": {
                c.criterion.value: {"score": c.score_used, "evidence": list(c.evidence)}
                for c in primary.criteria
                if c.applicable
            },
        }
        evidence["alternatives"] = [
            {"name": c.full_name, "suitability_score": c.suitability_score} for c in result.ranked[1:4]
        ]
    evidence["excluded_reasons"] = _reason_summary(result.excluded)
    return evidence


def is_grounded(text: str, evidence: dict[str, Any], required_name: str | None) -> bool:
    if not text.strip():
        return False
    if required_name and required_name not in text:
        return False
    allowed = {float(n) for n in _NUMBER.findall(json.dumps(evidence, default=str))}
    allowed |= {round(a) for a in allowed} | {round(a, 1) for a in allowed}
    allowed |= {a * 100 for a in allowed if 0 < a <= 1}  # coverage ratios rendered as percentages
    for raw in _NUMBER.findall(text):
        value = float(raw)
        if value <= 6 and value.is_integer():  # list numbering
            continue
        if not any(abs(value - a) <= 0.051 for a in allowed):
            return False
    return True


def generate_explanation(
    requirements: TaskRequirements,
    result: EngineResult,
    llm: LLMProvider | None,
    *,
    use_llm: bool,
    lang: str | None = None,
) -> tuple[str, str, list[str]]:
    """Returns (text, source, warnings) where source is 'deterministic' or 'llm'."""
    fallback = deterministic_explanation(requirements, result, lang=lang)
    if not use_llm or llm is None or result.status == RecommendationStatus.NO_SUITABLE_CANDIDATE:
        return fallback, "deterministic", []
    evidence = explanation_evidence(requirements, result)
    use_ar = (lang == "ar") or (lang in (None, "auto") and is_arabic(requirements.title))
    target_lang = "ar" if use_ar else "en"
    try:
        text = llm.generate_text(explanation_messages(evidence, lang=target_lang)).value
    except LLMError as exc:
        return fallback, "deterministic", [f"LLM_EXPLANATION_UNAVAILABLE:{exc.code}"]
    primary_name = result.primary.full_name if result.primary else None
    if not is_grounded(text, evidence, primary_name):
        return fallback, "deterministic", ["LLM_EXPLANATION_REJECTED_UNGROUNDED"]
    return text, "llm", []

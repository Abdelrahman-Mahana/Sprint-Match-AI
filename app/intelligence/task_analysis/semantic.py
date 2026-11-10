"""Semantic Search & Vector Embeddings for Skills Matching.

Provides dense multilingual neural vector embeddings (via FastEmbed) and high-dimensional
semantic concept vector matching to compare task titles/descriptions against database skills.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from typing import Any

from app.domain.entities.employee import Skill

logger = logging.getLogger(__name__)

# Multilingual ONNX model cached locally (~120MB, supports Arabic + English)
MULTILINGUAL_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_SIMILARITY_THRESHOLD = 0.45

_ARABIC_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي",
})

# Comprehensive semantic knowledge profiles for all database skills
SKILL_SEMANTIC_PROFILES: dict[str, str] = {
    "Docker": "Docker containers containerization dockerfile image building compose runtime microservices packaging deployment سيرفرات خوادم حاويات كونتينر نشر بيئة تشغيل سحابة",
    "Kubernetes": "Kubernetes k8s cluster orchestration pods deployment scaling ingress cloud load balancing كوبرنتس كلاود خوادم سحابية توزيع احمال مجموعات",
    "Terraform": "Terraform infrastructure as code IaC cloud provisioning AWS modules state management ترافورم بنية تحتية اكواد تهيئة سحابية",
    "AWS": "AWS Amazon Web Services cloud compute EC2 S3 lambda serverless networking VPC كلاود سحابة استضافة سحابية خوادم امازون",
    "CI/CD": "CI/CD continuous integration continuous deployment pipelines GitHub actions automation build test release نشر تلقائي اوتوميشن بايبلاين اختبارات وبناء",
    "Linux & Bash": "Linux operating system Bash shell scripting terminal command line administration system tools لينكس باش شل اوامر نظام سيرفر ادارة خوادم",
    "React": "React UI user interface frontend web components JSX hooks virtual DOM state management واجهات مستخدم صفحات ويب فرونت اند مكونات تفاعلية",
    "Next.js": "Next.js React framework server-side rendering SSR static site generation SSG SEO web apps performance نكست ويب محركات بحث تسريع صفحات سيو ارشفة",
    "TypeScript": "TypeScript typed JavaScript static typing interfaces frontend backend web type safety تايب سكريبت انواع اكواد جافاسكريبت",
    "Tailwind CSS": "Tailwind CSS styling responsive design utilities layout flexbox grid typography colors استايل تصميم الوان خطوط ريسبونسيف متجاوب",
    "Python": "Python programming language backend scripting data science automation scripts Django FastAPI بايثون برمجة باك اند سكربتات",
    "FastAPI": "FastAPI Python backend REST API asynchronous web endpoints routing validation Pydantic microservices واجهات برمجة ريست فاست اي بي اي",
    "Django": "Django Python web framework ORM batteries-included backend admin views models جانجو باك اند ويب",
    "Java": "Java enterprise backend JVM Spring Boot object oriented programming جافا برمجة مؤسسية",
    "Spring Boot": "Spring Boot Java microservices enterprise backend REST API dependency injection سبرنج بوت جافا خدمات مصغرة",
    "Node.js": "Node.js JavaScript backend server runtime Express asynchronous event-driven نود جي اس سيرفر باك اند",
    "Microservices": "Microservices architecture distributed systems service mesh API gateway decoupling services خدمات مصغرة معمارية موزعة انظمة مزامنة",
    "Microservices Architecture": "Microservices architecture distributed systems service mesh API gateway decoupling services خدمات مصغرة معمارية موزعة انظمة مزامنة",
    "PostgreSQL": "PostgreSQL relational database SQL queries tables indexing schema migrations ACID transactions بوستجرس قواعد بيانات استعلامات جداول فهارس",
    "Redis": "Redis in-memory key-value cache caching queues pub-sub session store performance رديس كاش تخزين مؤقت طوابير سرعة",
    "Kafka": "Kafka event streaming distributed event-driven message queue pub-sub pipelines كافكا معالجة احداث طوابير رسائل بث مباشر مزامنة",
    "Kafka & Event-Driven": "Kafka event streaming distributed event-driven message queue pub-sub pipelines كافكا معالجة احداث طوابير رسائل بث مباشر مزامنة",
    "Flutter": "Flutter Dart cross-platform mobile apps iOS Android widget tree state reactive UI فلاتر تطبيقات موبايل جوال هواتف ايفون اندرويد شاشات",
    "Dart": "Dart programming language Flutter object-oriented client-optimized دارت لغة برمجة تطبيقات",
    "Machine Learning": "Machine Learning ML predictive models data science classification regression scikit-learn PyTorch تعلم الة ذكاء اصطناعي تنبؤ تصنيف نماذج",
    "LLM Engineering": "LLM Engineering generative AI prompt engineering RAG vector embeddings fine-tuning chatbots LangChain نماذج لغوية ذكاء توليدي برومبت شات بوت",
    "LangChain": "LangChain LLM application framework agents chains RAG tools vector databases لاتج تشين وكلاء ذكاء شات بوت",
    "Data Engineering": "Data Engineering ETL data pipelines analytics warehousing batch processing pandas data cleaning هندسة بيانات انابيب نقل ومعالجة تحليلات",
    "Pandas & NumPy": "Pandas NumPy data analysis numerical computing dataframes arrays بانداز تحليل بيانات وجداول",
    "Test Automation": "Test Automation QA automated tests integration testing end-to-end unit tests assertions pytest selenium اتمتة الاختبارات فحص تلقائي جودة",
    "QA Testing": "QA Testing quality assurance manual testing bug reporting test plans verification quality control تأكيد جودة فحص يدوي تقارير عيوب",
    "Playwright": "Playwright end-to-end browser testing automation Chromium Firefox web testing بلاي رايت اختبار متصفحات اتمتة ويب",
    "Cypress": "Cypress modern frontend testing automation web test suite e2e component testing سايبرس اختبارات واجهات",
    "API Security": "API Security reverse engineering penetration testing vulnerability scanning encryption OAuth JWT auth protection أمان وحماية هندسة عكسية ثغرات تشفير اختراق مصادقة",
    "ERP Implementation": "ERP Enterprise Resource Planning business processes inventory accounting invoices workflow implementation اي ار بي تخطيط موارد محاسبة مخازن فواتير",
    "Odoo Development": "Odoo Development custom modules Python ERP business apps sales inventory accounting تطوير اودو برمجيات شركات محاسبة",
    "Odoo": "Odoo Development custom modules Python ERP business apps sales inventory accounting تطوير اودو برمجيات شركات محاسبة",
}


def _tokenize_text(text: str) -> list[str]:
    text = text.lower().translate(_ARABIC_FOLD)
    tokens = re.findall(r"[\w\u0600-\u06FF]+", text)
    res = []
    for t in tokens:
        res.append(t)
        if t.startswith("ال") and len(t) > 4:
            res.append(t[2:])
        if t.endswith("ات") and len(t) > 4:
            res.append(t[:-2])
        if t.endswith("ين") and len(t) > 4:
            res.append(t[:-2])
        if t.endswith("ية") and len(t) > 4:
            res.append(t[:-2])
    return res


class FallbackConceptVectorMatcher:
    """Zero-dependency concept vector space matcher (TF-IDF + Cosine Similarity)."""

    def __init__(self, profiles: dict[str, str]) -> None:
        self.doc_tokens = {k: _tokenize_text(v) for k, v in profiles.items()}
        doc_count = len(profiles)
        df: Counter[str] = Counter()
        for toks in self.doc_tokens.values():
            for t in set(toks):
                df[t] += 1
        self.idf = {t: math.log(1 + (doc_count / count)) for t, count in df.items()}
        self.skill_vectors: dict[str, dict[str, float]] = {}
        for k, toks in self.doc_tokens.items():
            vec = Counter(toks)
            weighted = {t: freq * self.idf.get(t, 1.0) for t, freq in vec.items()}
            norm = math.sqrt(sum(v * v for v in weighted.values())) or 1.0
            self.skill_vectors[k] = {t: v / norm for t, v in weighted.items()}

    def search(self, query: str, top_k: int = 5, threshold: float = 0.18) -> list[tuple[str, float]]:
        q_tokens = _tokenize_text(query)
        if not q_tokens:
            return []
        q_vec = Counter(q_tokens)
        weighted_q = {t: freq * self.idf.get(t, 1.0) for t, freq in q_vec.items()}
        q_norm = math.sqrt(sum(v * v for v in weighted_q.values())) or 1.0
        norm_q = {t: v / q_norm for t, v in weighted_q.items()}

        scores = []
        for skill, s_vec in self.skill_vectors.items():
            score = sum(val * s_vec.get(term, 0.0) for term, val in norm_q.items())
            if score >= threshold:
                scores.append((skill, round(score, 4)))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


class SemanticSkillMatcher:
    """Hybrid Vector Semantic Search Engine using ONNX Dense Embeddings and Sparse Concept Vectors."""

    _instance: SemanticSkillMatcher | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._dense_model: Any = None
        self._fallback = FallbackConceptVectorMatcher(SKILL_SEMANTIC_PROFILES)
        self._skill_dense_cache: dict[str, Any] = {}
        self._load_dense_model()

    @classmethod
    def get_instance(cls) -> SemanticSkillMatcher:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _load_dense_model(self) -> None:
        try:
            from fastembed import TextEmbedding
            self._dense_model = TextEmbedding(model_name=MULTILINGUAL_MODEL_NAME)
            logger.info("FastEmbed multilingual embedding model loaded successfully.")
        except Exception as exc:
            logger.warning("Could not load FastEmbed dense model (%s); using fallback concept vector space.", exc)
            self._dense_model = None

    def search_skills(
        self,
        query: str,
        catalog: list[Skill],
        *,
        top_k: int = 4,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> list[tuple[Skill, float]]:
        """Search catalog skills semantically using cosine similarity over dense vector embeddings."""
        if not query.strip() or not catalog:
            return []

        catalog_map = {s.name.lower(): s for s in catalog}
        # Also map aliases
        for s in catalog:
            for a in s.aliases:
                catalog_map.setdefault(a.lower(), s)

        # 1. Try Dense Multilingual Embeddings if available
        if self._dense_model is not None:
            try:
                import numpy as np

                # Prepare skill profiles for catalog skills
                target_skills: list[tuple[Skill, str]] = []
                for s in catalog:
                    profile = SKILL_SEMANTIC_PROFILES.get(s.name) or f"{s.name}: {s.category} {', '.join(s.aliases)}"
                    target_skills.append((s, f"{s.name}: {profile}"))

                # Get or compute skill embeddings
                skill_texts = [item[1] for item in target_skills]
                skill_vecs = np.array(list(self._dense_model.embed(skill_texts)))
                query_vec = np.array(list(self._dense_model.embed([query])))[0]

                # Compute Cosine Similarity
                sims = np.dot(skill_vecs, query_vec) / (
                    np.linalg.norm(skill_vecs, axis=1) * np.linalg.norm(query_vec)
                )

                ranked_indices = np.argsort(sims)[::-1]
                matches: list[tuple[Skill, float]] = []
                for idx in ranked_indices:
                    sim = float(sims[idx])
                    if sim >= threshold:
                        skill = target_skills[idx][0]
                        matches.append((skill, round(sim, 4)))
                    if len(matches) >= top_k:
                        break

                if matches:
                    return matches
            except Exception as exc:
                logger.warning("Dense embedding search failed: %s; falling back to concept vector search.", exc)

        # 2. Fallback Concept Vector Matcher
        fallback_results = self._fallback.search(query, top_k=top_k)
        matched_skills: list[tuple[Skill, float]] = []
        for name, score in fallback_results:
            skill = catalog_map.get(name.lower())
            if skill is not None and (skill, score) not in matched_skills:
                matched_skills.append((skill, score))

        return matched_skills

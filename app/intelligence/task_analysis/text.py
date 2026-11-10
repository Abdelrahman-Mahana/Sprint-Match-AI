"""Fuzzy text understanding for task analysis.

Task titles and descriptions are usually written by people in a hurry: "look into the checkout thing
for merchants", "Fix the login bug so people can get in", "نفسي حد يظبط الـ payments". Matching skills
by exact catalog name misses almost all of that. This module adds:

* **normalization** - Unicode NFKC, case folding, camelCase splitting, punctuation/separator folding,
  so ``Node.js``, ``nodejs``, ``Node-JS`` and ``NODE.JS`` are one term;
* **bilingual hints** - an explicit map of English/Arabic tech words, phrases, and domain concepts to
  canonical skill names. It is *evidence*, never a requirement on its own;
* **Arabic morphological stemming & letter folding** - strips prefixes ("ال", "كال", "بال", "وال", "لل")
  and suffixes ("ات", "ين", "ون", "ية"), folds Alef/Taa-marbouta variants;
* **fuzzy matching** - conservative ``difflib`` similarity for typos and inflections
  (``kubernets``, ``developing`` vs ``development``).

Everything here only affects *which skills the text mentions*; mandatory vs optional and levels always
come from the analyzer's documented precedence and, for mandatory, from an explicit quote.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.86
MIN_FUZZY_LENGTH = 5

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_TERM = re.compile(r"[^\w\u0600-\u06FF+#]+", re.UNICODE)
_TOKEN_SPLIT = re.compile(r"[\s\-_/.]+")

# Arabic diacritics and tatweel, removed before normalization.
_ARABIC_MARKS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u0640]")

# Regex to strip 'الـ' / 'ال-' / 'ال ' prefix before words (e.g. 'الـ backend' -> 'backend', 'الـ API' -> 'API')
_AL_PREFIX_SEPARATOR = re.compile(r"\b(?:ال|وال|بال|فال|كال|لل)[ـ\-\s]+([a-zA-Z\u0600-\u06FF]+)", re.UNICODE)

# Arabic letter folding for uniform search (Alef variants, Taa-marbouta, Alef-maqsoura, Hamza on Waw/Yaa)
_ARABIC_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه",
    "ى": "ي",
    "ؤ": "و",
    "ئ": "ي",
})

# Words that start with 'ال' as part of the root/word itself, not the definite article
EXCEPTIONS_KEEP_AL = frozenset({"الوان", "المانيا", "الماس", "اله", "الي", "الكترونيات", "الكتروني"})


def strip_al(word: str) -> str:
    """Strips the Arabic definite article 'ال' and common prepositional compounds (كال، بال، فال، وال، لل)."""
    w = word
    for pref in ("كال", "بال", "فال", "وال", "لل"):
        if w.startswith(pref) and len(w) > len(pref) + 2:
            w = w[len(pref):]
            break
    if w.startswith("ال") and len(w) > 3 and w not in EXCEPTIONS_KEEP_AL:
        return w[2:]
    return w


def normalize(text: str) -> str:
    """Canonical lowercase form with separators folded to single spaces,
    Arabic hamzas/letters unified, and separated 'الـ' prefixes stripped."""
    cleaned = _AL_PREFIX_SEPARATOR.sub(r"\1", text)
    decomposed = unicodedata.normalize("NFKC", _ARABIC_MARKS.sub("", cleaned))
    spaced = _CAMEL.sub(" ", decomposed)
    folded = _NON_TERM.sub(" ", spaced)
    unified = folded.translate(_ARABIC_FOLD)
    return " ".join(unified.lower().split())


def normalize_arabic(text: str) -> str:
    """Canonical form with Arabic letters folded to base shapes (delegates to normalize)."""
    return normalize(text)


def arabic_stems(token: str) -> set[str]:
    """Generates base/stem variants of an Arabic word by stripping common prefixes and suffixes."""
    stems = {token}
    t = token
    # Strip compound prefixes: 'كال', 'بال', 'فال', 'وال', 'لل', 'ال'
    stripped = strip_al(t)
    if stripped != t:
        stems.add(stripped)
        t = stripped
    # Strip single-letter conjunction ('و', 'ف', 'ب', 'ك', 'ل') if word is long enough
    if len(token) > 4 and token[0] in ("و", "ف", "ب", "ك", "ل") and not token.startswith("ال"):
        stems.add(token[1:])

    # Strip common technical suffixes on all variants ('ات', 'ين', 'ون', 'ية', 'يه', 'ها', 'هم', 'كم')
    curr = list(stems)
    for s in curr:
        for suff in ("ات", "ين", "ون", "ية", "يه", "ها", "هم", "كم"):
            if s.endswith(suff) and len(s) > len(suff) + 2:
                stems.add(s[:-len(suff)])
    return stems


def variants(term: str) -> set[str]:
    """All surface forms of a catalog term that count as the same skill."""
    base = normalize(term)
    out = {base, base.replace(" ", "")}
    out.add(normalize(term.replace(" ", "")))
    stripped = strip_al(base)
    if stripped != base:
        out.add(stripped)
        out.add(stripped.replace(" ", ""))
    return {v for v in out if v}


def tokens(text: str) -> list[str]:
    raw = [t for t in _TOKEN_SPLIT.split(normalize(text)) if t]
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        if tok not in seen:
            out.append(tok)
            seen.add(tok)
        stripped = strip_al(tok)
        if stripped and stripped not in seen:
            out.append(stripped)
            seen.add(stripped)
    return out


# ---------------------------------------------------------------------------------------------
# Bilingual hints: common words and phrases in task descriptions -> canonical skill names.
# Only used to *find* candidate skills in free text; the catalog decides which exist.
# ---------------------------------------------------------------------------------------------

_WORD_HINTS: dict[str, tuple[str, ...]] = {
    # ── backend / languages ──
    "backend": ("Python", "FastAPI", "Django", "Java", "Spring Boot", "Microservices"),
    "api": ("FastAPI", "Django", "Spring Boot", "Microservices"),
    "endpoint": ("FastAPI", "Microservices"),
    "endpoints": ("FastAPI", "Microservices"),
    "webhook": ("FastAPI",),
    "microservice": ("Microservices", "Microservices Architecture", "Python", "Docker"),
    "microservices": ("Microservices", "Microservices Architecture", "Python", "Docker"),
    "python": ("Python",),
    "java": ("Java",),
    "javascript": ("JavaScript",),
    "typescript": ("TypeScript",),
    "nodejs": ("Node.js", "TypeScript"),
    "node": ("Node.js",),
    "django": ("Django", "Python"),
    "fastapi": ("FastAPI", "Python"),
    "spring": ("Spring Boot", "Java"),
    "springboot": ("Spring Boot", "Java"),
    "graphql": ("GraphQL", "Node.js", "FastAPI"),
    "rest": ("FastAPI", "Django", "Microservices"),
    "restful": ("FastAPI", "Django", "Microservices"),
    "grpc": ("Microservices", "Python"),

    # ── frontend / mobile / web / styling ──
    "frontend": ("React", "Next.js", "JavaScript", "TypeScript", "Tailwind CSS"),
    "ui": ("React", "Next.js", "Tailwind CSS"),
    "ux": ("React", "Next.js"),
    "react": ("React",),
    "next": ("Next.js",),
    "nextjs": ("Next.js", "React"),
    "tailwind": ("Tailwind CSS",),
    "tailwindcss": ("Tailwind CSS",),
    "css": ("Tailwind CSS", "React"),
    "html": ("React", "Next.js"),
    "flutter": ("Flutter", "Dart"),
    "dart": ("Dart", "Flutter"),
    "mobile": ("Flutter", "Dart"),
    "app": ("Flutter", "React"),
    "android": ("Flutter", "Dart"),
    "ios": ("Flutter", "Dart"),
    "seo": ("Next.js", "React", "TypeScript"),
    "sitemap": ("Next.js", "React"),
    "accessibility": ("React", "Next.js"),
    "responsive": ("React", "Tailwind CSS"),

    # ── database & data engineering & caching ──
    "database": ("PostgreSQL", "SQLAlchemy", "Redis"),
    "db": ("PostgreSQL", "Redis"),
    "sql": ("PostgreSQL", "SQLAlchemy"),
    "query": ("PostgreSQL", "SQLAlchemy"),
    "queries": ("PostgreSQL",),
    "migration": ("PostgreSQL", "SQLAlchemy"),
    "schema": ("PostgreSQL",),
    "postgres": ("PostgreSQL",),
    "postgresql": ("PostgreSQL",),
    "redis": ("Redis",),
    "cache": ("Redis",),
    "caching": ("Redis",),
    "kafka": ("Kafka", "Kafka & Event-Driven"),
    "streaming": ("Kafka", "Kafka & Event-Driven", "Data Engineering"),
    "queue": ("Redis", "Kafka"),
    "etl": ("Data Engineering",),
    "pipeline": ("Data Engineering", "CI/CD"),
    "analytics": ("Data Engineering", "Machine Learning"),
    "dashboard": ("Data Engineering", "React"),
    "model": ("Machine Learning",),
    "ml": ("Machine Learning",),
    "ai": ("Machine Learning", "LLM Engineering", "LangChain"),
    "llm": ("LLM Engineering", "LangChain"),
    "chatbot": ("LLM Engineering", "LangChain"),
    "rag": ("LLM Engineering", "LangChain"),
    "prompt": ("LLM Engineering", "LangChain"),
    "langchain": ("LangChain", "LLM Engineering"),

    # ── devops / cloud / infrastructure ──
    "deploy": ("Docker", "CI/CD", "AWS"),
    "deployment": ("Docker", "Kubernetes", "AWS"),
    "docker": ("Docker",),
    "container": ("Docker",),
    "containers": ("Docker", "Kubernetes"),
    "kubernetes": ("Kubernetes",),
    "k8s": ("Kubernetes",),
    "cluster": ("Kubernetes", "AWS"),
    "terraform": ("Terraform",),
    "infrastructure": ("Terraform", "AWS", "Docker"),
    "cloud": ("AWS",),
    "aws": ("AWS",),
    "serverless": ("AWS",),
    "ci": ("CI/CD",),
    "cd": ("CI/CD",),
    "build": ("CI/CD",),
    "linux": ("Linux & Bash",),
    "bash": ("Linux & Bash",),
    "shell": ("Linux & Bash",),
    "sync": ("Docker", "Kubernetes", "AWS", "Linux & Bash", "CI/CD"),
    "synchronization": ("Docker", "Kubernetes", "AWS", "Linux & Bash"),
    "replication": ("PostgreSQL", "Docker", "AWS"),
    "backup": ("PostgreSQL", "AWS", "Docker"),
    "failover": ("AWS", "Kubernetes", "Docker"),
    "loadbalancer": ("AWS", "Kubernetes"),

    # ── quality / testing / security / reverse engineering ──
    "test": ("Test Automation", "QA Testing", "Playwright", "Cypress"),
    "tests": ("Test Automation", "QA Testing"),
    "testing": ("Test Automation", "QA Testing", "Playwright", "Cypress"),
    "qa": ("QA Testing", "Test Automation"),
    "bug": ("Test Automation", "QA Testing"),
    "automation": ("Test Automation", "CI/CD"),
    "e2e": ("Playwright", "Cypress", "Test Automation"),
    "browser": ("Playwright", "Cypress"),
    "playwright": ("Playwright",),
    "cypress": ("Cypress",),
    "security": ("API Security", "Linux & Bash", "Python"),
    "auth": ("API Security", "FastAPI"),
    "authentication": ("API Security", "FastAPI"),
    "login": ("API Security", "React"),
    "owasp": ("API Security",),
    "vulnerability": ("API Security", "Linux & Bash"),
    "penetration": ("API Security", "Linux & Bash"),
    "pentest": ("API Security", "Linux & Bash"),
    "reverse engineering": ("API Security", "Linux & Bash", "Python"),
    "decompile": ("API Security", "Python"),
    "encryption": ("API Security", "Python"),
    "jwt": ("API Security", "FastAPI"),
    "ssl": ("API Security", "Linux & Bash"),
    "tls": ("API Security", "Linux & Bash"),

    # ── ERP & business ──
    "erp": ("ERP Implementation", "Odoo Development", "Odoo"),
    "odoo": ("Odoo Development", "Odoo", "Python"),
    "accounting": ("ERP Implementation",),
    "inventory": ("ERP Implementation",),
    "invoice": ("ERP Implementation",),
    "payroll": ("ERP Implementation",),

    # ══════════════════════════════════════════════════════════════════════════════════════════════
    # Arabic hints: words, stems, compound terms, and domain phrases
    # ══════════════════════════════════════════════════════════════════════════════════════════════

    # ── general programming ──
    "برمجة": ("Python", "JavaScript", "Java"),
    "برمج": ("Python", "JavaScript"),
    "كود": ("Python", "Linux & Bash"),
    "تطوير": ("React", "Next.js", "Python", "JavaScript", "TypeScript"),
    "تحديث": ("React", "Next.js", "Python"),
    "اصلاح": ("Python", "React", "Test Automation"),
    "تصليح": ("Python", "React"),
    "مشكلة": ("Test Automation",),
    "خطأ": ("Test Automation",),

    # ── frontend / web / design / SEO ──
    "موقع": ("React", "Next.js", "TypeScript"),
    "واجهة": ("React", "Next.js", "TypeScript", "Tailwind CSS"),
    "صفحة": ("React", "Next.js", "TypeScript"),
    "ويب": ("React", "Next.js", "TypeScript"),
    "لوحة": ("React", "Next.js", "TypeScript", "Tailwind CSS"),
    "التحكم": ("React", "Next.js", "TypeScript"),
    "تحكم": ("React", "Next.js", "TypeScript"),
    "داشبورد": ("React", "Next.js", "Tailwind CSS"),
    "تخصيص": ("React", "Tailwind CSS", "TypeScript"),
    "الوان": ("Tailwind CSS", "React"),
    "خطوط": ("Tailwind CSS", "React"),
    "تصميم": ("React", "Tailwind CSS", "TypeScript"),
    "نماذج": ("React", "Next.js", "TypeScript"),
    "فورم": ("React", "Next.js"),
    "استايل": ("Tailwind CSS", "React"),
    "ستايل": ("Tailwind CSS", "React"),
    "ريسبونسيف": ("React", "Tailwind CSS"),
    "متجاوب": ("React", "Tailwind CSS"),
    "حديثة": ("React", "Next.js"),
    "سيو": ("Next.js", "React", "TypeScript"),
    "محركات البحث": ("Next.js", "React", "TypeScript"),
    "ارشفة": ("Next.js", "React"),
    "ارشفه": ("Next.js", "React"),
    "سرعة الموقع": ("Next.js", "React", "Tailwind CSS"),
    "تسريع": ("Next.js", "React", "Tailwind CSS"),
    "تحسين سيو": ("Next.js", "React", "TypeScript"),

    # ── backend / microservices / APIs ──
    "باك": ("FastAPI", "Python", "Microservices"),
    "سيرفس": ("Microservices", "Microservices Architecture", "FastAPI"),
    "ميكروسيرفس": ("Microservices", "Microservices Architecture", "Python", "Docker"),
    "خدمات مصغرة": ("Microservices", "Microservices Architecture", "Python", "Docker"),
    "خدمات مصغره": ("Microservices", "Microservices Architecture", "Python", "Docker"),
    "واجهات برمجة": ("FastAPI", "Python", "API Security"),
    "منطق": ("Python", "FastAPI"),
    "ربط": ("FastAPI", "Python", "Microservices"),

    # ── devops / cloud / synchronization / infrastructure ──
    "سيرفر": ("Docker", "AWS", "Linux & Bash"),
    "سيرفرات": ("Docker", "AWS", "Kubernetes", "Linux & Bash"),
    "خادم": ("Docker", "AWS", "Linux & Bash"),
    "خوادم": ("Docker", "AWS", "Kubernetes", "Linux & Bash"),
    "مزامنة": ("Docker", "Kubernetes", "AWS", "Linux & Bash", "CI/CD"),
    "مزامنه": ("Docker", "Kubernetes", "AWS", "Linux & Bash", "CI/CD"),
    "مزامنة السيرفرات": ("Docker", "Kubernetes", "AWS", "Linux & Bash", "CI/CD"),
    "تزامن": ("Docker", "Kubernetes", "AWS", "Linux & Bash"),
    "استضافة": ("AWS", "Docker"),
    "كلاود": ("AWS", "Docker", "Kubernetes"),
    "سحابة": ("AWS",),
    "سحابية": ("AWS",),
    "نشر": ("Docker", "CI/CD", "AWS"),
    "تهيئة": ("Docker", "Linux & Bash", "Terraform"),
    "توزيع احمال": ("AWS", "Kubernetes"),
    "نسخ احتياطي": ("PostgreSQL", "AWS", "Docker"),
    "باك اب": ("PostgreSQL", "AWS", "Docker"),
    "لينكس": ("Linux & Bash",),
    "باش": ("Linux & Bash",),
    "شل": ("Linux & Bash",),
    "كونتينر": ("Docker",),
    "حاويات": ("Docker",),

    # ── security / reverse engineering / vulnerabilities ──
    "أمان": ("API Security", "Linux & Bash"),
    "امان": ("API Security", "Linux & Bash"),
    "حماية": ("API Security", "Linux & Bash", "Python"),
    "دخول": ("API Security", "FastAPI"),
    "تسجيل": ("API Security", "FastAPI"),
    "مصادقة": ("API Security", "FastAPI"),
    "صلاحيات": ("API Security", "FastAPI"),
    "تشفير": ("API Security", "Python"),
    "ثغرات": ("API Security", "Linux & Bash"),
    "ثغرة": ("API Security", "Linux & Bash"),
    "اختراق": ("API Security", "Linux & Bash"),
    "هندسة عكسية": ("API Security", "Linux & Bash", "Python"),
    "هندسه عكسيه": ("API Security", "Linux & Bash", "Python"),
    "عكسية": ("API Security", "Linux & Bash", "Python"),
    "عكسيه": ("API Security", "Linux & Bash", "Python"),
    "تفكيك": ("API Security", "Linux & Bash", "Python"),
    "فحص امني": ("API Security", "Linux & Bash"),
    "جدار ناري": ("API Security", "Linux & Bash"),

    # ── mobile ──
    "موبايل": ("Flutter", "Dart"),
    "تطبيق": ("Flutter", "Dart"),
    "ابلكيشن": ("Flutter", "Dart"),
    "جوال": ("Flutter", "Dart"),
    "هاتف": ("Flutter", "Dart"),
    "اندرويد": ("Flutter", "Dart"),
    "ايفون": ("Flutter", "Dart"),

    # ── database & caching ──
    "قاعدة": ("PostgreSQL", "SQLAlchemy", "Redis"),
    "بيانات": ("PostgreSQL", "Data Engineering", "Redis"),
    "استعلام": ("PostgreSQL", "SQLAlchemy"),
    "تقارير": ("Data Engineering", "React"),
    "كاش": ("Redis",),
    "تخزين مؤقت": ("Redis",),
    "رديس": ("Redis",),
    "طوابير": ("Kafka", "Redis"),
    "كافكا": ("Kafka", "Kafka & Event-Driven"),
    "رسائل": ("Kafka", "Redis"),

    # ── AI / ML / NLP ──
    "ذكاء": ("Machine Learning", "LLM Engineering", "LangChain"),
    "اصطناعي": ("Machine Learning", "LLM Engineering", "LangChain"),
    "ذكاء اصطناعي": ("Machine Learning", "LLM Engineering", "LangChain"),
    "روبوت": ("Machine Learning", "LLM Engineering"),
    "بوت": ("LLM Engineering", "LangChain"),
    "شات بوت": ("LLM Engineering", "LangChain"),
    "توليدي": ("LLM Engineering", "LangChain"),
    "نماذج": ("Machine Learning", "LLM Engineering"),
    "تنبؤ": ("Machine Learning",),
    "تصنيف": ("Machine Learning",),

    # ── testing & QA ──
    "اختبار": ("Test Automation", "QA Testing", "Playwright", "Cypress"),
    "اختبارات": ("Test Automation", "QA Testing"),
    "فحص": ("Test Automation", "QA Testing", "API Security"),
    "تيست": ("Test Automation", "QA Testing"),
    "تست": ("Test Automation", "QA Testing"),
    "جودة": ("QA Testing", "Test Automation"),
    "اتمتة": ("Test Automation", "CI/CD"),
    "اوتوميشن": ("Test Automation", "CI/CD"),

    # ── ERP ──
    "مهام": ("ERP Implementation",),
    "محاسبة": ("ERP Implementation",),
    "مخزون": ("ERP Implementation",),
    "فاتورة": ("ERP Implementation",),
    "فواتير": ("ERP Implementation",),
    "اودو": ("Odoo Development", "Odoo", "Python"),
    "رواتب": ("ERP Implementation",),
}

# ---------------------------------------------------------------------------------------------
# Broad Technical Domains (used when no specific word hint is found, but technical intent exists)
# ---------------------------------------------------------------------------------------------

_DOMAIN_CATEGORIES: dict[str, tuple[set[str], tuple[str, ...]]] = {
    "frontend": (
        {"واجه", "صفح", "موقع", "ويب", "شاش", "زر", "لون", "خط", "تصميم", "مظهر", "ستايل", "سيو", "ui", "ux", "css"},
        ("React", "Next.js", "TypeScript", "Tailwind CSS"),
    ),
    "devops": (
        {"سيرفر", "خادم", "استضاف", "كلاود", "سحاب", "نشر", "تزامن", "مزامنه", "حاوي", "كونتينر", "شبك", "cloud", "deploy"},
        ("Docker", "Kubernetes", "AWS", "Linux & Bash", "CI/CD", "Terraform"),
    ),
    "backend": (
        {"باك", "خدم", "سيرفس", "ميكروسيرفس", "api", "endpoint", "rest", "graphql", "crud", "ربط"},
        ("FastAPI", "Python", "Microservices", "Microservices Architecture", "Node.js", "Django"),
    ),
    "security": (
        {"امن", "امان", "حماي", "تشفير", "ثغر", "اختراق", "عكسي", "تفكيك", "صلاحي", "auth", "security", "token"},
        ("API Security", "Linux & Bash", "Python", "FastAPI"),
    ),
    "database": (
        {"قاعد", "بيان", "استعلام", "فهرس", "كاش", "طابور", "داتابيز", "db", "sql", "postgres", "redis", "kafka"},
        ("PostgreSQL", "Redis", "Kafka", "SQLAlchemy", "Data Engineering"),
    ),
    "mobile": (
        {"موبايل", "تطبيق", "ابلكيشن", "جوال", "هاتف", "اندرويد", "ايفون", "mobile", "app", "flutter"},
        ("Flutter", "Dart"),
    ),
    "ai_ml": (
        {"ذكاء", "اصطناع", "نموذج", "توليد", "روبوت", "بوت", "تنبؤ", "تصنيف", "ai", "ml", "prompt", "llm"},
        ("Machine Learning", "LLM Engineering", "LangChain", "Python"),
    ),
    "testing": (
        {"فحص", "اختبار", "تست", "تيست", "جود", "اتمت", "qa", "test", "automation"},
        ("Test Automation", "QA Testing", "Playwright", "Cypress"),
    ),
    "erp": (
        {"اودو", "محاسب", "مخزن", "فاتور", "رواتب", "odoo", "erp"},
        ("ERP Implementation", "Odoo Development", "Odoo", "Python"),
    ),
}


def hinted_skill_names(text: str) -> set[str]:
    """Canonical skill names implied by keywords/phrases in ``text``."""
    found: set[str] = set()
    norm = normalize(text)
    norm_folded = norm.translate(_ARABIC_FOLD)
    raw_tokens = set(_TOKEN_SPLIT.split(norm))

    # Expand tokens with Arabic morphological stems and letter folding
    expanded_tokens: set[str] = set(raw_tokens)
    for tok in raw_tokens:
        tok_folded = tok.translate(_ARABIC_FOLD)
        expanded_tokens.add(tok_folded)
        expanded_tokens.update(arabic_stems(tok_folded))

    for phrase, skills in _WORD_HINTS.items():
        phrase_folded = phrase.translate(_ARABIC_FOLD)
        # 1. Direct token/stem match
        if phrase in expanded_tokens or phrase_folded in expanded_tokens:
            found.update(skills)
            continue
        # 2. Multi-word phrase substring match in normalized text
        if " " in phrase:
            if phrase in norm or phrase_folded in norm_folded:
                found.update(skills)
                continue
        # 3. Substring match for longer single terms (len > 3)
        if len(phrase) > 3 and (phrase in norm or phrase_folded in norm_folded):
            found.update(skills)
    return found


def infer_domain_skills(text: str) -> tuple[str, ...]:
    """Fallback: if no specific skill hint was found, score text against broad tech domains."""
    norm = normalize_arabic(text)
    tokens_set = set(_TOKEN_SPLIT.split(norm))
    # Add stems
    expanded = set(tokens_set)
    for t in tokens_set:
        expanded.update(arabic_stems(t))

    best_domain: str | None = None
    best_score = 0

    for domain_name, (indicators, skills) in _DOMAIN_CATEGORIES.items():
        score = 0
        for ind in indicators:
            if any(ind in tok for tok in expanded) or ind in norm:
                score += 1
        # Require at least 2 distinct domain signal hits to avoid accidental matching
        if score >= 2 and score > best_score:
            best_score = score
            best_domain = domain_name

    if best_domain is not None:
        return _DOMAIN_CATEGORIES[best_domain][1]
    return ()


def fuzzy_equals(a: str, b: str) -> bool:
    """True when two terms are the same after normalization, punctuation folding or close typo."""
    if a == b:
        return True
    if len(a) < MIN_FUZZY_LENGTH or len(b) < MIN_FUZZY_LENGTH:
        return False
    if abs(len(a) - len(b)) > max(2, len(a) // 3):
        return False
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_THRESHOLD


def text_mentions(term: str, text: str) -> bool:
    """Whether ``text`` mentions the catalog ``term`` (exact, spaced, punctuation or fuzzy)."""
    norm_text = normalize(text)
    for form in variants(term):
        if len(form) <= 2:
            if re.search(rf"(?<![\w\u0600-\u06FF]){re.escape(form)}(?![\w\u0600-\u06FF])", norm_text):
                return True
            continue
        if form in norm_text.replace(" ", ""):
            return True
        if re.search(rf"(?<![\w\u0600-\u06FF]){re.escape(form)}(?![\w\u0600-\u06FF])", norm_text):
            return True
    # Fuzzy pass over individual words and bigrams (never over the whole text).
    words = _TOKEN_SPLIT.split(norm_text)
    candidates = set(words)
    for i in range(len(words) - 1):
        candidates.add(f"{words[i]}{words[i + 1]}")
    for form in variants(term):
        if len(form) < MIN_FUZZY_LENGTH:
            continue
        compact = form.replace(" ", "")
        for candidate in candidates:
            if fuzzy_equals(compact, candidate):
                return True
    return False

"""Declarative specification of the synthetic demo company.

Scenario employees (EMP-001..EMP-013) are hand-specified so that each documented business scenario is
exercised deterministically; filler employees (EMP-014..EMP-030) are deliberately kept *below* the scenario
tasks' mandatory thresholds so they cannot accidentally change scenario outcomes.
All names are fictional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

V, SD, PR = "VERIFIED", "SELF_DECLARED", "PENDING_REVIEW"

SKILLS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Python", "language", ()),
    ("FastAPI", "backend_framework", ()),
    ("Django", "backend_framework", ()),
    ("PostgreSQL", "database", ("postgres",)),
    ("SQLAlchemy", "library", ()),
    ("Redis", "database", ()),
    ("Java", "language", ()),
    ("Spring Boot", "backend_framework", ()),
    ("JavaScript", "language", ()),
    ("TypeScript", "language", ()),
    ("React", "frontend_framework", ("reactjs", "react.js")),
    ("Next.js", "frontend_framework", ("nextjs",)),
    ("Flutter", "mobile", ()),
    ("Docker", "devops", ()),
    ("Kubernetes", "devops", ("k8s",)),
    ("Terraform", "devops", ()),
    ("AWS", "cloud", ("amazon web services",)),
    ("CI/CD", "devops", ("github actions", "gitlab ci")),
    ("Machine Learning", "ai", ("scikit-learn",)),
    ("Data Engineering", "data", ("airflow", "etl")),
    ("LLM Engineering", "ai", ("langgraph",)),
    ("Test Automation", "qa", ("automated tests",)),
    ("Playwright", "qa", ()),
    ("API Security", "security", ("owasp",)),
    ("Odoo Development", "backend_framework", ("odoo", "odoo erp", "odoo 17")),
    ("ERP Implementation", "engineering", ("erp", "erp implementation")),
]

DEPARTMENTS: dict[str, list[str]] = {
    "Backend Engineering": ["Payments Core", "Platform APIs"],
    "Frontend & Mobile": ["Web Experience", "Mobile Apps"],
    "Data & AI": ["Data Platform", "Applied AI"],
    "DevOps & Cloud": ["Cloud Infrastructure"],
    "Quality Assurance": ["QA Automation"],
    "Odoo Implementation": ["Odoo ERP Implementation"],
}

# code, name, description, project manager employee code
PROJECTS: list[tuple[str, str, str, str]] = [
    ("PAY", "Payments Gateway", "Merchant payments, refunds and settlement APIs", "EMP-002"),
    ("MOB", "Merchant Mobile App", "Flutter app for merchants", "EMP-019"),
    ("ANA", "Analytics Platform", "Dashboards and data pipelines for merchant analytics", "EMP-021"),
    ("CLD", "Cloud Migration", "Migration of on-prem services to Kubernetes on AWS", "EMP-013"),
    ("HRP", "HR Self-Service Portal", "Internal portal for HR processes", "EMP-018"),
    ("AIS", "AI Support Assistant", "LLM-based assistant for customer support agents", "EMP-023"),
]

# kind -> (required skills, category, title templates)
TASK_KINDS: dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]] = {
    "backend_api": (
        ("Python", "FastAPI", "PostgreSQL"),
        "backend_development",
        (
            "Implement {} endpoint",
            "Refactor {} service",
            "Add validation to {} API",
            "Build {} webhook handler",
        ),
    ),
    "backend_sql": (
        ("PostgreSQL", "Python"),
        "database",
        ("Optimize {} queries", "Design {} schema migration", "Add partitioning for {} table"),
    ),
    "django": (
        ("Python", "Django", "PostgreSQL"),
        "backend_development",
        ("Build {} admin workflow", "Implement {} approval flow", "Add {} reporting view"),
    ),
    "java": (
        ("Java", "Spring Boot", "PostgreSQL"),
        "backend_development",
        ("Implement {} integration", "Migrate {} module to Spring Boot 3"),
    ),
    "frontend": (
        ("React", "TypeScript", "JavaScript"),
        "frontend_development",
        ("Build {} dashboard page", "Implement {} filter panel", "Improve {} table performance"),
    ),
    "mobile": (("Flutter",), "mobile_development", ("Build {} screen", "Implement offline {} sync")),
    "data": (
        ("Python", "Data Engineering", "PostgreSQL"),
        "data_engineering",
        ("Build {} ingestion pipeline", "Backfill {} history"),
    ),
    "ml": (("Python", "Machine Learning"), "machine_learning", ("Train {} model", "Evaluate {} features")),
    "llm": (
        ("Python", "LLM Engineering"),
        "machine_learning",
        ("Build {} retrieval chain", "Evaluate {} prompts"),
    ),
    "devops": (
        ("Docker", "Kubernetes", "Terraform", "CI/CD"),
        "devops",
        ("Containerize {} service", "Provision {} cluster", "Automate {} deployments"),
    ),
    "cloud": (("AWS", "Terraform"), "devops", ("Provision {} infrastructure", "Harden {} IAM policies")),
    "qa": (
        ("Test Automation", "Playwright"),
        "qa_testing",
        ("Automate {} regression suite", "Add {} e2e tests"),
    ),
}

SUBJECTS = (
    "refund",
    "settlement",
    "merchant profile",
    "payout",
    "invoice",
    "chargeback",
    "onboarding",
    "audit log",
    "notification",
    "currency rates",
    "reporting",
    "leave request",
    "ticket triage",
    "billing",
)


@dataclass(frozen=True)
class Outcome:
    kind: str
    project: str
    quality: float
    complexity: str = "medium"
    on_time: bool = True
    delay: str | None = None
    rework: int = 0
    accepted: bool = True


def hist(
    kind: str,
    project: str,
    qualities: list[float],
    complexities: list[str] | None = None,
    late: dict[int, str] | None = None,
    rework: dict[int, int] | None = None,
) -> tuple[Outcome, ...]:
    late, rework = late or {}, rework or {}
    return tuple(
        Outcome(
            kind=kind,
            project=project,
            quality=q,
            complexity=(complexities[i % len(complexities)] if complexities else "medium"),
            on_time=i not in late,
            delay=late.get(i),
            rework=rework.get(i, 0),
        )
        for i, q in enumerate(qualities)
    )


@dataclass(frozen=True)
class Exp:
    org: str
    role: str
    years: float
    skills: tuple[str, ...]
    ended_months_ago: int = 0


@dataclass(frozen=True)
class EmpSpec:
    code: str
    name: str
    title: str
    department: str
    team: str
    years: float
    hired_months_ago: int
    skills: tuple[tuple[str, int, str, int], ...]  # name, level, verification, months since assessment
    history: tuple[Outcome, ...] = ()
    kinds: tuple[str, ...] = ()  # filler employees: kinds used for generated history/active tasks
    experiences: tuple[Exp, ...] = ()
    credentials: tuple[tuple[str, str, str, str | None], ...] = ()  # name, issuer, type, skill
    status: str = "ACTIVE"
    timezone: str = "Africa/Cairo"
    schedule: str = "standard"  # standard | part_time | mon_fri | none
    projects: tuple[str, ...] = ()
    flags: frozenset[str] = field(default_factory=frozenset)


SCENARIO_EMPLOYEES: list[EmpSpec] = [
    EmpSpec(
        "EMP-001",
        "Ahmed Hassan",
        "Senior Backend Engineer",
        "Backend Engineering",
        "Payments Core",
        6,
        40,
        (
            ("Python", 92, V, 4),
            ("FastAPI", 88, V, 4),
            ("PostgreSQL", 80, V, 6),
            ("SQLAlchemy", 82, V, 6),
            ("Docker", 65, SD, 10),
            ("Redis", 60, SD, 12),
        ),
        history=hist(
            "backend_api",
            "PAY",
            [90, 92, 88, 94, 91, 93],
            ["medium", "high", "high", "medium", "high", "high"],
            late={2: "EXTERNAL_BLOCKER"},
        ),
        experiences=(Exp("Payments startup (fictional)", "Backend Developer", 3, ("Python", "FastAPI"), 40),),
        credentials=(("PCAP - Python Associate", "Python Institute", "CERTIFICATION", "Python"),),
        projects=("PAY",),
    ),
    EmpSpec(
        "EMP-002",
        "Karim Mostafa",
        "Backend Tech Lead",
        "Backend Engineering",
        "Payments Core",
        10,
        70,
        (
            ("Python", 95, V, 3),
            ("FastAPI", 90, V, 3),
            ("PostgreSQL", 85, V, 3),
            ("Redis", 80, V, 6),
            ("Docker", 75, V, 8),
            ("API Security", 70, V, 8),
        ),
        history=hist("backend_api", "PAY", [93, 95, 90, 94, 92, 96, 91, 93], ["high", "critical"]),
        experiences=(Exp("Regional bank (fictional)", "Software Engineer", 4, ("Python", "PostgreSQL"), 70),),
        projects=("PAY",),
    ),
    EmpSpec(
        "EMP-003",
        "Nour El-Din Adel",
        "Backend Engineer",
        "Backend Engineering",
        "Platform APIs",
        5,
        30,
        (("Python", 85, V, 5), ("Django", 82, V, 5), ("PostgreSQL", 72, V, 5), ("Redis", 55, SD, 9)),
        history=hist("django", "HRP", [82, 80, 85, 83]),
        projects=("HRP",),
    ),
    EmpSpec(
        "EMP-004",
        "Salma Farouk",
        "Senior Backend Engineer",
        "Backend Engineering",
        "Platform APIs",
        7,
        50,
        (("Python", 88, V, 5), ("FastAPI", 85, V, 5), ("PostgreSQL", 78, V, 5), ("SQLAlchemy", 75, V, 7)),
        history=hist("backend_api", "HRP", [86, 84, 88, 85, 87], ["medium", "high"], late={3: "EMPLOYEE"}),
        projects=("HRP", "PAY"),
        flags=frozenset({"leave_window"}),
    ),
    EmpSpec(
        "EMP-005",
        "Youssef Ibrahim",
        "Backend Engineer",
        "Backend Engineering",
        "Payments Core",
        4,
        28,
        (("Python", 82, V, 6), ("FastAPI", 80, V, 6), ("PostgreSQL", 70, V, 6), ("Redis", 65, SD, 6)),
        history=hist("backend_api", "PAY", [80, 78, 82, 79, 81], late={1: "EMPLOYEE"}),
        projects=("PAY",),
    ),
    EmpSpec(
        "EMP-006",
        "Hana Mahmoud",
        "Backend Engineer",
        "Backend Engineering",
        "Platform APIs",
        3,
        26,
        (("Python", 80, V, 4), ("FastAPI", 75, V, 4), ("PostgreSQL", 55, V, 4)),
        history=hist("backend_api", "PAY", [76, 74, 78, 75], late={2: "EMPLOYEE"}),
        projects=("PAY",),
        flags=frozenset({"leave_today"}),
    ),
    EmpSpec(
        "EMP-007",
        "Tarek Samir",
        "Senior Database Engineer",
        "Backend Engineering",
        "Payments Core",
        7,
        55,
        (
            ("PostgreSQL", 92, V, 2),
            ("Python", 78, V, 5),
            ("FastAPI", 72, V, 5),
            ("SQLAlchemy", 80, V, 5),
            ("Data Engineering", 60, SD, 12),
        ),
        history=hist("backend_sql", "PAY", [88, 90, 86, 92, 89, 87, 91, 88, 90], ["high", "medium"]),
        experiences=(
            Exp("Telecom data warehouse (fictional)", "Database Developer", 5, ("PostgreSQL",), 55),
        ),
        projects=("PAY",),
    ),
    EmpSpec(
        "EMP-008",
        "Laila Nabil",
        "Backend Engineer",
        "Backend Engineering",
        "Payments Core",
        3,
        1,
        (("Python", 80, V, 1), ("FastAPI", 76, V, 1), ("PostgreSQL", 60, V, 1), ("Docker", 55, SD, 1)),
        experiences=(Exp("Fintech startup (fictional)", "Backend Developer", 3, ("Python", "FastAPI"), 1),),
        projects=("PAY",),
    ),
    EmpSpec(
        "EMP-009",
        "Mahmoud Fathy",
        "Backend Engineer",
        "Backend Engineering",
        "Platform APIs",
        6,
        60,
        (("Python", 85, SD, 50), ("FastAPI", 78, SD, 50), ("PostgreSQL", 70, SD, 50), ("Django", 72, PR, 14)),
        history=hist("django", "HRP", [66, 62, 68], late={1: "EMPLOYEE"}, rework={0: 1, 1: 2}),
        projects=("HRP",),
    ),
    EmpSpec(
        "EMP-010",
        "Rania Saad",
        "Senior Mobile Engineer",
        "Frontend & Mobile",
        "Mobile Apps",
        7,
        48,
        (
            ("Flutter", 94, V, 3),
            ("JavaScript", 72, V, 6),
            ("Test Automation", 75, V, 6),
            ("CI/CD", 60, SD, 12),
        ),
        history=hist("mobile", "MOB", [96, 97, 95, 98, 96, 97, 95], ["high"]),
        projects=("MOB",),
    ),
    EmpSpec(
        "EMP-011",
        "Mariam Ali",
        "Frontend Engineer",
        "Frontend & Mobile",
        "Web Experience",
        4,
        30,
        (("React", 81, V, 3), ("TypeScript", 78, V, 3), ("JavaScript", 85, V, 3), ("Next.js", 70, V, 3)),
        history=hist("frontend", "ANA", [84, 86, 82, 85]),
        experiences=(
            Exp("E-commerce agency (fictional)", "Frontend Developer", 2, ("React", "TypeScript"), 30),
        ),
        projects=("ANA",),
    ),
    EmpSpec(
        "EMP-012",
        "Omar Khaled",
        "Frontend Engineer",
        "Frontend & Mobile",
        "Web Experience",
        4,
        30,
        (("React", 80, V, 3), ("TypeScript", 78, V, 3), ("JavaScript", 85, V, 3), ("Next.js", 70, V, 3)),
        history=hist("frontend", "ANA", [84, 86, 82, 85]),
        experiences=(Exp("Media company (fictional)", "Frontend Developer", 2, ("React", "TypeScript"), 30),),
        projects=("ANA",),
    ),
    EmpSpec(
        "EMP-013",
        "Mostafa Gamal",
        "DevOps Lead",
        "DevOps & Cloud",
        "Cloud Infrastructure",
        9,
        66,
        (
            ("Kubernetes", 90, V, 2),
            ("Terraform", 86, V, 2),
            ("Docker", 92, V, 2),
            ("AWS", 85, V, 4),
            ("CI/CD", 88, V, 4),
        ),
        history=hist("devops", "CLD", [90, 92, 88, 91, 89, 93], ["high", "critical"]),
        credentials=(("Certified Kubernetes Administrator", "CNCF", "CERTIFICATION", "Kubernetes"),),
        projects=("CLD",),
        flags=frozenset({"leave_window"}),
    ),
]

FILLER_EMPLOYEES: list[EmpSpec] = [
    EmpSpec(
        "EMP-014",
        "Mohamed Saber",
        "Java Backend Engineer",
        "Backend Engineering",
        "Platform APIs",
        5,
        36,
        (("Java", 72, V, 6), ("Spring Boot", 70, V, 6), ("PostgreSQL", 60, V, 6), ("Python", 45, SD, 10)),
        kinds=("java",),
        projects=("HRP",),
        flags=frozenset({"pair_unknown"}),
    ),
    EmpSpec(
        "EMP-015",
        "Aya Sherif",
        "Backend Engineer",
        "Backend Engineering",
        "Platform APIs",
        3,
        24,
        (("Java", 65, V, 8), ("Spring Boot", 62, V, 8), ("Python", 58, SD, 8), ("PostgreSQL", 55, V, 8)),
        kinds=("java",),
        projects=("HRP",),
        flags=frozenset({"pair_unknown"}),
    ),
    EmpSpec(
        "EMP-016",
        "Ibrahim Lotfy",
        "Backend Engineer",
        "Backend Engineering",
        "Payments Core",
        2,
        18,
        (("Python", 68, V, 5), ("FastAPI", 60, V, 5), ("PostgreSQL", 62, V, 5), ("Redis", 55, SD, 5)),
        kinds=("backend_api",),
        projects=("PAY",),
        flags=frozenset({"estimated_remaining"}),
    ),
    EmpSpec(
        "EMP-017",
        "Dina Hamdy",
        "Frontend Engineer",
        "Frontend & Mobile",
        "Web Experience",
        3,
        22,
        (("React", 62, V, 5), ("TypeScript", 60, V, 5), ("JavaScript", 70, V, 5), ("Next.js", 55, SD, 5)),
        kinds=("frontend",),
        projects=("ANA",),
        flags=frozenset({"rejected_leave"}),
    ),
    EmpSpec(
        "EMP-018",
        "Sherif Anwar",
        "Frontend Lead",
        "Frontend & Mobile",
        "Web Experience",
        8,
        60,
        (("React", 60, V, 9), ("JavaScript", 68, V, 9), ("TypeScript", 55, SD, 9)),
        kinds=("frontend",),
        projects=("HRP",),
    ),
    EmpSpec(
        "EMP-019",
        "Yasmin Fouad",
        "Mobile Engineer",
        "Frontend & Mobile",
        "Mobile Apps",
        4,
        30,
        (("Flutter", 72, V, 6), ("JavaScript", 50, SD, 6)),
        kinds=("mobile",),
        projects=("MOB",),
    ),
    EmpSpec(
        "EMP-020",
        "Hossam Ragab",
        "Frontend Engineer",
        "Frontend & Mobile",
        "Web Experience",
        2,
        14,
        (("React", 65, V, 4), ("TypeScript", 62, V, 4), ("JavaScript", 72, V, 4)),
        kinds=("frontend",),
        projects=("ANA",),
        flags=frozenset({"pending_leave"}),
    ),
    EmpSpec(
        "EMP-021",
        "Nada Mansour",
        "Senior Data Engineer",
        "Data & AI",
        "Data Platform",
        6,
        44,
        (("Python", 70, V, 5), ("Data Engineering", 74, V, 5), ("PostgreSQL", 68, V, 5)),
        kinds=("data",),
        projects=("ANA",),
    ),
    EmpSpec(
        "EMP-022",
        "Walid Zaki",
        "Data Engineer",
        "Data & AI",
        "Data Platform",
        3,
        20,
        (("Python", 66, V, 7), ("Data Engineering", 70, V, 7), ("PostgreSQL", 65, SD, 7)),
        kinds=("data",),
        projects=("ANA",),
        flags=frozenset({"no_attendance_today"}),
    ),
    EmpSpec(
        "EMP-023",
        "Farah Adly",
        "ML Engineer",
        "Data & AI",
        "Applied AI",
        5,
        34,
        (("Python", 70, V, 4), ("Machine Learning", 75, V, 4), ("LLM Engineering", 68, V, 4)),
        kinds=("ml", "llm"),
        projects=("AIS",),
    ),
    EmpSpec(
        "EMP-024",
        "Bassem Nour",
        "ML Engineer (remote)",
        "Data & AI",
        "Applied AI",
        4,
        16,
        (("Python", 68, V, 6), ("Machine Learning", 72, V, 6), ("LLM Engineering", 65, SD, 6)),
        kinds=("llm",),
        projects=("AIS",),
        timezone="Europe/Berlin",
        schedule="mon_fri",
    ),
    EmpSpec(
        "EMP-025",
        "Ehab Selim",
        "Data Analyst",
        "Data & AI",
        "Data Platform",
        4,
        40,
        (("Python", 60, V, 20), ("PostgreSQL", 66, V, 20)),
        kinds=("data",),
        projects=("ANA",),
        status="INACTIVE",
    ),
    EmpSpec(
        "EMP-026",
        "Amr Fekry",
        "DevOps Engineer",
        "DevOps & Cloud",
        "Cloud Infrastructure",
        5,
        38,
        (
            ("Docker", 78, V, 5),
            ("Kubernetes", 72, V, 5),
            ("Terraform", 65, V, 5),
            ("AWS", 70, V, 5),
            ("CI/CD", 75, V, 5),
        ),
        kinds=("devops",),
        projects=("CLD",),
        flags=frozenset({"commitment"}),
    ),
    EmpSpec(
        "EMP-027",
        "Reem Tawfik",
        "Cloud Engineer (part-time)",
        "DevOps & Cloud",
        "Cloud Infrastructure",
        6,
        26,
        (("AWS", 74, V, 6), ("Terraform", 70, V, 6), ("Docker", 65, V, 6), ("Kubernetes", 60, SD, 6)),
        kinds=("cloud",),
        projects=("CLD",),
        schedule="part_time",
    ),
    EmpSpec(
        "EMP-028",
        "Khaled Azmy",
        "QA Automation Engineer",
        "Quality Assurance",
        "QA Automation",
        5,
        42,
        (
            ("Test Automation", 78, V, 4),
            ("Playwright", 75, V, 4),
            ("JavaScript", 60, V, 4),
            ("Python", 50, SD, 8),
        ),
        kinds=("qa",),
        projects=("PAY", "MOB"),
    ),
    EmpSpec(
        "EMP-029",
        "Noha Gaber",
        "QA Engineer",
        "Quality Assurance",
        "QA Automation",
        3,
        20,
        (("Test Automation", 70, V, 5), ("Playwright", 65, V, 5), ("API Security", 55, SD, 5)),
        kinds=("qa",),
        projects=("PAY",),
    ),
    EmpSpec(
        "EMP-030",
        "Ziad Hamed",
        "QA Engineer (contractor)",
        "Quality Assurance",
        "QA Automation",
        2,
        0,
        (("Test Automation", 62, SD, 0), ("Python", 55, SD, 0)),
        kinds=("qa",),
        schedule="none",
    ),
]

# Active (in-progress) workload of scenario employees.
# code -> list of (project, kind, remaining_hours, start_working_day_offset, end_working_day_offset)
SCENARIO_ACTIVE: dict[str, list[tuple[str, str, float, int, int]]] = {
    "EMP-001": [("PAY", "backend_api", 4, 0, 2)],
    "EMP-002": [("PAY", "backend_api", 24, -3, 3), ("PAY", "backend_sql", 12, 1, 4)],
    "EMP-005": [("PAY", "backend_api", 24, -2, 3)],
    "EMP-007": [("PAY", "backend_sql", 17, -1, 3)],
    "EMP-008": [("PAY", "backend_api", 2, 0, 1)],
    "EMP-009": [("HRP", "django", 6, -2, 2)],
    "EMP-010": [("MOB", "mobile", 10, -3, 4)],
}


@dataclass(frozen=True)
class ScenarioTask:
    code: str
    project: str
    title: str
    description: str
    skills: tuple[tuple[str, int, bool], ...]  # name, minimum level, mandatory
    complexity: str | None
    priority: str
    hours: float | None
    start_day: int  # working-day offset of planned start (09:00)
    end_day: int  # working-day offset of deadline (17:00)
    category: str | None
    certifications: tuple[str, ...] = ()  # hard constraint: required (valid) certifications


SCENARIO_TASKS: list[ScenarioTask] = [
    ScenarioTask(
        "TASK-SCN-01",
        "PAY",
        "Build payment refund API endpoint",
        "Expose a FastAPI endpoint that issues full and partial refunds, persists them in PostgreSQL and "
        "publishes a refund event. Must be idempotent.",
        (("Python", 75, True), ("FastAPI", 70, True), ("PostgreSQL", 50, False)),
        "high",
        "high",
        16,
        1,
        3,
        "backend_development",
    ),
    ScenarioTask(
        "TASK-SCN-02",
        "PAY",
        "Optimize settlement report SQL query",
        "The daily settlement report query takes 40 seconds. Analyse the execution plan and add indexes or "
        "rewrite the query.",
        (("PostgreSQL", 75, True), ("Python", 60, False)),
        "medium",
        "medium",
        6,
        1,
        3,
        "database",
    ),
    ScenarioTask(
        "TASK-SCN-03",
        "ANA",
        "Build merchant analytics dashboard widgets",
        "Create reusable chart widgets for the merchant analytics dashboard.",
        (("React", 70, True), ("TypeScript", 65, True)),
        "medium",
        "medium",
        12,
        1,
        3,
        "frontend_development",
    ),
    ScenarioTask(
        "TASK-SCN-04",
        "CLD",
        "Design multi-cluster Kubernetes failover",
        "Design and implement active-passive failover between two Kubernetes clusters provisioned with Terraform.",
        (("Kubernetes", 85, True), ("Terraform", 80, True)),
        "critical",
        "urgent",
        24,
        1,
        3,
        "devops",
    ),
    ScenarioTask(
        "TASK-SCN-05",
        "PAY",
        "Improve merchant onboarding experience",
        "Merchants say onboarding feels slow and confusing. Work with the business team to understand the pain "
        "points and make the first-week experience smoother.",
        (),
        None,
        "medium",
        8,
        1,
        3,
        None,
    ),
    ScenarioTask(
        "TASK-SCN-06",
        "PAY",
        "Add caching to merchant lookup",
        "Cache merchant lookups to reduce database load.",
        (("Redis", 60, True), ("Python", 70, True)),
        "medium",
        "medium",
        None,
        1,
        3,
        "backend_development",
    ),
    ScenarioTask(
        "TASK-SCN-07",
        "PAY",
        "Migrate ledger service to new database cluster",
        "Move the ledger service to the new PostgreSQL cluster with zero downtime.",
        (("Python", 75, True), ("PostgreSQL", 70, True)),
        "high",
        "urgent",
        40,
        1,
        1,
        "database",
    ),
    ScenarioTask(
        "TASK-SCN-08",
        "PAY",
        "Certify the payment SDK's Python packaging",
        "Prepare the Python payment SDK for the partner certification programme; the partner requires the "
        "implementer to hold the PCAP certification.",
        (("Python", 70, True),),
        "medium",
        "high",
        8,
        1,
        4,
        "backend_development",
        ("PCAP - Python Associate",),
    ),
    ScenarioTask(
        "TASK-BL-01",
        "PAY",
        "Add Playwright smoke tests for checkout",
        "Automate smoke tests for the checkout flow.",
        (("Test Automation", 60, True), ("Playwright", 55, False)),
        "low",
        "medium",
        10,
        1,
        5,
        "qa_testing",
    ),
    ScenarioTask(
        "TASK-BL-02",
        "ANA",
        "Build settlement export pipeline",
        "Daily export of settlements into the analytics warehouse.",
        (("Data Engineering", 65, True), ("Python", 60, True)),
        "medium",
        "medium",
        14,
        1,
        5,
        "data_engineering",
    ),
    ScenarioTask(
        "TASK-BL-03",
        "AIS",
        "Fine-tune support ticket intent classifier",
        "Improve the intent classifier used to route support tickets.",
        (("Machine Learning", 70, True), ("Python", 65, True)),
        "high",
        "medium",
        20,
        1,
        5,
        "machine_learning",
    ),
]

# Second, tiny tenant used to prove tenant isolation. Its employees are deliberately *stronger* than anyone
# in the main company: they must never appear in its recommendations.
SECOND_COMPANY: dict[str, Any] = {
    "code": "DLT",
    "name": "Delta Labs GmbH",
    "timezone": "Europe/Berlin",
    "working_weekdays": [0, 1, 2, 3, 4],
    "employees": [
        ("DLT-001", "Lena Weber", "Staff Engineer"),
        ("DLT-002", "Jonas Becker", "Senior Engineer"),
        ("DLT-003", "Mia Schulz", "Senior Engineer"),
    ],
}


# Task dependencies (task code, depends on task code, type). Informational for recommendations.
TASK_DEPENDENCIES: list[tuple[str, str, str]] = [
    ("TASK-SCN-02", "TASK-SCN-01", "FINISH_TO_START"),
    ("TASK-SCN-07", "TASK-SCN-02", "FINISH_TO_START"),
    ("TASK-SCN-03", "TASK-BL-02", "RELATED"),
    ("TASK-SCN-08", "TASK-SCN-01", "RELATED"),
]

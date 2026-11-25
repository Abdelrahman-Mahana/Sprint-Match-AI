"""Demo dataset catalog, synthetic workforce generator, database seeder, and consistency validator."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.orm import Session

from app.core import ConflictError, SprintMatchError
from app.infrastructure.orm import Base, CompanyModel
from app.intelligence.capacity import is_working_day, next_working_days

# ===========================================================================
# Demo Skill & Employee Catalog
# ===========================================================================

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

# ===========================================================================
# Synthetic Dataset Generator
# ===========================================================================

COMPANY_CODE = "NDS"
COMPANY_TZ = "Africa/Cairo"
SUN_THU = [6, 0, 1, 2, 3]
NOW_TIME = time(10, 0)

# Insertion order respects foreign keys.
TABLE_ORDER = [
    "companies",
    "departments",
    "teams",
    "calendar_holidays",
    "employees",
    "skills",
    "employee_skills",
    "projects",
    "project_members",
    "sprints",
    "tasks",
    "task_required_skills",
    "employee_skill_evidence",
    "employee_experiences",
    "employee_experience_skills",
    "employee_credentials",
    "work_schedule_days",
    "attendance_records",
    "leave_records",
    "task_assignments",
    "timesheet_entries",
    "employee_commitments",
    "task_outcomes",
    "task_dependencies",
    "performance_evaluations",
    "performance_metrics",
]


@dataclass
class DemoDataset:
    reference_date: date
    now: datetime
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {t: [] for t in TABLE_ORDER})

    def counts(self) -> dict[str, int]:
        return {t: len(r) for t, r in self.rows.items()}


class DemoDataGenerator:
    def __init__(self, reference_date: date, seed: int) -> None:
        self.seed = seed
        self.tz = ZoneInfo(COMPANY_TZ)
        self.ns = uuid.uuid5(uuid.NAMESPACE_URL, f"sprintmatch-demo:{seed}")
        base_holidays: set[date] = set()
        d0 = reference_date
        while not is_working_day(d0, SUN_THU, base_holidays):
            d0 += timedelta(days=1)
        # One future company holiday (8th working day) and one past one.
        future_holiday = next_working_days(d0, 8, SUN_THU, base_holidays)[-1]
        past_holiday = d0 - timedelta(days=20)
        self.holidays = {future_holiday: "Company Foundation Day", past_holiday: "National Holiday"}
        self.d0 = d0
        self.now = datetime.combine(d0, NOW_TIME, tzinfo=self.tz)
        self.ds = DemoDataset(reference_date=d0, now=self.now)
        self.skill_ids: dict[str, uuid.UUID] = {}
        self.emp_ids: dict[str, uuid.UUID] = {}
        self.project_ids: dict[str, uuid.UUID] = {}
        self.sprint_ids: dict[tuple[str, int], uuid.UUID] = {}
        self.pm: dict[str, str] = {p[0]: p[3] for p in PROJECTS}
        self.members: dict[str, set[str]] = {}
        self.first_task_for_skill: dict[tuple[str, str], tuple[uuid.UUID, str]] = {}
        self.counters: dict[str, int] = {}

    # helpers
    def uid(self, *parts: object) -> uuid.UUID:
        return uuid.uuid5(self.ns, ":".join(str(p) for p in parts))

    def rng(self, *parts: object) -> random.Random:
        return random.Random(f"{self.seed}:" + ":".join(str(p) for p in parts))

    def add(self, table: str, **row: Any) -> dict[str, Any]:
        self.ds.rows[table].append(row)
        return row

    def wd(self, offset: int) -> date:
        """Working day at ``offset`` from the reference day (company calendar, holidays skipped)."""
        if offset == 0:
            return self.d0
        holidays = set(self.holidays)
        if offset > 0:
            return next_working_days(self.d0, offset, SUN_THU, holidays)[-1]
        day, remaining = self.d0, -offset
        while remaining:
            day -= timedelta(days=1)
            if is_working_day(day, SUN_THU, holidays):
                remaining -= 1
        return day

    def at(self, day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), tzinfo=self.tz)

    def next_code(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-{self.counters[prefix]:03d}"

    def member(self, emp_code: str, project: str) -> None:
        self.members.setdefault(project, set()).add(emp_code)

    # build
    def build(self) -> DemoDataset:
        company_id = self._company()
        self._organization(company_id)
        employees = SCENARIO_EMPLOYEES + FILLER_EMPLOYEES
        for spec in employees:
            self._employee(company_id, spec)
        self._projects(company_id)
        for spec in employees:
            self._history(company_id, spec)
        for spec in employees:
            self._active_work(company_id, spec)
        self._special_workload(company_id)
        self._scenario_tasks(company_id)
        for spec in employees:
            self._profile_details(spec)
            self._schedule_and_attendance(spec)
            self._leaves_and_commitments(spec)
        self._memberships()
        self._performance_reviews(company_id)
        self._second_company()
        return self.ds

    def _company(self) -> uuid.UUID:
        cid = self.uid("company", COMPANY_CODE)
        self.add(
            "companies",
            id=cid,
            code=COMPANY_CODE,
            name="Nile Digital Solutions",
            timezone=COMPANY_TZ,
            working_weekdays=SUN_THU,
        )
        for day, name in sorted(self.holidays.items()):
            self.add(
                "calendar_holidays",
                id=self.uid("holiday", COMPANY_CODE, day),
                company_id=cid,
                holiday_date=day,
                name=name,
            )
        return cid

    def _organization(self, company_id: uuid.UUID) -> None:
        self.dept_ids: dict[str, uuid.UUID] = {}
        self.team_ids: dict[str, uuid.UUID] = {}
        for dept, teams in DEPARTMENTS.items():
            did = self.uid("dept", dept)
            self.dept_ids[dept] = did
            self.add("departments", id=did, company_id=company_id, name=dept)
            for team in teams:
                tid = self.uid("team", team)
                self.team_ids[team] = tid
                self.add("teams", id=tid, department_id=did, name=team)
        for name, category, aliases in SKILLS:
            sid = self.uid("skill", COMPANY_CODE, name)
            self.skill_ids[name] = sid
            self.add(
                "skills",
                id=sid,
                company_id=company_id,
                name=name,
                category=category,
                description=f"{name} ({category.replace('_', ' ')})",
                aliases=list(aliases),
            )

    def _employee(self, company_id: uuid.UUID, spec: EmpSpec) -> None:
        eid = self.uid("employee", spec.code)
        self.emp_ids[spec.code] = eid
        first, *rest = spec.name.lower().replace("-", "").split()
        self.add(
            "employees",
            id=eid,
            company_id=company_id,
            employee_code=spec.code,
            full_name=spec.name,
            email=f"{first}.{rest[-1] if rest else 'x'}@nds.example",
            job_title=spec.title,
            department_id=self.dept_ids[spec.department],
            team_id=self.team_ids[spec.team],
            hire_date=self.d0 - timedelta(days=30 * spec.hired_months_ago),
            experience_years=spec.years,
            employment_status=spec.status,
            timezone=spec.timezone,
        )
        for name, level, status, months in spec.skills:
            assessed = self.now - timedelta(days=round(30.44 * months))
            self.add(
                "employee_skills",
                id=self.uid("emp_skill", spec.code, name),
                employee_id=eid,
                skill_id=self.skill_ids[name],
                proficiency_level=level,
                verification_status=status,
                verified_at=assessed if status == "VERIFIED" else None,
                last_assessed_at=assessed,
            )
        for project in spec.projects:
            self.member(spec.code, project)

    def _projects(self, company_id: uuid.UUID) -> None:
        for code, name, desc, pm in PROJECTS:
            pid = self.uid("project", code)
            self.project_ids[code] = pid
            self.add(
                "projects",
                id=pid,
                company_id=company_id,
                code=code,
                name=name,
                description=desc,
                status="ACTIVE",
                project_manager_id=self.emp_ids[pm],
            )
            sprints = [
                (1, self.d0 - timedelta(days=28), self.d0 - timedelta(days=15), "COMPLETED"),
                (2, self.d0 - timedelta(days=7), self.d0 + timedelta(days=7), "ACTIVE"),
            ]
            for n, start, end, status in sprints:
                sid = self.uid("sprint", code, n)
                self.sprint_ids[(code, n)] = sid
                self.add(
                    "sprints",
                    id=sid,
                    project_id=pid,
                    name=f"{code} Sprint {n}",
                    goal=f"{name} increment {n}",
                    start_date=start,
                    end_date=end,
                    status=status,
                )

    def _task(
        self,
        company_id: uuid.UUID,
        *,
        code: str,
        project: str,
        kind: str | None,
        title: str,
        status: str,
        complexity: str | None,
        priority: str,
        est: float | None,
        remaining: float | None,
        start: datetime | None,
        deadline: datetime | None,
        sprint: int | None,
        description: str | None = None,
        skills: tuple[tuple[str, int, bool], ...] | None = None,
        category: str | None = None,
        certifications: tuple[str, ...] = (),
    ) -> uuid.UUID:
        tid = self.uid("task", code)
        if kind is not None:
            kind_skills, kind_category, _ = TASK_KINDS[kind]
            category = category or kind_category
            if skills is None:
                skills = tuple((s, 60 if i < 2 else 50, i < 2) for i, s in enumerate(kind_skills))
        self.add(
            "tasks",
            id=tid,
            company_id=company_id,
            task_code=code,
            project_id=self.project_ids[project],
            sprint_id=self.sprint_ids.get((project, sprint)) if sprint else None,
            title=title,
            description=description or title,
            acceptance_criteria=None,
            complexity=complexity,
            priority=priority,
            status=status,
            estimated_hours=est,
            remaining_estimated_hours=remaining,
            planned_start=start,
            deadline=deadline,
            task_category=category,
            restricted_to_team_id=None,
            requires_project_membership=False,
            required_certifications=list(certifications),
        )
        for name, level, mandatory in skills or ():
            self.add(
                "task_required_skills",
                id=self.uid("trs", code, name),
                task_id=tid,
                skill_id=self.skill_ids[name],
                minimum_level=level,
                is_mandatory=mandatory,
                requires_verification=False,
                weight=1.0 if mandatory else 0.5,
            )
        return tid

    def _timesheets(
        self, task_id: uuid.UUID, emp: str, start: date, end: date, total: float, key: str
    ) -> None:
        days = []
        day = start
        while day <= end:
            if is_working_day(day, SUN_THU, set(self.holidays)):
                days.append(day)
            day += timedelta(days=1)
        if not days or total <= 0:
            return
        per_day = min(round(total / len(days) * 2) / 2, 7.5)
        remaining = total
        for i, day in enumerate(days):
            hours = per_day if i < len(days) - 1 else min(max(remaining, 0.5), 7.5)
            if remaining <= 0:
                break
            hours = min(hours, remaining)
            self.add(
                "timesheet_entries",
                id=self.uid("ts", key, day),
                task_id=task_id,
                employee_id=self.emp_ids[emp],
                entry_date=day,
                hours=round(hours, 2),
                description="Implementation work",
            )
            remaining = round(remaining - hours, 2)

    def _reviewer(self, project: str, emp: str) -> uuid.UUID:
        for candidate in (self.pm[project], "EMP-013", "EMP-002"):
            if candidate != emp:
                return self.emp_ids[candidate]
        raise AssertionError("no reviewer")  # pragma: no cover

    # history
    def _filler_history(self, spec: EmpSpec) -> tuple[Outcome, ...]:
        r = self.rng("history", spec.code)
        if spec.schedule == "none":
            return ()
        project_for = {
            "java": "HRP",
            "backend_api": "PAY",
            "frontend": spec.projects[0] if spec.projects else "ANA",
            "mobile": "MOB",
            "data": "ANA",
            "ml": "AIS",
            "llm": "AIS",
            "devops": "CLD",
            "cloud": "CLD",
            "qa": spec.projects[0] if spec.projects else "PAY",
        }
        outcomes = []
        for _ in range(r.randint(1, 3)):
            kind = r.choice(spec.kinds)
            on_time = r.random() < 0.8
            outcomes.append(
                Outcome(
                    kind=kind,
                    project=project_for[kind],
                    quality=float(r.randint(62, 84)),
                    complexity=r.choice(["low", "medium", "medium"]),
                    on_time=on_time,
                    delay=None if on_time else r.choice(["EMPLOYEE", "EXTERNAL_BLOCKER", "DEPENDENCY"]),
                    rework=r.choice([0, 0, 1]),
                )
            )
        return tuple(outcomes)

    def _history(self, company_id: uuid.UUID, spec: EmpSpec) -> None:
        outcomes = spec.history or (self._filler_history(spec) if spec.kinds else ())
        r = self.rng("history-dates", spec.code)
        n = len(outcomes)
        for i, o in enumerate(outcomes):
            code = self.next_code(f"H-{o.project}")
            _, _, titles = TASK_KINDS[o.kind]
            title = r.choice(titles).format(r.choice(SUBJECTS))
            est = float(r.choice([6, 8, 10, 12, 16, 20]))
            finish_day = self.d0 - timedelta(days=16 + (n - 1 - i) * 11 + r.randint(0, 3))
            duration = r.randint(3, 6)
            # On-time work finishes the day before its deadline; late work two days after it.
            deadline_day = finish_day + timedelta(days=1) if o.on_time else finish_day - timedelta(days=2)
            start_day = deadline_day - timedelta(days=duration)
            past_sprint = self.d0 - timedelta(days=28) <= finish_day <= self.d0 - timedelta(days=15)
            tid = self._task(
                company_id,
                code=code,
                project=o.project,
                kind=o.kind,
                title=title,
                status="DONE",
                complexity=o.complexity,
                priority=r.choice(["low", "medium", "high"]),
                est=est,
                remaining=0,
                start=self.at(start_day, 9),
                deadline=self.at(deadline_day, 17),
                sprint=1 if past_sprint else None,
            )
            self.add(
                "task_assignments",
                id=self.uid("assign", code, spec.code),
                task_id=tid,
                employee_id=self.emp_ids[spec.code],
                assignment_status="COMPLETED",
                planned_start=self.at(start_day, 9),
                planned_end=self.at(deadline_day, 17),
                allocated_hours=est,
                remaining_estimated_hours=0,
                assigned_at=self.at(start_day, 8),
            )
            actual = round(est * r.uniform(0.85, 1.25) * 2) / 2
            self._timesheets(tid, spec.code, start_day, finish_day, actual, f"{code}:{spec.code}")
            self.add(
                "task_outcomes",
                id=self.uid("outcome", code, spec.code),
                task_id=tid,
                employee_id=self.emp_ids[spec.code],
                quality_score=o.quality,
                accepted=o.accepted,
                rework_count=o.rework,
                on_time=o.on_time,
                delay_reason=o.delay if not o.on_time else "NONE",
                blocker_hours=float(r.choice([4, 6, 8]))
                if o.delay in ("EXTERNAL_BLOCKER", "DEPENDENCY")
                else 0.0,
                reviewer_id=self._reviewer(o.project, spec.code),
                reviewed_at=self.at(finish_day + timedelta(days=1), 12),
            )
            self.member(spec.code, o.project)
            for skill in TASK_KINDS[o.kind][0]:
                self.first_task_for_skill.setdefault((spec.code, skill), (tid, o.project))

    # current workload
    def _active_assignment(
        self,
        company_id: uuid.UUID,
        emp: str,
        project: str,
        kind: str,
        remaining: float,
        s: int,
        e: int,
        key: str,
    ) -> None:
        r = self.rng("active", key)
        code = self.next_code(f"A-{project}")
        start_day, end_day = self.wd(s), self.wd(e)
        logged = float(r.randint(2, 10)) if s < 0 else 0.0
        allocated = remaining + logged
        _, _, titles = TASK_KINDS[kind]
        tid = self._task(
            company_id,
            code=code,
            project=project,
            kind=kind,
            title=r.choice(titles).format(r.choice(SUBJECTS)),
            status="IN_PROGRESS",
            complexity=r.choice(["medium", "high"]),
            priority="medium",
            est=allocated,
            remaining=remaining,
            start=self.at(start_day, 9),
            deadline=self.at(end_day, 17),
            sprint=2,
        )
        self.add(
            "task_assignments",
            id=self.uid("assign", code, emp),
            task_id=tid,
            employee_id=self.emp_ids[emp],
            assignment_status="ACTIVE",
            planned_start=self.at(start_day, 9),
            planned_end=self.at(end_day, 17),
            allocated_hours=allocated,
            remaining_estimated_hours=remaining,
            assigned_at=self.at(start_day, 8),
        )
        if logged:
            self._timesheets(tid, emp, start_day, self.wd(-1), logged, f"{code}:{emp}")
        self.member(emp, project)

    def _active_work(self, company_id: uuid.UUID, spec: EmpSpec) -> None:
        if spec.status != "ACTIVE" or spec.schedule == "none":
            return
        if spec.code in SCENARIO_ACTIVE:
            for i, (project, kind, remaining, s, e) in enumerate(SCENARIO_ACTIVE[spec.code]):
                self._active_assignment(
                    company_id, spec.code, project, kind, remaining, s, e, f"{spec.code}:{i}"
                )
            return
        if not spec.kinds or spec.flags & {"pair_unknown", "estimated_remaining"}:
            return
        r = self.rng("filler-active", spec.code)
        project = spec.projects[0] if spec.projects else "PAY"
        for i in range(r.randint(1, 2)):
            self._active_assignment(
                company_id,
                spec.code,
                project,
                r.choice(spec.kinds),
                float(r.randint(4, 14)),
                -r.randint(1, 4),
                r.randint(3, 7),
                f"{spec.code}:{i}",
            )

    def _special_workload(self, company_id: uuid.UUID) -> None:
        # Multi-assignee task without an employee-level split: workload must be reported as UNCERTAIN.
        code = self.next_code("A-HRP")
        start, end = self.wd(-2), self.wd(5)
        tid = self._task(
            company_id,
            code=code,
            project="HRP",
            kind="java",
            title="Implement leave balance integration",
            status="IN_PROGRESS",
            complexity="high",
            priority="high",
            est=40,
            remaining=30,
            start=self.at(start, 9),
            deadline=self.at(end, 17),
            sprint=2,
        )
        for emp in ("EMP-014", "EMP-015"):
            self.add(
                "task_assignments",
                id=self.uid("assign", code, emp),
                task_id=tid,
                employee_id=self.emp_ids[emp],
                assignment_status="ACTIVE",
                planned_start=self.at(start, 9),
                planned_end=self.at(end, 17),
                allocated_hours=None,
                remaining_estimated_hours=None,
                assigned_at=self.at(start, 8),
            )
            self._timesheets(tid, emp, start, self.wd(-1), 5, f"{code}:{emp}")
        # Remaining effort unknown, but allocation known: estimated as allocated - logged (ESTIMATED).
        code = self.next_code("A-PAY")
        start, end = self.wd(-2), self.wd(4)
        tid = self._task(
            company_id,
            code=code,
            project="PAY",
            kind="backend_api",
            title="Implement payout webhook handler",
            status="IN_PROGRESS",
            complexity="medium",
            priority="medium",
            est=16,
            remaining=None,
            start=self.at(start, 9),
            deadline=self.at(end, 17),
            sprint=2,
        )
        self.add(
            "task_assignments",
            id=self.uid("assign", code, "EMP-016"),
            task_id=tid,
            employee_id=self.emp_ids["EMP-016"],
            assignment_status="ACTIVE",
            planned_start=self.at(start, 9),
            planned_end=self.at(end, 17),
            allocated_hours=16,
            remaining_estimated_hours=None,
            assigned_at=self.at(start, 8),
        )
        self._timesheets(tid, "EMP-016", start, self.wd(-1), 6, f"{code}:EMP-016")

    def _scenario_tasks(self, company_id: uuid.UUID) -> None:
        for t in SCENARIO_TASKS:
            self._task(
                company_id,
                code=t.code,
                project=t.project,
                kind=None,
                title=t.title,
                status="TODO",
                complexity=t.complexity,
                priority=t.priority,
                est=t.hours,
                remaining=t.hours,
                start=self.at(self.wd(t.start_day), 9),
                deadline=self.at(self.wd(t.end_day), 17),
                sprint=2,
                description=t.description,
                skills=t.skills,
                category=t.category,
                certifications=t.certifications,
            )
        for code, depends_on, dep_type in TASK_DEPENDENCIES:
            self.add(
                "task_dependencies",
                id=self.uid("dependency", code, depends_on),
                task_id=self.uid("task", code),
                depends_on_task_id=self.uid("task", depends_on),
                dependency_type=dep_type,
            )

    # profile details
    def _profile_details(self, spec: EmpSpec) -> None:
        eid = self.emp_ids[spec.code]
        for name, _level, status, months in spec.skills:
            es_id = self.uid("emp_skill", spec.code, name)
            if status == "VERIFIED":
                self.add(
                    "employee_skill_evidence",
                    id=self.uid("evidence", spec.code, name, "assessment"),
                    employee_skill_id=es_id,
                    evidence_type="ASSESSMENT",
                    project_id=None,
                    task_id=None,
                    verification_status="VERIFIED",
                    notes="Technical assessment",
                    created_at=self.now - timedelta(days=round(30.44 * months)),
                )
            if (spec.code, name) in self.first_task_for_skill:
                tid, project = self.first_task_for_skill[(spec.code, name)]
                self.add(
                    "employee_skill_evidence",
                    id=self.uid("evidence", spec.code, name, "task"),
                    employee_skill_id=es_id,
                    evidence_type="TASK_OUTCOME",
                    project_id=self.project_ids[project],
                    task_id=tid,
                    verification_status="VERIFIED",
                    notes="Delivered reviewed task using this skill",
                    created_at=self.now - timedelta(days=120),
                )
        experiences = list(spec.experiences)
        if not experiences and spec.kinds:
            skills = tuple(s for s in TASK_KINDS[spec.kinds[0]][0] if any(s == k[0] for k in spec.skills))
            if skills:
                experiences.append(
                    Exp(
                        "Previous employer (fictional)",
                        spec.title,
                        max(spec.years - 2, 1),
                        skills,
                        spec.hired_months_ago,
                    )
                )
        for i, exp in enumerate(experiences):
            xid = self.uid("experience", spec.code, i)
            end = self.d0 - timedelta(days=30 * exp.ended_months_ago)
            self.add(
                "employee_experiences",
                id=xid,
                employee_id=eid,
                organization_or_project=exp.org,
                role=exp.role,
                description=f"{exp.role} working with {', '.join(exp.skills)}",
                relevant_start_date=end - timedelta(days=round(365.25 * exp.years)),
                relevant_end_date=end,
            )
            for skill in exp.skills:
                self.add("employee_experience_skills", experience_id=xid, skill_id=self.skill_ids[skill])
        for i, (name, issuer, ctype, cred_skill) in enumerate(spec.credentials):
            issued = self.d0 - timedelta(days=400)
            self.add(
                "employee_credentials",
                id=self.uid("credential", spec.code, i),
                employee_id=eid,
                name=name,
                issuer=issuer,
                credential_type=ctype,
                skill_id=self.skill_ids.get(cred_skill) if cred_skill else None,
                issued_at=issued,
                expires_at=issued + timedelta(days=3 * 365),
                verification_status="VERIFIED",
            )

    def _schedule_days(self, spec: EmpSpec) -> list[tuple[int, time, time, int]]:
        if spec.schedule == "none":
            return []
        if spec.schedule == "part_time":
            return [(w, time(10), time(14), 0) for w in (6, 0, 1, 2)]
        if spec.schedule == "mon_fri":
            return [(w, time(9), time(17, 30), 30) for w in (0, 1, 2, 3, 4)]
        return [(w, time(9), time(17), 60) for w in SUN_THU]

    def _schedule_and_attendance(self, spec: EmpSpec) -> None:
        eid = self.emp_ids[spec.code]
        days = self._schedule_days(spec)
        for weekday, start, end, brk in days:
            self.add(
                "work_schedule_days",
                id=self.uid("schedule", spec.code, weekday),
                employee_id=eid,
                weekday=weekday,
                start_time=start,
                end_time=end,
                break_minutes=brk,
            )
        if spec.status != "ACTIVE" or not days:
            return
        tz = ZoneInfo(spec.timezone)
        by_weekday = {d[0]: d for d in days}
        r = self.rng("attendance", spec.code)
        today = self.now.astimezone(tz).date()
        holidays = set(self.holidays)
        on_leave = self._leave_days(spec)
        for back in range(14, -1, -1):
            day = today - timedelta(days=back)
            shift = by_weekday.get(day.weekday())
            if shift is None or day in holidays:
                continue
            is_today = day == today
            key = self.uid("attendance", spec.code, day)
            if day in on_leave:
                self.add(
                    "attendance_records",
                    id=key,
                    employee_id=eid,
                    work_date=day,
                    check_in=None,
                    check_out=None,
                    attendance_status="ON_LEAVE",
                )
                continue
            if is_today and "no_attendance_today" in spec.flags:
                continue
            scenario = spec.code in {s.code for s in SCENARIO_EMPLOYEES}
            if not is_today and not scenario and r.random() < 0.04:
                self.add(
                    "attendance_records",
                    id=key,
                    employee_id=eid,
                    work_date=day,
                    check_in=None,
                    check_out=None,
                    attendance_status="ABSENT",
                )
                continue
            check_in = datetime.combine(day, shift[1], tzinfo=tz) + timedelta(minutes=r.randint(-20, 10))
            if is_today:
                check_in = min(check_in, self.now - timedelta(minutes=5))
                check_out = None
            else:
                check_out = datetime.combine(day, shift[2], tzinfo=tz) + timedelta(minutes=r.randint(-10, 35))
            status = "REMOTE" if r.random() < 0.15 else "PRESENT"
            self.add(
                "attendance_records",
                id=key,
                employee_id=eid,
                work_date=day,
                check_in=check_in,
                check_out=check_out,
                attendance_status=status,
            )

    def _leave_specs(self, spec: EmpSpec) -> list[tuple[str, datetime, datetime, str]]:
        """(leave_type, start, end, status) for an employee."""
        leaves: list[tuple[str, datetime, datetime, str]] = []
        if "leave_window" in spec.flags:
            leaves.append(
                ("ANNUAL", self.at(self.wd(1), 0), self.at(self.wd(5) + timedelta(days=1), 0), "APPROVED")
            )
        if "leave_today" in spec.flags:
            leaves.append(
                ("CASUAL", self.at(self.d0, 0), self.at(self.d0 + timedelta(days=1), 0), "APPROVED")
            )
        if "pending_leave" in spec.flags:
            leaves.append(
                ("ANNUAL", self.at(self.wd(2), 0), self.at(self.wd(2) + timedelta(days=1), 0), "REQUESTED")
            )
        if "rejected_leave" in spec.flags:
            leaves.append(
                ("ANNUAL", self.at(self.wd(1), 0), self.at(self.wd(3) + timedelta(days=1), 0), "REJECTED")
            )
        r = self.rng("past-leave", spec.code)
        if spec.status == "ACTIVE" and r.random() < 0.35:
            start = self.wd(-r.randint(5, 9))
            leaves.append(("ANNUAL", self.at(start, 0), self.at(start + timedelta(days=2), 0), "APPROVED"))
        return leaves

    def _leave_days(self, spec: EmpSpec) -> set[date]:
        days: set[date] = set()
        for _, start, end, status in self._leave_specs(spec):
            if status != "APPROVED":
                continue
            day = start.date()
            while self.at(day, 0) < end:
                days.add(day)
                day += timedelta(days=1)
        return days

    def _leaves_and_commitments(self, spec: EmpSpec) -> None:
        eid = self.emp_ids[spec.code]
        for i, (ltype, start, end, status) in enumerate(self._leave_specs(spec)):
            self.add(
                "leave_records",
                id=self.uid("leave", spec.code, i),
                employee_id=eid,
                leave_type=ltype,
                start_at=start,
                end_at=end,
                status=status,
            )
        if "commitment" in spec.flags:
            self.add(
                "employee_commitments",
                id=self.uid("commitment", spec.code),
                employee_id=eid,
                title="On-call support rotation",
                start_at=self.at(self.wd(1), 0),
                end_at=self.at(self.wd(3) + timedelta(days=1), 0),
                hours=6.0,
            )

    def _memberships(self) -> None:
        for project, emps in sorted(self.members.items()):
            for emp in sorted(emps | {self.pm[project]}):
                self.add(
                    "project_members",
                    project_id=self.project_ids[project],
                    employee_id=self.emp_ids[emp],
                    role="manager" if emp == self.pm[project] else "developer",
                )

    # second tenant
    # periodic reviews and metric snapshots (derived from the reviewed outcomes)
    def review_periods(self) -> list[tuple[date, date]]:
        """Two closed 4-week review periods before the reference day."""
        return [
            (self.d0 - timedelta(days=70), self.d0 - timedelta(days=43)),
            (self.d0 - timedelta(days=42), self.d0 - timedelta(days=15)),
        ]

    def _performance_reviews(self, company_id: uuid.UUID) -> None:
        by_employee: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for o in self.ds.rows["task_outcomes"]:
            by_employee.setdefault(o["employee_id"], []).append(o)
        task_project = {t["id"]: t["project_id"] for t in self.ds.rows["tasks"]}
        code_of = {v: k for k, v in self.emp_ids.items()}
        project_code = {v: k for k, v in self.project_ids.items()}
        for emp_id in sorted(by_employee, key=lambda e: code_of[e]):
            emp_code = code_of[emp_id]
            for start, end in self.review_periods():
                period = [o for o in by_employee[emp_id] if start <= o["reviewed_at"].date() <= end]
                if not period:
                    continue
                qualities = [o["quality_score"] for o in period if o["quality_score"] is not None]
                on_time = [o["on_time"] for o in period if o["on_time"] is not None]
                accepted = [o["accepted"] for o in period if o["accepted"] is not None]
                metrics = {
                    "tasks_completed": float(len(period)),
                    "avg_quality": round(sum(qualities) / len(qualities), 2) if qualities else None,
                    "on_time_rate": round(100 * sum(on_time) / len(on_time), 2) if on_time else None,
                    "acceptance_rate": round(100 * sum(accepted) / len(accepted), 2) if accepted else None,
                    "rework_rate": round(100 * sum(1 for o in period if o["rework_count"]) / len(period), 2),
                }
                for metric, value in metrics.items():
                    self.add(
                        "performance_metrics",
                        id=self.uid("metric", emp_code, metric, start),
                        company_id=company_id,
                        employee_id=emp_id,
                        metric=metric,
                        period_start=start,
                        period_end=end,
                        value=value,
                        sample_size=len(period),
                        computed_at=self.at(end + timedelta(days=1), 8),
                    )
                main_project = project_code[task_project[period[-1]["task_id"]]]
                r = self.rng("evaluation", emp_code, start)
                self.add(
                    "performance_evaluations",
                    id=self.uid("evaluation", emp_code, start),
                    company_id=company_id,
                    employee_id=emp_id,
                    sprint_id=None,
                    reviewer_id=self._reviewer(main_project, emp_code),
                    period_start=start,
                    period_end=end,
                    technical_quality=metrics["avg_quality"],
                    delivery=metrics["on_time_rate"],
                    collaboration=float(r.randint(65, 95)),
                    summary=f"Periodic technical review covering {len(period)} reviewed task(s).",
                    created_at=self.at(end + timedelta(days=2), 10),
                )

    def _second_company(self) -> None:
        c = SECOND_COMPANY
        cid = self.uid("company", c["code"])
        self.add(
            "companies",
            id=cid,
            code=c["code"],
            name=c["name"],
            timezone=c["timezone"],
            working_weekdays=c["working_weekdays"],
        )
        did = self.uid("dept", c["code"], "Engineering")
        tid = self.uid("team", c["code"], "Core")
        self.add("departments", id=did, company_id=cid, name="Engineering")
        self.add("teams", id=tid, department_id=did, name="Core")
        skill_ids = {}
        for name in ("Python", "FastAPI", "PostgreSQL"):
            skill_ids[name] = self.uid("skill", c["code"], name)
            self.add(
                "skills",
                id=skill_ids[name],
                company_id=cid,
                name=name,
                category="engineering",
                description=name,
                aliases=[],
            )
        tz = ZoneInfo(c["timezone"])
        for code, name, title in c["employees"]:
            eid = self.uid("employee", code)
            self.add(
                "employees",
                id=eid,
                company_id=cid,
                employee_code=code,
                full_name=name,
                email=f"{code.lower()}@delta.example",
                job_title=title,
                department_id=did,
                team_id=tid,
                hire_date=self.d0 - timedelta(days=900),
                experience_years=9,
                employment_status="ACTIVE",
                timezone=c["timezone"],
            )
            for skill, sid in skill_ids.items():
                self.add(
                    "employee_skills",
                    id=self.uid("emp_skill", code, skill),
                    employee_id=eid,
                    skill_id=sid,
                    proficiency_level=96,
                    verification_status="VERIFIED",
                    verified_at=self.now,
                    last_assessed_at=self.now,
                )
            for weekday in c["working_weekdays"]:
                self.add(
                    "work_schedule_days",
                    id=self.uid("schedule", code, weekday),
                    employee_id=eid,
                    weekday=weekday,
                    start_time=time(9),
                    end_time=time(17),
                    break_minutes=30,
                )
        pid = self.uid("project", c["code"], "CORE")
        self.add(
            "projects",
            id=pid,
            company_id=cid,
            code="CORE",
            name="Delta Core Platform",
            description=None,
            status="ACTIVE",
            project_manager_id=self.uid("employee", "DLT-001"),
        )
        task_id = self.uid("task", c["code"], "DLT-T-1")
        start = datetime.combine(self.d0 + timedelta(days=1), time(9), tzinfo=tz)
        self.add(
            "tasks",
            id=task_id,
            company_id=cid,
            task_code="DLT-T-1",
            project_id=pid,
            sprint_id=None,
            title="Build billing API",
            description="FastAPI billing endpoints",
            acceptance_criteria=None,
            complexity="high",
            priority="high",
            status="TODO",
            estimated_hours=12,
            remaining_estimated_hours=12,
            planned_start=start,
            deadline=start + timedelta(days=4),
            task_category="backend_development",
            restricted_to_team_id=None,
            requires_project_membership=False,
            required_certifications=[],
        )
        for skill in ("Python", "FastAPI"):
            self.add(
                "task_required_skills",
                id=self.uid("trs", "DLT-T-1", skill),
                task_id=task_id,
                skill_id=skill_ids[skill],
                minimum_level=75,
                is_mandatory=True,
                requires_verification=False,
                weight=1.0,
            )


def generate_demo_dataset(reference_date: date, seed: int) -> DemoDataset:
    return DemoDataGenerator(reference_date, seed).build()

# ===========================================================================
# Database Seeder
# ===========================================================================

class SeedRefusedError(SprintMatchError):
    code = "SEED_REFUSED"


def guard_environment(environment: str) -> None:
    if environment == "production":
        raise SeedRefusedError("Demo data must never be loaded into a production environment")


def has_data(engine: Engine) -> bool:
    with Session(engine) as s:
        return s.execute(select(func.count()).select_from(CompanyModel)).scalar_one() > 0


def reset_all_data(engine: Engine) -> None:
    """Remove all application rows (schema and alembic_version are kept)."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


def load_dataset(engine: Engine, dataset: DemoDataset) -> dict[str, int]:
    tables = Base.metadata.tables
    with Session(engine) as s, s.begin():
        for name in TABLE_ORDER:
            rows = dataset.rows[name]
            if rows:
                s.execute(insert(tables[name]), rows)
    return dataset.counts()


def seed_demo_data(
    engine: Engine, *, environment: str, reference_date: date, seed: int, reset: bool = False
) -> DemoDataset:
    guard_environment(environment)
    if has_data(engine):
        if not reset:
            raise ConflictError("Database already contains data; use --reset to replace it")
        reset_all_data(engine)
    dataset = generate_demo_dataset(reference_date, seed)
    load_dataset(engine, dataset)
    return dataset

# ===========================================================================
# Dataset Consistency Validator
# ===========================================================================

# name -> (description, SQL returning a count of violations)
CHECKS: dict[str, tuple[str, str]] = {
    "employee_department_same_company": (
        "Employees reference departments of their own company",
        """SELECT count(*) FROM employees e JOIN departments d ON d.id = e.department_id
           WHERE d.company_id <> e.company_id""",
    ),
    "employee_team_in_department": (
        "Employee team belongs to the employee's department",
        """SELECT count(*) FROM employees e JOIN teams t ON t.id = e.team_id
           WHERE e.department_id IS NOT NULL AND t.department_id <> e.department_id""",
    ),
    "employee_skill_same_company": (
        "Employee skills come from the employee's company catalog",
        """SELECT count(*) FROM employee_skills es JOIN employees e ON e.id = es.employee_id
           JOIN skills s ON s.id = es.skill_id WHERE s.company_id <> e.company_id""",
    ),
    "task_project_same_company": (
        "Tasks reference projects of the same company",
        """SELECT count(*) FROM tasks t JOIN projects p ON p.id = t.project_id WHERE p.company_id <> t.company_id""",
    ),
    "task_sprint_same_project": (
        "Task sprint belongs to the task's project",
        """SELECT count(*) FROM tasks t JOIN sprints s ON s.id = t.sprint_id WHERE s.project_id <> t.project_id""",
    ),
    "assignment_same_company": (
        "Assignments link employees and tasks of the same company",
        """SELECT count(*) FROM task_assignments a JOIN tasks t ON t.id = a.task_id
           JOIN employees e ON e.id = a.employee_id WHERE e.company_id <> t.company_id""",
    ),
    "active_assignment_on_open_task": (
        "Active/planned assignments belong to tasks that are not DONE/CANCELLED",
        """SELECT count(*) FROM task_assignments a JOIN tasks t ON t.id = a.task_id
           WHERE a.assignment_status IN ('PLANNED','ACTIVE') AND t.status IN ('DONE','CANCELLED')""",
    ),
    "inactive_without_active_work": (
        "Inactive employees hold no active assignments",
        """SELECT count(*) FROM task_assignments a JOIN employees e ON e.id = a.employee_id
           WHERE a.assignment_status IN ('PLANNED','ACTIVE') AND e.employment_status <> 'ACTIVE'""",
    ),
    "assignment_remaining_within_allocation": (
        "Remaining effort never exceeds allocation",
        """SELECT count(*) FROM task_assignments
           WHERE allocated_hours IS NOT NULL AND remaining_estimated_hours > allocated_hours""",
    ),
    "completed_assignment_has_outcome": (
        "Every completed assignment has a reviewed outcome",
        """SELECT count(*) FROM task_assignments a LEFT JOIN task_outcomes o
             ON o.task_id = a.task_id AND o.employee_id = a.employee_id
           WHERE a.assignment_status = 'COMPLETED' AND o.id IS NULL""",
    ),
    "outcome_has_assignment": (
        "Every outcome corresponds to an assignment of that employee",
        """SELECT count(*) FROM task_outcomes o LEFT JOIN task_assignments a
             ON a.task_id = o.task_id AND a.employee_id = o.employee_id WHERE a.id IS NULL""",
    ),
    "outcome_on_done_task": (
        "Outcomes are only recorded for DONE tasks",
        """SELECT count(*) FROM task_outcomes o JOIN tasks t ON t.id = o.task_id WHERE t.status <> 'DONE'""",
    ),
    "outcome_delay_consistency": (
        "Late outcomes have a delay reason; on-time outcomes have none",
        """SELECT count(*) FROM task_outcomes WHERE (on_time = false AND (delay_reason IS NULL OR delay_reason = 'NONE'))
           OR (on_time = true AND delay_reason NOT IN ('NONE'))""",
    ),
    "timesheet_has_assignment": (
        "Timesheet entries belong to an assignment of that employee",
        """SELECT count(*) FROM timesheet_entries ts LEFT JOIN task_assignments a
             ON a.task_id = ts.task_id AND a.employee_id = ts.employee_id WHERE a.id IS NULL""",
    ),
    "timesheet_daily_hours_plausible": (
        "An employee logs at most 12 hours per day",
        """SELECT count(*) FROM (SELECT employee_id, entry_date FROM timesheet_entries
           GROUP BY employee_id, entry_date HAVING sum(hours) > 12) x""",
    ),
    "attendance_not_during_approved_leave": (
        "No PRESENT/REMOTE attendance during approved leave",
        """SELECT count(*) FROM attendance_records a JOIN leave_records l ON l.employee_id = a.employee_id
           WHERE l.status = 'APPROVED' AND a.attendance_status IN ('PRESENT','REMOTE')
             AND a.check_in >= l.start_at AND a.check_in < l.end_at""",
    ),
    "attendance_checkin_matches_work_date": (
        "Check-in happens on the recorded work date (employee timezone)",
        """SELECT count(*) FROM attendance_records a JOIN employees e ON e.id = a.employee_id
           WHERE a.check_in IS NOT NULL AND (a.check_in AT TIME ZONE e.timezone)::date <> a.work_date""",
    ),
    "approved_leaves_do_not_overlap": (
        "Approved leaves of one employee do not overlap",
        """SELECT count(*) FROM leave_records a JOIN leave_records b ON a.employee_id = b.employee_id
           AND a.id < b.id AND a.status = 'APPROVED' AND b.status = 'APPROVED'
           AND a.start_at < b.end_at AND b.start_at < a.end_at""",
    ),
    "schedule_valid_shift": (
        "Schedules have positive net working time",
        """SELECT count(*) FROM work_schedule_days
           WHERE EXTRACT(EPOCH FROM (CASE WHEN end_time > start_time THEN end_time - start_time
                 ELSE end_time - start_time + interval '24 hours' END)) / 60 <= break_minutes""",
    ),
    "weekly_workload_plausible": (
        "Active remaining effort per employee is below 120h",
        """SELECT count(*) FROM (SELECT employee_id FROM task_assignments
           WHERE assignment_status IN ('PLANNED','ACTIVE') GROUP BY employee_id
           HAVING sum(coalesce(remaining_estimated_hours, 0)) > 120) x""",
    ),
    "reviewer_same_company": (
        "Outcome reviewers belong to the same company",
        """SELECT count(*) FROM task_outcomes o JOIN employees e ON e.id = o.employee_id
           JOIN employees r ON r.id = o.reviewer_id WHERE r.company_id <> e.company_id""",
    ),
    "dependency_same_company": (
        "Task dependencies link tasks of the same company",
        """SELECT count(*) FROM task_dependencies d JOIN tasks a ON a.id = d.task_id
           JOIN tasks b ON b.id = d.depends_on_task_id WHERE a.company_id <> b.company_id""",
    ),
    "dependency_no_direct_cycle": (
        "No pair of tasks depends on each other",
        """SELECT count(*) FROM task_dependencies a JOIN task_dependencies b
           ON a.task_id = b.depends_on_task_id AND a.depends_on_task_id = b.task_id""",
    ),
    "evaluation_same_company": (
        "Performance evaluations: employee and reviewer belong to the evaluation's company",
        """SELECT count(*) FROM performance_evaluations v JOIN employees e ON e.id = v.employee_id
           LEFT JOIN employees r ON r.id = v.reviewer_id
           WHERE e.company_id <> v.company_id OR (r.id IS NOT NULL AND r.company_id <> v.company_id)""",
    ),
    "metric_snapshot_matches_outcomes": (
        "avg_quality snapshots equal the mean of the reviewed outcomes in their period",
        """SELECT count(*) FROM performance_metrics m
           JOIN employees e ON e.id = m.employee_id
           JOIN companies c ON c.id = e.company_id
           LEFT JOIN LATERAL (
             SELECT avg(o.quality_score) AS q FROM task_outcomes o
             WHERE o.employee_id = m.employee_id AND o.quality_score IS NOT NULL
               AND (o.reviewed_at AT TIME ZONE c.timezone)::date BETWEEN m.period_start AND m.period_end
           ) x ON true
           WHERE m.metric = 'avg_quality'
             AND (m.value IS DISTINCT FROM round(x.q, 2))""",
    ),
}


# Informational: reported, never fail validation (documented data-gap scenarios are expected here).
INFO_CHECKS: dict[str, tuple[str, str]] = {
    "active_employees_without_schedule": (
        "Active employees without a work schedule (availability will be UNKNOWN)",
        """SELECT count(*) FROM employees e WHERE e.employment_status = 'ACTIVE'
           AND NOT EXISTS (SELECT 1 FROM work_schedule_days s WHERE s.employee_id = e.id)""",
    ),
    "active_assignments_without_remaining_estimate": (
        "Active assignments without an employee-level remaining estimate",
        """SELECT count(*) FROM task_assignments
           WHERE assignment_status IN ('PLANNED','ACTIVE') AND remaining_estimated_hours IS NULL""",
    ),
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    description: str
    violations: int
    informational: bool = False

    @property
    def passed(self) -> bool:
        return self.informational or self.violations == 0


def validate_dataset(engine: Engine) -> list[CheckResult]:
    results = []
    with engine.connect() as conn:
        for checks, info in ((CHECKS, False), (INFO_CHECKS, True)):
            for name, (description, sql) in checks.items():
                count = int(conn.execute(text(sql)).scalar_one())
                results.append(CheckResult(name, description, count, informational=info))
    return results

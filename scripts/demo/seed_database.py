"""Seed the distinct 'NovaTech Cloud & AI Solutions' database (sprintmatch_novatech).

100% English - All names, skills, aliases, departments, and records are in pure English.
Populates 20 distinct employees, 7 departments, realistic projects, sprints, tasks,
task outcomes, schedules, leave records, credentials, and performance metrics.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.domain.enums import (
    AssignmentStatus,
    AttendanceStatus,
    CredentialType,
    EmploymentStatus,
    EvidenceType,
    LeaveStatus,
    LeaveType,
    ProjectStatus,
    SkillVerificationStatus,
    SprintStatus,
    TaskCategory,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
)
from app.infrastructure.database.models import (
    AttendanceRecordModel,
    Base,
    CompanyModel,
    DepartmentModel,
    EmployeeCredentialModel,
    EmployeeExperienceModel,
    EmployeeExperienceSkillModel,
    EmployeeModel,
    EmployeeSkillModel,
    LeaveRecordModel,
    PerformanceEvaluationModel,
    PerformanceMetricModel,
    ProjectMemberModel,
    ProjectModel,
    SkillModel,
    SprintModel,
    TaskAssignmentModel,
    TaskModel,
    TaskOutcomeModel,
    TaskRequiredSkillModel,
    TeamModel,
    WorkScheduleDayModel,
)

DB_URL = "postgresql+psycopg://sprintmatch:sprintmatch_dev_pw@localhost:55432/sprintmatch_novatech"
engine = create_engine(DB_URL)


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"novatech:{name}")


def seed_novatech() -> None:
    print(f"Connecting to {DB_URL}...")
    with Session(engine) as session:
        # Check if already seeded - truncate for fresh clean English data
        existing_comp = session.execute(
            select(CompanyModel).where(CompanyModel.code.in_(["NOV", "NDS"]))
        ).scalar_one_or_none()

        if existing_comp:
            print("Truncating existing tables for fresh 100% English seeding...")
            tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            session.execute(text(f"TRUNCATE {tables} CASCADE"))
            session.commit()

        print("1. Creating Company: NovaTech Cloud & AI Solutions (NDS)...")
        comp = CompanyModel(
            id=uid("company:nov"),
            code="NDS",
            name="NovaTech Cloud & AI Solutions",
            timezone="Africa/Cairo",
            working_weekdays=[6, 0, 1, 2, 3],  # Sun to Thu
        )
        session.add(comp)
        session.flush()

        print("2. Creating Departments & Teams (100% English)...")
        dept_defs = [
            ("Cloud Infrastructure & SRE", ["Kubernetes & DevOps Crew", "Cloud Security"]),
            ("Artificial Intelligence & ML", ["Generative AI & LLM Systems", "Data Science"]),
            ("Core Backend & Microservices", ["High-Throughput APIs", "Data Systems"]),
            ("Mobile Systems & Edge", ["Cross-Platform Mobile Team", "Native Edge"]),
            ("Frontend & Product Experience", ["Design Systems & UI", "Web Applications"]),
            ("Quality Engineering & Automation", ["SDET & E2E Automation", "Performance QA"]),
            ("Enterprise Solutions & ERP", ["Odoo & Enterprise Implementation"]),
        ]

        depts: dict[str, DepartmentModel] = {}
        teams: dict[str, TeamModel] = {}
        for dname, tnames in dept_defs:
            d = DepartmentModel(id=uid(f"dept:{dname}"), company_id=comp.id, name=dname)
            session.add(d)
            depts[dname] = d
            session.flush()
            for tname in tnames:
                t = TeamModel(id=uid(f"team:{tname}"), department_id=d.id, name=tname)
                session.add(t)
                teams[tname] = t
        session.flush()

        print("3. Creating Comprehensive Skills Catalog (100% English)...")
        skill_defs = [
            ("Kubernetes", "devops", ["k8s", "kubernets", "k8", "container-orchestration"]),
            ("Docker", "devops", ["doker", "dockr", "containers"]),
            ("AWS", "cloud", ["amazon web services", "ec2", "s3", "ecs", "aws-cloud"]),
            ("Terraform", "devops", ["terform", "tf", "iac"]),
            ("Python", "language", ["py", "python3", "python-dev"]),
            ("FastAPI", "backend_framework", ["fast-api", "fastapi-framework", "async-python"]),
            ("PostgreSQL", "database", ["psql", "postgres", "relational-db"]),
            ("Redis", "database", ["rediss", "redis-cache", "in-memory-cache"]),
            ("Flutter", "mobile", ["fluter", "flutter-app", "mobile-app"]),
            ("Dart", "language", ["dart-lang", "dart3"]),
            ("React", "frontend_framework", ["reack", "reactjs", "react.js"]),
            ("Next.js", "frontend_framework", ["nxtjs", "nextjs", "next-js"]),
            ("TypeScript", "language", ["ts", "typescript-lang"]),
            ("Node.js", "backend_framework", ["node", "nodejs", "node-js"]),
            ("Playwright", "qa", ["playwrite", "playwright-test", "e2e-testing"]),
            ("Cypress", "qa", ["cypress-io", "cypress-e2e"]),
            ("Odoo", "backend_framework", ["odoo erp", "odoo 17", "erp", "odoo-enterprise"]),
            ("Machine Learning", "ai", ["ml", "pytorch", "machine-learning"]),
            ("LangChain", "ai", ["lang-chain", "llm-framework"]),
            ("CI/CD", "devops", ["github actions", "gitlab ci", "pipelines", "ci-cd"]),
            ("Microservices", "backend_framework", ["distributed-systems", "microservice-architecture"]),
            ("Kafka", "data", ["event-streaming", "apache-kafka"]),
            ("Linux & Bash", "devops", ["linux", "bash", "shell-scripting"]),
            ("Tailwind CSS", "frontend_framework", ["tailwind", "css-framework"]),
        ]

        skills: dict[str, SkillModel] = {}
        for sname, cat, aliases in skill_defs:
            s = SkillModel(
                id=uid(f"skill:{sname}"),
                company_id=comp.id,
                name=sname,
                category=cat,
                description=f"Enterprise proficiency in {sname}",
                aliases=aliases,
            )
            session.add(s)
            skills[sname] = s
        session.flush()

        print("4. Creating 20 Distinct Employees with Realistic, Proportional Skill Gradients...")
        # Varied, proportional proficiencies (96%, 90%, 85%, 78%, 70%, 65%) across junior, mid, senior and leads
        emp_defs = [
            {
                "code": "NOV-101",
                "name": "Dr. Marwan El-Saeed",
                "title": "Principal Cloud & DevOps Architect",
                "email": "marwan.elsaeed@novatech.ai",
                "dept": "Cloud Infrastructure & SRE",
                "team": "Kubernetes & DevOps Crew",
                "exp": 12.0,
                "hire": date(2021, 1, 10),
                "skills": [("Kubernetes", 98), ("Docker", 94), ("AWS", 96), ("Terraform", 92), ("CI/CD", 88), ("Linux & Bash", 85)],
                "quality": 98.5,
                "ontime": True,
                "cred": ("AWS Certified Solutions Architect - Professional", "Amazon Web Services"),
                "past_exp": ("Vodafone Intelligent Solutions", "Senior Cloud Architect", 4, ["Kubernetes", "Docker", "AWS", "Terraform", "CI/CD"]),
            },
            {
                "code": "NOV-102",
                "name": "Layla El-Sherif",
                "title": "Staff AI & LLM Engineer",
                "email": "layla.elsherif@novatech.ai",
                "dept": "Artificial Intelligence & ML",
                "team": "Generative AI & LLM Systems",
                "exp": 8.5,
                "hire": date(2022, 3, 1),
                "skills": [("Python", 98), ("Machine Learning", 95), ("LangChain", 92), ("FastAPI", 85), ("Docker", 78)],
                "quality": 97.0,
                "ontime": True,
                "cred": ("Google Professional Machine Learning Engineer", "Google Cloud"),
                "past_exp": ("Instabug", "Senior AI Research Engineer", 3, ["Python", "Machine Learning", "LangChain", "FastAPI"]),
            },
            {
                "code": "NOV-103",
                "name": "Ziad El-Kady",
                "title": "Senior Mobile Engineer (Flutter Lead)",
                "email": "ziad.elkady@novatech.ai",
                "dept": "Mobile Systems & Edge",
                "team": "Cross-Platform Mobile Team",
                "exp": 6.5,
                "hire": date(2022, 6, 15),
                "skills": [("Flutter", 96), ("Dart", 94), ("FastAPI", 82), ("Docker", 74)],
                "quality": 95.0,
                "ontime": True,
                "cred": ("Certified Mobile Application Developer", "Flutter Foundation"),
                "past_exp": ("Swvl", "Senior Mobile Engineer", 3, ["Flutter", "Dart", "FastAPI"]),
            },
            {
                "code": "NOV-104",
                "name": "Nadine Fahmy",
                "title": "Lead Backend & Distributed Systems",
                "email": "nadine.fahmy@novatech.ai",
                "dept": "Core Backend & Microservices",
                "team": "High-Throughput APIs",
                "exp": 7.0,
                "hire": date(2022, 8, 1),
                "skills": [("FastAPI", 96), ("Python", 94), ("PostgreSQL", 90), ("Microservices", 85), ("Redis", 78), ("Kafka", 70)],
                "quality": 96.0,
                "ontime": True,
                "cred": ("Confluent Certified Developer for Apache Kafka", "Confluent"),
                "past_exp": ("Fawry Banking & Payment", "Senior Backend Engineer", 4, ["Python", "FastAPI", "PostgreSQL", "Microservices", "Redis"]),
            },
            {
                "code": "NOV-105",
                "name": "Youssef El-Menshawy",
                "title": "Senior Frontend & UX Architect",
                "email": "youssef.elmenshawy@novatech.ai",
                "dept": "Frontend & Product Experience",
                "team": "Design Systems & UI",
                "exp": 6.0,
                "hire": date(2022, 10, 1),
                "skills": [("React", 96), ("Next.js", 92), ("TypeScript", 88), ("Tailwind CSS", 82), ("Node.js", 75)],
                "quality": 94.5,
                "ontime": True,
                "cred": ("Meta Certified Frontend Developer", "Meta"),
                "past_exp": ("Valu", "Frontend Tech Lead", 3, ["React", "Next.js", "TypeScript", "Tailwind CSS"]),
            },
            {
                "code": "NOV-106",
                "name": "Rana Mamdouh",
                "title": "Senior SDET & QA Automation Lead",
                "email": "rana.mamdouh@novatech.ai",
                "dept": "Quality Engineering & Automation",
                "team": "SDET & E2E Automation",
                "exp": 5.5,
                "hire": date(2023, 1, 15),
                "skills": [("Playwright", 96), ("Cypress", 90), ("Python", 84), ("CI/CD", 78)],
                "quality": 98.0,
                "ontime": True,
                "cred": ("ISTQB Certified Test Automation Engineer", "ISTQB"),
                "past_exp": ("Paymob", "Senior Automation QA", 3, ["Playwright", "Cypress", "Python", "CI/CD"]),
            },
            {
                "code": "NOV-107",
                "name": "Tarek El-Awadi",
                "title": "Senior ERP & Odoo Consultant",
                "email": "tarek.elawadi@novatech.ai",
                "dept": "Enterprise Solutions & ERP",
                "team": "Odoo & Enterprise Implementation",
                "exp": 8.0,
                "hire": date(2022, 2, 1),
                "skills": [("Odoo", 98), ("Python", 90), ("PostgreSQL", 85), ("Docker", 75)],
                "quality": 95.5,
                "ontime": True,
                "cred": ("Odoo 17 Certified Expert", "Odoo S.A."),
                "past_exp": ("BDO Digital", "Lead Odoo Consultant", 4, ["Odoo", "Python", "PostgreSQL"]),
            },
            {
                "code": "NOV-108",
                "name": "Hazem Ghanem",
                "title": "Site Reliability & Cloud Engineer",
                "email": "hazem.ghanem@novatech.ai",
                "dept": "Cloud Infrastructure & SRE",
                "team": "Kubernetes & DevOps Crew",
                "exp": 4.5,
                "hire": date(2023, 4, 1),
                "skills": [("Kubernetes", 88), ("Docker", 92), ("AWS", 84), ("Terraform", 80), ("CI/CD", 76)],
                "quality": 92.0,
                "ontime": True,
                "cred": ("Certified Kubernetes Administrator (CKA)", "Linux Foundation"),
                "past_exp": ("Orange Egypt", "DevOps Engineer", 2, ["Kubernetes", "Docker", "AWS", "Terraform"]),
            },
            {
                "code": "NOV-109",
                "name": "Mayy Mostafa",
                "title": "Data Platform & Pipeline Engineer",
                "email": "mayy.mostafa@novatech.ai",
                "dept": "Artificial Intelligence & ML",
                "team": "Data Science",
                "exp": 4.0,
                "hire": date(2023, 5, 15),
                "skills": [("Python", 92), ("PostgreSQL", 88), ("Kafka", 82), ("Docker", 74)],
                "quality": 91.5,
                "ontime": True,
                "cred": ("Databricks Certified Data Engineer", "Databricks"),
                "past_exp": ("Trella", "Data Engineer", 2, ["Python", "PostgreSQL", "Kafka"]),
            },
            {
                "code": "NOV-110",
                "name": "Amir El-Gohary",
                "title": "Junior Backend Developer",
                "email": "amir.elgohary@novatech.ai",
                "dept": "Core Backend & Microservices",
                "team": "High-Throughput APIs",
                "exp": 1.5,
                "hire": date(2024, 7, 1),
                "skills": [("Python", 76), ("FastAPI", 72), ("PostgreSQL", 68)],
                "quality": 81.0,
                "ontime": False,  # lower performance test persona
                "cred": None,
                "past_exp": ("Raya Holding", "Junior Python Developer", 1, ["Python", "FastAPI"]),
            },
            {
                "code": "NOV-111",
                "name": "Salma Zahran",
                "title": "Junior Mobile Developer",
                "email": "salma.zahran@novatech.ai",
                "dept": "Mobile Systems & Edge",
                "team": "Cross-Platform Mobile Team",
                "exp": 1.0,
                "hire": date(2025, 1, 15),
                "skills": [("Flutter", 74), ("Dart", 70)],
                "quality": 80.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Digital Egypt", "Junior Flutter Developer", 1, ["Flutter", "Dart"]),
            },
            {
                "code": "NOV-112",
                "name": "Karim El-Shennawy",
                "title": "Senior Cloud Security Specialist",
                "email": "karim.elshennawy@novatech.ai",
                "dept": "Cloud Infrastructure & SRE",
                "team": "Cloud Security",
                "exp": 6.0,
                "hire": date(2023, 3, 1),
                "skills": [("Kubernetes", 94), ("AWS", 92), ("Docker", 88), ("Terraform", 82)],
                "quality": 94.0,
                "ontime": True,
                "cred": ("AWS Certified Security - Specialty", "Amazon Web Services"),
                "past_exp": ("Trend Micro", "Security Cloud Engineer", 3, ["Kubernetes", "AWS", "Docker", "Terraform"]),
            },
            {
                "code": "NOV-113",
                "name": "Hoda Rashwan",
                "title": "Senior Backend Developer",
                "email": "hoda.rashwan@novatech.ai",
                "dept": "Core Backend & Microservices",
                "team": "High-Throughput APIs",
                "exp": 5.5,
                "hire": date(2023, 6, 1),
                "skills": [("FastAPI", 94), ("Python", 92), ("PostgreSQL", 88), ("Redis", 82)],
                "quality": 93.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Etisalat Egypt", "Backend Developer", 3, ["Python", "FastAPI", "PostgreSQL", "Redis"]),
            },
            {
                "code": "NOV-114",
                "name": "Omar El-Toukhy",
                "title": "Mid-level Frontend Developer",
                "email": "omar.eltoukhy@novatech.ai",
                "dept": "Frontend & Product Experience",
                "team": "Web Applications",
                "exp": 3.0,
                "hire": date(2023, 9, 1),
                "skills": [("React", 84), ("TypeScript", 80), ("Next.js", 75)],
                "quality": 87.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Robusta Studio", "Frontend Developer", 2, ["React", "TypeScript"]),
            },
            {
                "code": "NOV-115",
                "name": "Engy Shoukry",
                "title": "Mid-level QA Automation Engineer",
                "email": "engy.shoukry@novatech.ai",
                "dept": "Quality Engineering & Automation",
                "team": "Performance QA",
                "exp": 3.0,
                "hire": date(2024, 2, 1),
                "skills": [("Playwright", 84), ("Python", 78), ("CI/CD", 72)],
                "quality": 88.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Lynx Software", "QA Automation Engineer", 2, ["Playwright", "Python"]),
            },
            {
                "code": "NOV-116",
                "name": "Ahmed El-Shahawy",
                "title": "DevOps Systems Engineer",
                "email": "ahmed.elshahawy@novatech.ai",
                "dept": "Cloud Infrastructure & SRE",
                "team": "Kubernetes & DevOps Crew",
                "exp": 3.5,
                "hire": date(2024, 4, 1),
                "skills": [("Docker", 88), ("Linux & Bash", 92), ("AWS", 78)],
                "quality": 89.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Telecom Egypt", "Junior Systems Engineer", 2, ["Docker", "Linux & Bash", "AWS"]),
            },
            {
                "code": "NOV-117",
                "name": "Nour Badr",
                "title": "Machine Learning Engineer",
                "email": "nour.badr@novatech.ai",
                "dept": "Artificial Intelligence & ML",
                "team": "Generative AI & LLM Systems",
                "exp": 3.0,
                "hire": date(2024, 5, 1),
                "skills": [("Python", 88), ("Machine Learning", 82), ("FastAPI", 76)],
                "quality": 90.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Si-Ware Systems", "AI Software Engineer", 2, ["Python", "Machine Learning"]),
            },
            {
                "code": "NOV-118",
                "name": "Mahmoud Eissa",
                "title": "Odoo Implementation Developer",
                "email": "mahmoud.eissa@novatech.ai",
                "dept": "Enterprise Solutions & ERP",
                "team": "Odoo & Enterprise Implementation",
                "exp": 4.0,
                "hire": date(2023, 11, 1),
                "skills": [("Odoo", 88), ("Python", 82), ("PostgreSQL", 78)],
                "quality": 91.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Trust ERP", "Odoo Developer", 2, ["Odoo", "Python"]),
            },
            {
                "code": "NOV-119",
                "name": "Dina El-Sawy",
                "title": "Senior UI/UX & React Specialist",
                "email": "dina.elsawy@novatech.ai",
                "dept": "Frontend & Product Experience",
                "team": "Design Systems & UI",
                "exp": 5.0,
                "hire": date(2023, 7, 1),
                "skills": [("React", 90), ("TypeScript", 86), ("Next.js", 80)],
                "quality": 93.0,
                "ontime": True,
                "cred": None,
                "past_exp": ("Nagwa Education", "UI/UX Engineer", 3, ["React", "TypeScript", "Next.js"]),
            },
            {
                "code": "NOV-120",
                "name": "Khaled Abdel-Moneim",
                "title": "Database Administrator & Data Engineer",
                "email": "khaled.abdelmoneim@novatech.ai",
                "dept": "Core Backend & Microservices",
                "team": "Data Systems",
                "exp": 9.0,
                "hire": date(2022, 1, 1),
                "skills": [("PostgreSQL", 98), ("Redis", 88), ("Python", 82)],
                "quality": 97.0,
                "ontime": True,
                "cred": ("PostgreSQL Certified Professional", "PostgreSQL Guild"),
                "past_exp": ("CIB Egypt", "Principal DBA", 5, ["PostgreSQL", "Redis", "Python"]),
            },
        ]

        emps: dict[str, EmployeeModel] = {}
        now = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)

        for ed in emp_defs:
            emp = EmployeeModel(
                id=uid(f"emp:{ed['code']}"),
                company_id=comp.id,
                employee_code=ed["code"],
                full_name=ed["name"],
                email=ed["email"],
                job_title=ed["title"],
                department_id=depts[ed["dept"]].id,
                team_id=teams[ed["team"]].id,
                hire_date=ed["hire"],
                experience_years=Decimal(str(ed["exp"])),
                employment_status=EmploymentStatus.ACTIVE,
                timezone="Africa/Cairo",
            )
            session.add(emp)
            emps[ed["code"]] = emp
            session.flush()

            # Assign skills
            for sname, level in ed["skills"]:
                es = EmployeeSkillModel(
                    id=uid(f"empskill:{ed['code']}:{sname}"),
                    employee_id=emp.id,
                    skill_id=skills[sname].id,
                    proficiency_level=level,
                    verification_status=SkillVerificationStatus.VERIFIED,
                    verified_at=now,
                    last_assessed_at=now,
                )
                session.add(es)

            # Assign weekly schedule (Sun..Thu 9am..5pm = 35 net hours after 1h break)
            for wday in [0, 1, 2, 3, 6]:
                ws = WorkScheduleDayModel(
                    id=uid(f"sched:{ed['code']}:{wday}"),
                    employee_id=emp.id,
                    weekday=wday,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                    break_minutes=60,
                )
                session.add(ws)

            # Assign credential if any
            if ed.get("cred"):
                cname, cissuer = ed["cred"]
                cred = EmployeeCredentialModel(
                    id=uid(f"cred:{ed['code']}"),
                    employee_id=emp.id,
                    credential_type=CredentialType.CERTIFICATION,
                    name=cname,
                    issuer=cissuer,
                    issued_at=date(2023, 6, 1),
                    expires_at=date(2027, 6, 1),
                    verification_status=SkillVerificationStatus.VERIFIED,
                )
                session.add(cred)

            # Assign past experience with linked skill technologies in employee_experience_skills!
            if ed.get("past_exp"):
                cname, rname, yrs, exp_skill_names = ed["past_exp"]
                exp = EmployeeExperienceModel(
                    id=uid(f"exp:{ed['code']}"),
                    employee_id=emp.id,
                    organization_or_project=cname,
                    role=rname,
                    relevant_start_date=date(2019, 1, 1),
                    relevant_end_date=date(2019 + yrs, 1, 1),
                    description=f"Led high-impact initiatives and enterprise delivery at {cname}.",
                )
                session.add(exp)
                session.flush()

                for esn in exp_skill_names:
                    if esn in skills:
                        session.add(
                            EmployeeExperienceSkillModel(
                                experience_id=exp.id,
                                skill_id=skills[esn].id,
                            )
                        )

        session.flush()

        print("5. Adding Realistic Staggered Leaves Across Teams...")
        # Karim El-Shennawy (NOV-112) is ON LEAVE for the full sprint month
        session.add(
            LeaveRecordModel(
                id=uid("leave:nov-112"),
                employee_id=emps["NOV-112"].id,
                leave_type=LeaveType.ANNUAL,
                start_at=datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
                end_at=datetime(2026, 11, 30, 23, 59, tzinfo=UTC),
                status=LeaveStatus.APPROVED,
            )
        )
        # Omar El-Toukhy (NOV-114) has 2-day approved personal leave (14h)
        session.add(
            LeaveRecordModel(
                id=uid("leave:nov-114"),
                employee_id=emps["NOV-114"].id,
                leave_type=LeaveType.CASUAL,
                start_at=datetime(2026, 11, 3, 7, 0, tzinfo=UTC),
                end_at=datetime(2026, 11, 4, 15, 0, tzinfo=UTC),
                status=LeaveStatus.APPROVED,
            )
        )
        # Engy Shoukry (NOV-115) has 1-day approved medical leave (7h)
        session.add(
            LeaveRecordModel(
                id=uid("leave:nov-115"),
                employee_id=emps["NOV-115"].id,
                leave_type=LeaveType.SICK,
                start_at=datetime(2026, 11, 5, 7, 0, tzinfo=UTC),
                end_at=datetime(2026, 11, 5, 15, 0, tzinfo=UTC),
                status=LeaveStatus.APPROVED,
            )
        )
        # Dina El-Sawy (NOV-119) has 1-day approved annual leave (7h)
        session.add(
            LeaveRecordModel(
                id=uid("leave:nov-119"),
                employee_id=emps["NOV-119"].id,
                leave_type=LeaveType.ANNUAL,
                start_at=datetime(2026, 11, 4, 7, 0, tzinfo=UTC),
                end_at=datetime(2026, 11, 4, 15, 0, tzinfo=UTC),
                status=LeaveStatus.APPROVED,
            )
        )

        print("6. Creating Projects, Sprints, and Project Memberships...")
        proj_apex = ProjectModel(
            id=uid("proj:apex"),
            company_id=comp.id,
            code="APEX",
            name="Apex Cloud Infrastructure",
            description="Enterprise Kubernetes migration, AWS landing zones and service mesh",
            status=ProjectStatus.ACTIVE,
            project_manager_id=emps["NOV-101"].id,
        )
        proj_novapay = ProjectModel(
            id=uid("proj:novapay"),
            company_id=comp.id,
            code="NPAY",
            name="NovaPay High-Throughput Gateway",
            description="FastAPI async payment engine, Redis distributed locks and Flutter app",
            status=ProjectStatus.ACTIVE,
            project_manager_id=emps["NOV-104"].id,
        )
        session.add_all([proj_apex, proj_novapay])
        session.flush()

        # Memberships for all 20 team members
        apex_members = ["NOV-101", "NOV-108", "NOV-112", "NOV-116"]
        novapay_members = [
            "NOV-102", "NOV-103", "NOV-104", "NOV-105", "NOV-106", "NOV-107",
            "NOV-109", "NOV-110", "NOV-111", "NOV-113", "NOV-114", "NOV-115",
            "NOV-117", "NOV-118", "NOV-119", "NOV-120"
        ]
        for ecode in apex_members:
            session.add(ProjectMemberModel(project_id=proj_apex.id, employee_id=emps[ecode].id, role="DevOps & Cloud Engineer"))
        for ecode in novapay_members:
            session.add(ProjectMemberModel(project_id=proj_novapay.id, employee_id=emps[ecode].id, role="Gateway Core Engineer"))
        session.flush()

        # Sprints
        sprint1 = SprintModel(
            id=uid("sprint:apex:1"),
            project_id=proj_apex.id,
            name="Sprint 12 - Cluster Foundation",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 25),
            status=SprintStatus.ACTIVE,
        )
        sprint2 = SprintModel(
            id=uid("sprint:novapay:1"),
            project_id=proj_novapay.id,
            name="Sprint 08 - Payment Core",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 25),
            status=SprintStatus.ACTIVE,
        )
        session.add_all([sprint1, sprint2])
        session.flush()

        # Historical Tasks to generate TaskOutcomes & link TaskRequiredSkills (Experience & Quality Evidence!)
        print("7. Generating Historical Tasks, Task Outcomes & Required Skills for Experience Evidence...")
        historical_task_specs = [
            ("HIST-101", "NOV-101", "Kubernetes cluster hardening and ingress control", "devops", 98.5, True, 0, ["Kubernetes", "Docker", "Linux & Bash"]),
            ("HIST-102", "NOV-101", "Terraform AWS networking and VPC baseline", "devops", 99.0, True, 0, ["Terraform", "AWS", "CI/CD"]),
            ("HIST-103", "NOV-102", "LLM prompt chaining and model evaluation", "machine_learning", 97.5, True, 0, ["Python", "LangChain", "Machine Learning"]),
            ("HIST-104", "NOV-102", "Vector database retrieval and embeddings pipeline", "machine_learning", 96.5, True, 0, ["Python", "Machine Learning", "FastAPI"]),
            ("HIST-105", "NOV-103", "Flutter checkout payment UI refactor", "mobile_development", 95.0, True, 0, ["Flutter", "Dart"]),
            ("HIST-106", "NOV-103", "Biometric authentication in Flutter app", "mobile_development", 96.0, True, 0, ["Flutter", "Dart", "FastAPI"]),
            ("HIST-107", "NOV-104", "FastAPI transaction processing async worker", "backend_development", 96.5, True, 0, ["FastAPI", "Python", "PostgreSQL"]),
            ("HIST-108", "NOV-104", "PostgreSQL connection pooling and index optimization", "backend_development", 95.5, True, 0, ["PostgreSQL", "Python", "Redis"]),
            ("HIST-108B", "NOV-104", "Microservices distributed tracing and Kafka events", "backend_development", 97.0, True, 0, ["Microservices", "Kafka", "Python"]),
            ("HIST-108C", "NOV-104", "FastAPI idempotent refund middleware and transactional lock", "backend_development", 98.0, True, 0, ["FastAPI", "Python", "PostgreSQL", "Redis"]),
            ("HIST-109", "NOV-105", "Next.js dashboard server-side rendering migration", "frontend_development", 95.0, True, 0, ["React", "Next.js", "TypeScript"]),
            ("HIST-110", "NOV-106", "Playwright automated regression testing suite", "qa_testing", 98.0, True, 0, ["Playwright", "Python", "CI/CD"]),
            ("HIST-111", "NOV-107", "Odoo 17 tax and invoices localization customization", "backend_development", 96.0, True, 0, ["Odoo", "Python", "PostgreSQL"]),
            ("HIST-112", "NOV-108", "Docker container multi-stage build optimization", "devops", 92.0, True, 0, ["Docker", "Kubernetes", "CI/CD"]),
            ("HIST-113", "NOV-110", "Fix backend bug in login endpoint", "backend_development", 81.0, False, 3, ["Python", "FastAPI"]),
            ("HIST-114", "NOV-113", "Redis cache synchronization background service", "backend_development", 93.0, True, 0, ["Redis", "Python", "FastAPI"]),
            ("HIST-115", "NOV-114", "Web responsive checkout layout components", "frontend_development", 88.0, True, 0, ["React", "TypeScript"]),
            ("HIST-116", "NOV-115", "Performance smoke tests with Playwright", "qa_testing", 89.0, True, 0, ["Playwright", "CI/CD"]),
            ("HIST-117", "NOV-117", "Customer churn prediction model training", "machine_learning", 91.0, True, 0, ["Python", "Machine Learning"]),
            ("HIST-118", "NOV-118", "Odoo payroll custom report generator", "backend_development", 90.0, True, 0, ["Odoo", "Python"]),
            ("HIST-119", "NOV-119", "UI Design System accessible button tokens", "frontend_development", 94.0, True, 0, ["React", "Tailwind CSS", "TypeScript"]),
            ("HIST-120", "NOV-120", "Database sharding and vacuum tuning", "backend_development", 97.5, True, 0, ["PostgreSQL", "Redis"]),
        ]

        for tcode, ecode, title, cat, qscore, ontime, rework, tskills in historical_task_specs:
            t = TaskModel(
                id=uid(f"task:{tcode}"),
                company_id=comp.id,
                task_code=tcode,
                project_id=proj_apex.id if "devops" in cat else proj_novapay.id,
                sprint_id=sprint1.id if "devops" in cat else sprint2.id,
                title=title,
                status=TaskStatus.DONE,
                task_category=TaskCategory(cat),
                complexity=TaskComplexity.HIGH if qscore > 95 else TaskComplexity.MEDIUM,
                priority=TaskPriority.HIGH,
                estimated_hours=Decimal("30.0"),
                remaining_estimated_hours=Decimal("0.0"),
            )
            session.add(t)
            session.flush()

            # Add required skills to historical task so task_similarity() can match them!
            for sname in tskills:
                if sname in skills:
                    session.add(
                        TaskRequiredSkillModel(
                            id=uid(f"taskskill:{tcode}:{sname}"),
                            task_id=t.id,
                            skill_id=skills[sname].id,
                            minimum_level=70,
                            is_mandatory=True,
                            requires_verification=True,
                            weight=Decimal("1.0"),
                        )
                    )

            # Add completed assignment so project_history counts completed tasks on the project!
            session.add(
                TaskAssignmentModel(
                    id=uid(f"histassign:{tcode}"),
                    task_id=t.id,
                    employee_id=emps[ecode].id,
                    assignment_status=AssignmentStatus.COMPLETED,
                    planned_start=now - timedelta(days=20),
                    planned_end=now - timedelta(days=12),
                    allocated_hours=Decimal("30.0"),
                    remaining_estimated_hours=Decimal("0.0"),
                )
            )

            # Reviewer: Dr. Marwan reviews others; Nadine reviews Dr. Marwan
            rev_id = emps["NOV-104"].id if ecode == "NOV-101" else emps["NOV-101"].id
            outcome = TaskOutcomeModel(
                id=uid(f"outcome:{tcode}"),
                task_id=t.id,
                employee_id=emps[ecode].id,
                quality_score=Decimal(str(qscore)),
                accepted=True,
                rework_count=rework,
                on_time=ontime,
                delay_reason=None,
                blocker_hours=Decimal("0.0"),
                reviewer_id=rev_id,
                reviewed_at=now - timedelta(days=10),
            )
            session.add(outcome)

        print("8. Setting up Varied & Staggered Active Sprint Tasks & Commitments across Employees...")
        # Active assignments for current sprint window to produce realistic, varying capacity
        active_assignment_specs = [
            ("NPAY-021", "NOV-104", proj_novapay.id, sprint2.id, "High-Throughput Webhook Event Ingestion Engine", "backend_development", 14.0),
            ("APEX-011", "NOV-101", proj_apex.id, sprint1.id, "Kubernetes Cluster Multi-Region Mesh Setup", "devops", 10.5),
            ("NPAY-022", "NOV-102", proj_novapay.id, sprint2.id, "LLM Fraud Detection Pipeline Optimization", "machine_learning", 7.0),
            ("NPAY-023", "NOV-103", proj_novapay.id, sprint2.id, "Biometric Payment Authorization Screen & NFC Flow", "mobile_development", 14.0),
            ("NPAY-024", "NOV-105", proj_novapay.id, sprint2.id, "Design System Dark Mode & Accessible Charts", "frontend_development", 21.0),
            ("NPAY-025", "NOV-106", proj_novapay.id, sprint2.id, "Playwright End-to-End Regression Suite for Checkout", "qa_testing", 7.0),
            ("NPAY-026", "NOV-107", proj_novapay.id, sprint2.id, "Odoo 17 Enterprise Multi-Company Fiscal Settlement", "backend_development", 14.0),
            ("APEX-012", "NOV-108", proj_apex.id, sprint1.id, "Prometheus & Grafana Enterprise Telemetry Stack", "devops", 17.5),
            ("NPAY-027", "NOV-109", proj_novapay.id, sprint2.id, "Kafka Event Stream Deduplication Service", "machine_learning", 10.5),
            ("NPAY-028", "NOV-110", proj_novapay.id, sprint2.id, "API Response Pagination & Validation Helpers", "backend_development", 7.0),
            ("NPAY-029", "NOV-114", proj_novapay.id, sprint2.id, "Frontend Error Boundaries & Sentry Integration", "frontend_development", 7.0),
            ("NPAY-030", "NOV-115", proj_novapay.id, sprint2.id, "Automated Load Testing in Staging Environment", "qa_testing", 14.0),
            ("APEX-013", "NOV-116", proj_apex.id, sprint1.id, "Terraform CI/CD Pipeline Automation", "devops", 21.0),
            ("NPAY-031", "NOV-117", proj_novapay.id, sprint2.id, "Feature Store Pipeline for User Embedding", "machine_learning", 14.0),
            ("NPAY-032", "NOV-118", proj_novapay.id, sprint2.id, "Odoo Automated Bank Reconciliation Feed", "backend_development", 10.5),
            ("NPAY-033", "NOV-119", proj_novapay.id, sprint2.id, "Micro-frontend Shell Navigation Bar", "frontend_development", 7.0),
            ("NPAY-034", "NOV-120", proj_novapay.id, sprint2.id, "PostgreSQL Read Replica Lag Monitoring", "backend_development", 17.5),
        ]

        for tcode, ecode, pid, sid, title, cat, hrs in active_assignment_specs:
            atask = TaskModel(
                id=uid(f"task:{tcode}"),
                company_id=comp.id,
                task_code=tcode,
                project_id=pid,
                sprint_id=sid,
                title=title,
                status=TaskStatus.IN_PROGRESS,
                task_category=TaskCategory(cat),
                complexity=TaskComplexity.HIGH if hrs >= 14 else TaskComplexity.MEDIUM,
                priority=TaskPriority.HIGH,
                estimated_hours=Decimal(str(hrs)),
                remaining_estimated_hours=Decimal(str(hrs)),
            )
            session.add(atask)
            session.flush()

            assign = TaskAssignmentModel(
                id=uid(f"assign:{ecode}:{tcode}"),
                task_id=atask.id,
                employee_id=emps[ecode].id,
                assignment_status=AssignmentStatus.ACTIVE,
                planned_start=datetime(2026, 11, 3, 7, 0, tzinfo=UTC),
                planned_end=datetime(2026, 11, 6, 15, 0, tzinfo=UTC),
                allocated_hours=Decimal(str(hrs)),
                remaining_estimated_hours=Decimal(str(hrs)),
            )
            session.add(assign)

        # Overload Hoda Rashwan (NOV-113) with active heavy task assignment (40h)
        overload_task = TaskModel(
            id=uid("task:OVERLOAD-1"),
            company_id=comp.id,
            task_code="NOV-OVL-01",
            project_id=proj_novapay.id,
            sprint_id=sprint2.id,
            title="Massive High-Volume Settlement Pipeline Refactor",
            status=TaskStatus.IN_PROGRESS,
            task_category=TaskCategory.BACKEND_DEVELOPMENT,
            complexity=TaskComplexity.CRITICAL,
            priority=TaskPriority.URGENT,
            estimated_hours=Decimal("40.0"),
            remaining_estimated_hours=Decimal("40.0"),
        )
        session.add(overload_task)
        session.flush()

        task_assign_hoda = TaskAssignmentModel(
            id=uid("assign:hoda:1"),
            task_id=overload_task.id,
            employee_id=emps["NOV-113"].id,
            assignment_status=AssignmentStatus.ACTIVE,
            planned_start=datetime(2026, 11, 2, 7, 0, tzinfo=UTC),
            planned_end=datetime(2026, 11, 10, 15, 0, tzinfo=UTC),
            allocated_hours=Decimal("40.0"),
            remaining_estimated_hours=Decimal("40.0"),
        )
        session.add(task_assign_hoda)

        print("9. Creating Performance Evaluations and Metrics Snapshots (English)...")
        for ed in emp_defs:
            ecode = ed["code"]
            e = emps[ecode]
            rev_id = emps["NOV-104"].id if ecode == "NOV-101" else emps["NOV-101"].id
            eval_rec = PerformanceEvaluationModel(
                id=uid(f"eval:{ecode}"),
                company_id=comp.id,
                employee_id=e.id,
                sprint_id=sprint1.id,
                reviewer_id=rev_id,
                period_start=date(2026, 10, 1),
                period_end=date(2026, 10, 31),
                technical_quality=Decimal(str(ed["quality"])),
                delivery=Decimal("95.0" if ed["ontime"] else "78.0"),
                collaboration=Decimal("94.0"),
                summary=f"Key technical contributor in {ed['dept']}. Demonstrated high engineering standards and consistent velocity.",
            )
            session.add(eval_rec)

            # Snapshots
            for mname, mval in [
                ("avg_quality", ed["quality"]),
                ("on_time_rate", 98.0 if ed["ontime"] else 78.0),
                ("acceptance_rate", 100.0),
                ("rework_rate", 0.0 if ed["ontime"] else 15.0),
                ("tasks_completed", 12.0),
            ]:
                pm = PerformanceMetricModel(
                    id=uid(f"pmetric:{ecode}:{mname}"),
                    company_id=comp.id,
                    employee_id=e.id,
                    metric=mname,
                    period_start=date(2026, 10, 1),
                    period_end=date(2026, 10, 31),
                    value=Decimal(str(mval)),
                    sample_size=6,
                )
                session.add(pm)

        print("10. Creating Attendance Records...")
        # 10 attendance records per employee
        for ed in emp_defs:
            e = emps[ed["code"]]
            for day_offset in range(1, 11):
                wdate = date(2026, 10, 20) + timedelta(days=day_offset)
                if wdate.weekday() in [4, 5]:  # Fri, Sat
                    continue
                att = AttendanceRecordModel(
                    id=uid(f"att:{ed['code']}:{day_offset}"),
                    employee_id=e.id,
                    work_date=wdate,
                    check_in=datetime.combine(wdate, time(9, 0), tzinfo=UTC),
                    check_out=datetime.combine(wdate, time(17, 30), tzinfo=UTC),
                    attendance_status=AttendanceStatus.PRESENT,
                )
                session.add(att)

        session.commit()
        print("\nSUCCESS! Seeded 'sprintmatch_novatech' with 100% English enterprise data, varied skills, staggered capacity & rich experience.")

        # Output summary counts
        print("\nDatabase Statistics for 'sprintmatch_novatech':")
        for model in [
            CompanyModel, DepartmentModel, TeamModel, SkillModel, EmployeeModel,
            EmployeeSkillModel, EmployeeExperienceModel, EmployeeExperienceSkillModel,
            WorkScheduleDayModel, ProjectModel, ProjectMemberModel, SprintModel,
            TaskModel, TaskOutcomeModel, TaskRequiredSkillModel, TaskAssignmentModel,
            LeaveRecordModel, PerformanceEvaluationModel, PerformanceMetricModel
        ]:
            count = session.execute(select(text("COUNT(*)")).select_from(model)).scalar()
            print(f"  {model.__tablename__:<32} {count:>5}")


if __name__ == "__main__":
    seed_novatech()

"""Comprehensive blueprints for all 58 employees in SprintMatch database.
Guarantees distinct names, unique skill mixes, varied proficiencies, verified credentials,
detailed past experiences, performance evaluations, and realistic availability.
"""

from __future__ import annotations
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CAIRO_TZ = ZoneInfo("Africa/Cairo")
REF_TS = datetime(2026, 11, 3, 10, 0, 0, tzinfo=CAIRO_TZ)

V = "VERIFIED"
SD = "SELF_DECLARED"


def get_employee_blueprints() -> dict[str, dict]:
    return {
        # =========================================================================
        # 1. DELTA LABS GMBH (DLT) - BERLIN
        # =========================================================================
        "DLT-001": {
            "name": "Dr. Lena Weber",
            "email": "lena.weber@delta.example",
            "title": "Principal Systems Architect",
            "years": 11.5,
            "hire_date": date(2021, 2, 1),
            "skills": [
                ("Python", 96, V),
                ("FastAPI", 92, V),
                ("PostgreSQL", 90, V),
                ("Docker", 88, V),
                ("Kubernetes", 86, V),
            ],
            "credentials": [
                {
                    "name": "TOGAF 9 Certified Enterprise Architect",
                    "issuer": "The Open Group",
                    "skill": "Python",
                    "issued_at": date(2022, 5, 10),
                    "expires_at": date(2027, 5, 10),
                },
                {
                    "name": "AWS Certified Solutions Architect - Professional",
                    "issuer": "Amazon Web Services (AWS)",
                    "skill": "Python",
                    "issued_at": date(2023, 1, 15),
                    "expires_at": date(2026, 1, 15),
                },
            ],
            "experiences": [
                {
                    "org": "Siemens Healthineers - European Data Platform",
                    "role": "Lead Distributed Systems Architect",
                    "desc": "Designed resilient microservices architecture processing 40M daily medical telemetry events with high availability and GDPR compliance.",
                    "start_date": date(2018, 4, 1),
                    "end_date": date(2021, 1, 31),
                    "skills": ["Python", "FastAPI", "PostgreSQL"],
                }
            ],
            "eval": {
                "quality": 4.95,
                "delivery": 4.90,
                "collab": 4.85,
                "summary": "Lena sets the architectural standard across the engineering org. Exceptional technical foresight and systems design.",
            },
            "metrics": {
                "velocity_ratio": 1.12,
                "code_review_acceptance": 0.99,
                "bug_rate_per_kloc": 0.04,
                "on_time_delivery_rate": 0.98,
            },
        },
        "DLT-002": {
            "name": "Jonas Becker",
            "email": "jonas.becker@delta.example",
            "title": "Senior Backend Engineer",
            "years": 7.5,
            "hire_date": date(2022, 6, 15),
            "skills": [
                ("Python", 88, V),
                ("FastAPI", 90, V),
                ("PostgreSQL", 86, V),
                ("Docker", 80, V),
                ("Redis", 82, V),
            ],
            "credentials": [
                {
                    "name": "Certified Kubernetes Application Developer (CKAD)",
                    "issuer": "Cloud Native Computing Foundation (CNCF)",
                    "skill": "Docker",
                    "issued_at": date(2023, 9, 20),
                    "expires_at": date(2026, 9, 20),
                }
            ],
            "experiences": [
                {
                    "org": "Zalando Payments Platform - Checkout Service",
                    "role": "Senior Software Engineer",
                    "desc": "Implemented high-throughput async order authorization service handling Black Friday peak traffic without degradation.",
                    "start_date": date(2019, 7, 1),
                    "end_date": date(2022, 5, 31),
                    "skills": ["Python", "FastAPI", "PostgreSQL", "Redis"],
                }
            ],
            "eval": {
                "quality": 4.75,
                "delivery": 4.80,
                "collab": 4.70,
                "summary": "Jonas delivers clean, well-tested Python APIs on time and is a dependable technical mentor.",
            },
            "metrics": {
                "velocity_ratio": 1.05,
                "code_review_acceptance": 0.96,
                "bug_rate_per_kloc": 0.09,
                "on_time_delivery_rate": 0.94,
            },
        },
        "DLT-003": {
            "name": "Mia Schulz",
            "email": "mia.schulz@delta.example",
            "title": "Senior Data Platform Engineer",
            "years": 8.0,
            "hire_date": date(2022, 9, 1),
            "skills": [
                ("Python", 92, V),
                ("PostgreSQL", 88, V),
                ("FastAPI", 84, V),
                ("Data Engineering", 90, V),
                ("Docker", 78, V),
            ],
            "credentials": [
                {
                    "name": "Databricks Certified Data Engineer Professional",
                    "issuer": "Databricks",
                    "skill": "Data Engineering",
                    "issued_at": date(2023, 11, 5),
                    "expires_at": date(2026, 11, 5),
                }
            ],
            "experiences": [
                {
                    "org": "Delivery Hero - Global Analytics Hub",
                    "role": "Data Platform Specialist",
                    "desc": "Built real-time streaming ETL pipelines aggregating restaurant order events across 14 European markets.",
                    "start_date": date(2019, 1, 1),
                    "end_date": date(2022, 8, 15),
                    "skills": ["Python", "PostgreSQL", "Data Engineering"],
                }
            ],
            "eval": {
                "quality": 4.85,
                "delivery": 4.75,
                "collab": 4.80,
                "summary": "Mia combines deep database knowledge with high-performance Python streaming. Excellent contributor.",
            },
            "metrics": {
                "velocity_ratio": 1.08,
                "code_review_acceptance": 0.97,
                "bug_rate_per_kloc": 0.07,
                "on_time_delivery_rate": 0.95,
            },
        },

        # =========================================================================
        # 2. BACKEND & PAYMENTS CORE (NDS CAIRO)
        # =========================================================================
        "EMP-001": {
            "name": "Ahmed Hassan",
            "email": "ahmed.hassan@nds.example",
            "title": "Senior Backend Engineer",
            "years": 6.5,
            "hire_date": date(2021, 4, 15),
            "skills": [
                ("Python", 94, V),
                ("FastAPI", 90, V),
                ("PostgreSQL", 85, V),
                ("SQLAlchemy", 86, V),
                ("Redis", 75, V),
                ("Docker", 70, V),
                ("Git & GitHub", 88, V),
                ("Microservices Architecture", 82, V),
            ],
            "credentials": [
                {
                    "name": "AWS Certified Developer - Associate",
                    "issuer": "Amazon Web Services (AWS)",
                    "skill": "Python",
                    "issued_at": date(2023, 3, 12),
                    "expires_at": date(2026, 3, 12),
                },
                {
                    "name": "PostgreSQL 15 Certified Professional",
                    "issuer": "PostgreSQL Professional Group",
                    "skill": "PostgreSQL",
                    "issued_at": date(2022, 10, 18),
                },
            ],
            "experiences": [
                {
                    "org": "Fawry Banking & Payment - Merchant Aggregator",
                    "role": "Senior Backend Developer",
                    "desc": "Implemented idempotent payment webhook processors and settlement reconciliation microservices handling EGP 200M monthly volume.",
                    "start_date": date(2021, 5, 1),
                    "end_date": date(2023, 12, 31),
                    "skills": ["Python", "FastAPI", "PostgreSQL", "SQLAlchemy", "Redis"],
                },
                {
                    "org": "Swvl Mobility - Booking API Engine",
                    "role": "Software Engineer",
                    "desc": "Engineered async route search service and seat locking protocol with Redis distributed locks.",
                    "start_date": date(2019, 6, 1),
                    "end_date": date(2021, 4, 1),
                    "skills": ["Python", "FastAPI", "Redis"],
                },
            ],
            "eval": {
                "quality": 4.88,
                "delivery": 4.85,
                "collab": 4.90,
                "summary": "Ahmed is our top backend performer for FastAPI microservices. Extremely thorough in database design and concurrency safety.",
            },
            "metrics": {
                "velocity_ratio": 1.10,
                "code_review_acceptance": 0.98,
                "bug_rate_per_kloc": 0.06,
                "on_time_delivery_rate": 0.96,
            },
        },
        "EMP-002": {
            "name": "Karim Mostafa",
            "email": "karim.mostafa@nds.example",
            "title": "Backend Tech Lead",
            "years": 10.5,
            "hire_date": date(2020, 1, 10),
            "skills": [
                ("Python", 95, V),
                ("FastAPI", 92, V),
                ("PostgreSQL", 88, V),
                ("Redis", 82, V),
                ("Docker", 78, V),
                ("API Security", 76, V),
                ("Microservices Architecture", 94, V),
                ("Agile & Scrum", 90, V),
            ],
            "credentials": [
                {
                    "name": "Professional Scrum Master II (PSM II)",
                    "issuer": "Scrum.org",
                    "skill": "Agile & Scrum",
                    "issued_at": date(2022, 4, 20),
                },
                {
                    "name": "AWS Certified Solutions Architect - Associate",
                    "issuer": "Amazon Web Services (AWS)",
                    "skill": "Python",
                    "issued_at": date(2021, 8, 14),
                    "expires_at": date(2024, 8, 14),
                },
            ],
            "experiences": [
                {
                    "org": "Vodafone Intelligent Network - Core Charging Gateway",
                    "role": "Principal Software Architect",
                    "desc": "Designed ultra-low latency rating and charging engine with strict SLA and 99.999% availability.",
                    "start_date": date(2016, 2, 1),
                    "end_date": date(2019, 12, 31),
                    "skills": ["Python", "FastAPI", "PostgreSQL", "Redis"],
                }
            ],
            "eval": {
                "quality": 4.90,
                "delivery": 4.80,
                "collab": 4.95,
                "summary": "Karim is the cornerstone of the Payments team. Superb technical leadership, mentorship, and security rigor.",
            },
            "metrics": {
                "velocity_ratio": 1.04,
                "code_review_acceptance": 0.99,
                "bug_rate_per_kloc": 0.05,
                "on_time_delivery_rate": 0.95,
            },
            "commitment": {
                "title": "Sprint 14: Payment Gateway Core Settlement Architecture",
                "hours": 16.0,
            },
        },
        "EMP-003": {
            "name": "Nour El-Din Adel",
            "email": "nour.adel@nds.example",
            "title": "Backend Engineer",
            "years": 4.5,
            "hire_date": date(2022, 7, 1),
            "skills": [
                ("Python", 85, V),
                ("Django", 84, V),
                ("PostgreSQL", 74, V),
                ("Redis", 60, SD),
                ("Git & GitHub", 80, V),
            ],
            "experiences": [
                {
                    "org": "Bosta Logistics - Courier Dispatch Management",
                    "role": "Backend Developer",
                    "desc": "Built Django-based warehouse intake workflows and parcel routing management consoles.",
                    "start_date": date(2020, 8, 1),
                    "end_date": date(2022, 6, 1),
                    "skills": ["Python", "Django", "PostgreSQL"],
                }
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.70,
                "collab": 4.65,
                "summary": "Nour is a reliable Django and relational database developer with solid execution on admin interfaces.",
            },
            "metrics": {
                "velocity_ratio": 1.02,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.12,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-004": {
            "name": "Salma Farouk",
            "email": "salma.farouk@nds.example",
            "title": "Senior Backend Engineer",
            "years": 7.0,
            "hire_date": date(2021, 9, 1),
            "skills": [
                ("Python", 88, V),
                ("FastAPI", 85, V),
                ("PostgreSQL", 80, V),
                ("SQLAlchemy", 78, V),
                ("Docker", 68, SD),
            ],
            "credentials": [
                {
                    "name": "Python Institute PCAP – Certified Associate in Python Programming",
                    "issuer": "OpenEDG Python Institute",
                    "skill": "Python",
                    "issued_at": date(2022, 6, 14),
                }
            ],
            "experiences": [
                {
                    "org": "ValU Consumer Finance - Customer Underwriting API",
                    "role": "Senior Software Engineer",
                    "desc": "Implemented asynchronous credit scoring and identity verification integrations.",
                    "start_date": date(2019, 5, 1),
                    "end_date": date(2021, 8, 15),
                    "skills": ["Python", "FastAPI", "PostgreSQL", "SQLAlchemy"],
                }
            ],
            "eval": {
                "quality": 4.80,
                "delivery": 4.75,
                "collab": 4.85,
                "summary": "Salma consistently writes elegant, modular code. Excellent attention to edge cases and input validation.",
            },
            "metrics": {
                "velocity_ratio": 1.06,
                "code_review_acceptance": 0.96,
                "bug_rate_per_kloc": 0.08,
                "on_time_delivery_rate": 0.93,
            },
        },
        "EMP-005": {
            "name": "Youssef Ibrahim",
            "email": "youssef.ibrahim@nds.example",
            "title": "Backend Engineer",
            "years": 4.0,
            "hire_date": date(2023, 1, 15),
            "skills": [
                ("Python", 82, V),
                ("FastAPI", 80, V),
                ("PostgreSQL", 72, V),
                ("Redis", 66, SD),
            ],
            "experiences": [
                {
                    "org": "Cartona B2B Marketplace - Supplier API",
                    "role": "Backend Engineer",
                    "desc": "Built inventory sync endpoints and batch price update jobs using Celery and Redis.",
                    "start_date": date(2021, 3, 1),
                    "end_date": date(2022, 12, 31),
                    "skills": ["Python", "FastAPI", "PostgreSQL"],
                }
            ],
            "eval": {
                "quality": 4.55,
                "delivery": 4.60,
                "collab": 4.70,
                "summary": "Youssef demonstrates rapid growth and positive enthusiasm. Quick to absorb feedback.",
            },
            "metrics": {
                "velocity_ratio": 0.98,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.14,
                "on_time_delivery_rate": 0.90,
            },
        },
        "EMP-006": {
            "name": "Hana Mahmoud",
            "email": "hana.mahmoud@nds.example",
            "title": "Backend Engineer",
            "years": 3.0,
            "hire_date": date(2023, 8, 1),
            "skills": [
                ("Python", 80, V),
                ("FastAPI", 76, V),
                ("PostgreSQL", 62, SD),
                ("Git & GitHub", 78, V),
            ],
            "experiences": [
                {
                    "org": "MaxAB Wholesale - Order Management System",
                    "role": "Junior Backend Developer",
                    "desc": "Maintained retailer order endpoints and built PDF invoice generation service.",
                    "start_date": date(2021, 9, 1),
                    "end_date": date(2023, 7, 15),
                    "skills": ["Python", "FastAPI", "PostgreSQL"],
                }
            ],
            "eval": {
                "quality": 4.50,
                "delivery": 4.55,
                "collab": 4.80,
                "summary": "Hana is proactive and collaborative. Consistently meets sprint goals with high commitment.",
            },
            "metrics": {
                "velocity_ratio": 0.96,
                "code_review_acceptance": 0.92,
                "bug_rate_per_kloc": 0.15,
                "on_time_delivery_rate": 0.89,
            },
        },
        "EMP-007": {
            "name": "Tarek Samir",
            "email": "tarek.samir@nds.example",
            "title": "Senior Database Engineer",
            "years": 7.5,
            "hire_date": date(2021, 3, 1),
            "skills": [
                ("PostgreSQL", 94, V),
                ("SQLAlchemy", 82, V),
                ("Python", 78, V),
                ("FastAPI", 72, SD),
                ("Data Engineering", 68, SD),
            ],
            "credentials": [
                {
                    "name": "EnterpriseDB Certified PostgreSQL DBA",
                    "issuer": "EnterpriseDB",
                    "skill": "PostgreSQL",
                    "issued_at": date(2022, 1, 20),
                }
            ],
            "experiences": [
                {
                    "org": "Commercial International Bank (CIB) - Core Banking DW",
                    "role": "Lead Database Administrator",
                    "desc": "Tuned high-volume transactional SQL queries, configured declarative table partitioning, and managed PITR backup automation.",
                    "start_date": date(2017, 6, 1),
                    "end_date": date(2021, 2, 28),
                    "skills": ["PostgreSQL", "Data Engineering"],
                }
            ],
            "eval": {
                "quality": 4.90,
                "delivery": 4.85,
                "collab": 4.75,
                "summary": "Tarek is our PostgreSQL guru. Resolves query bottlenecks and index bloat with unmatched expertise.",
            },
            "metrics": {
                "velocity_ratio": 1.08,
                "code_review_acceptance": 0.98,
                "bug_rate_per_kloc": 0.05,
                "on_time_delivery_rate": 0.96,
            },
        },
        "EMP-008": {
            "name": "Laila Nabil",
            "email": "laila.nabil@nds.example",
            "title": "Backend Engineer",
            "years": 3.2,
            "hire_date": date(2023, 5, 10),
            "skills": [
                ("Python", 80, V),
                ("FastAPI", 78, V),
                ("PostgreSQL", 65, SD),
                ("Docker", 58, SD),
            ],
            "eval": {
                "quality": 4.55,
                "delivery": 4.60,
                "collab": 4.70,
                "summary": "Laila shows strong analytical capability and steady delivery cadence.",
            },
            "metrics": {
                "velocity_ratio": 0.97,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.13,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-009": {
            "name": "Mahmoud Fathy",
            "email": "mahmoud.fathy@nds.example",
            "title": "Backend Engineer",
            "years": 5.8,
            "hire_date": date(2022, 3, 1),
            "skills": [
                ("Python", 86, V),
                ("FastAPI", 80, V),
                ("Django", 75, V),
                ("PostgreSQL", 72, V),
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.70,
                "collab": 4.75,
                "summary": "Mahmoud is dependable and adapts easily between FastAPI and Django stacks.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.11,
                "on_time_delivery_rate": 0.92,
            },
        },

        # =========================================================================
        # 3. FRONTEND & MOBILE (NDS CAIRO)
        # =========================================================================
        "EMP-010": {
            "name": "Rania Saad",
            "email": "rania.saad@nds.example",
            "title": "Senior Mobile Engineer",
            "years": 7.0,
            "hire_date": date(2021, 8, 1),
            "skills": [
                ("Flutter", 94, V),
                ("JavaScript", 75, V),
                ("Test Automation", 75, V),
                ("CI/CD", 65, SD),
            ],
            "credentials": [
                {
                    "name": "Google Associate Android Developer",
                    "issuer": "Google",
                    "skill": "Flutter",
                    "issued_at": date(2022, 3, 15),
                }
            ],
            "experiences": [
                {
                    "org": "Thndr Digital Investment - Mobile Trading App",
                    "role": "Senior Flutter Developer",
                    "desc": "Built real-time stock price streaming tickers, biometric authentication, and Egyptian Stock Exchange order execution screens.",
                    "start_date": date(2020, 1, 1),
                    "end_date": date(2021, 7, 31),
                    "skills": ["Flutter"],
                }
            ],
            "eval": {
                "quality": 4.80,
                "delivery": 4.85,
                "collab": 4.80,
                "summary": "Rania is an exceptional mobile engineer with a meticulous eye for 60fps animations and smooth state handling.",
            },
            "metrics": {
                "velocity_ratio": 1.08,
                "code_review_acceptance": 0.96,
                "bug_rate_per_kloc": 0.07,
                "on_time_delivery_rate": 0.95,
            },
        },
        "EMP-011": {
            "name": "Mariam Ali",
            "email": "mariam.ali@nds.example",
            "title": "Senior Frontend Engineer",
            "years": 5.0,
            "hire_date": date(2022, 4, 1),
            "skills": [
                ("React", 92, V),
                ("Next.js", 88, V),
                ("TypeScript", 90, V),
                ("JavaScript", 94, V),
                ("Tailwind CSS", 88, V),
            ],
            "credentials": [
                {
                    "name": "Meta Certified Front-End Developer",
                    "issuer": "Meta",
                    "skill": "React",
                    "issued_at": date(2023, 2, 10),
                }
            ],
            "experiences": [
                {
                    "org": "Rabbit Mart - Consumer Grocery Web App",
                    "role": "Frontend Specialist",
                    "desc": "Developed Next.js 14 SSR merchant portal and live delivery tracker with responsive Tailwind CSS.",
                    "start_date": date(2021, 6, 1),
                    "end_date": date(2022, 3, 31),
                    "skills": ["React", "Next.js", "TypeScript", "JavaScript"],
                }
            ],
            "eval": {
                "quality": 4.85,
                "delivery": 4.90,
                "collab": 4.90,
                "summary": "Mariam is our lead contributor for modern React and Next.js applications. Superb UI velocity and pixel-perfect attention to detail.",
            },
            "metrics": {
                "velocity_ratio": 1.12,
                "code_review_acceptance": 0.97,
                "bug_rate_per_kloc": 0.06,
                "on_time_delivery_rate": 0.97,
            },
        },
        "EMP-012": {
            "name": "Omar Khaled",
            "email": "omar.khaled@nds.example",
            "title": "Frontend Engineer",
            "years": 4.2,
            "hire_date": date(2022, 9, 15),
            "skills": [
                ("React", 82, V),
                ("Next.js", 76, V),
                ("TypeScript", 80, V),
                ("JavaScript", 86, V),
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.65,
                "collab": 4.70,
                "summary": "Omar is a solid frontend contributor with clean state management and good code review habits.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.12,
                "on_time_delivery_rate": 0.91,
            },
        },

        # =========================================================================
        # 4. DEVOPS & CLOUD INFRASTRUCTURE (NDS CAIRO)
        # =========================================================================
        "EMP-013": {
            "name": "Mostafa Gamal",
            "email": "mostafa.gamal@nds.example",
            "title": "DevOps Lead",
            "years": 9.5,
            "hire_date": date(2020, 6, 1),
            "skills": [
                ("Docker", 92, V),
                ("Kubernetes", 90, V),
                ("Terraform", 86, V),
                ("AWS", 85, V),
                ("CI/CD", 88, V),
                ("Linux & Bash", 90, V),
            ],
            "credentials": [
                {
                    "name": "Certified Kubernetes Administrator (CKA)",
                    "issuer": "The Linux Foundation",
                    "skill": "Kubernetes",
                    "issued_at": date(2022, 8, 12),
                    "expires_at": date(2025, 8, 12),
                },
                {
                    "name": "AWS Certified Solutions Architect - Associate",
                    "issuer": "Amazon Web Services (AWS)",
                    "skill": "AWS",
                    "issued_at": date(2021, 11, 20),
                },
            ],
            "experiences": [
                {
                    "org": "Etisalat Misr - Cloud Modernization Initiative",
                    "role": "Principal DevOps Architect",
                    "desc": "Led multi-cluster Kubernetes migration on AWS with automated GitOps continuous deployment using ArgoCD.",
                    "start_date": date(2017, 3, 1),
                    "end_date": date(2020, 5, 15),
                    "skills": ["Docker", "Kubernetes", "AWS", "Terraform", "CI/CD"],
                }
            ],
            "eval": {
                "quality": 4.90,
                "delivery": 4.80,
                "collab": 4.90,
                "summary": "Mostafa is a seasoned cloud leader with deep operational resilience experience and infrastructure governance.",
            },
            "metrics": {
                "velocity_ratio": 1.05,
                "code_review_acceptance": 0.98,
                "bug_rate_per_kloc": 0.05,
                "on_time_delivery_rate": 0.94,
            },
            "commitment": {
                "title": "Cloud Migration Architecture & Governance Review",
                "hours": 16.0,
            },
        },
        "EMP-031": {
            "name": "Hesham Talaat",
            "email": "hesham.talaat@nds.example",
            "title": "Senior DevOps Engineer",
            "years": 7.2,
            "hire_date": date(2021, 10, 1),
            "skills": [
                ("Docker", 94, V),
                ("Kubernetes", 92, V),
                ("Terraform", 90, V),
                ("CI/CD", 92, V),
                ("AWS", 88, V),
                ("Linux & Bash", 88, V),
                ("Git & GitHub", 90, V),
            ],
            "credentials": [
                {
                    "name": "Certified Kubernetes Administrator (CKA)",
                    "issuer": "Cloud Native Computing Foundation (CNCF)",
                    "skill": "Kubernetes",
                    "issued_at": date(2023, 4, 15),
                    "expires_at": date(2026, 4, 15),
                },
                {
                    "name": "HashiCorp Certified: Terraform Associate (003)",
                    "issuer": "HashiCorp",
                    "skill": "Terraform",
                    "issued_at": date(2023, 7, 22),
                    "expires_at": date(2025, 7, 22),
                },
                {
                    "name": "AWS Certified Solutions Architect - Associate",
                    "issuer": "Amazon Web Services (AWS)",
                    "skill": "AWS",
                    "issued_at": date(2022, 11, 10),
                    "expires_at": date(2025, 11, 10),
                },
            ],
            "experiences": [
                {
                    "org": "Paymob Solutions - Multi-Region AWS EKS Platform",
                    "role": "Senior Cloud Infrastructure Engineer",
                    "desc": "Built production Terraform modules to provision multi-AZ AWS EKS clusters with autoscaling, IAM least-privilege, and automated canary deployments.",
                    "start_date": date(2021, 11, 1),
                    "end_date": date(2024, 2, 28),
                    "skills": ["Docker", "Kubernetes", "Terraform", "AWS", "CI/CD"],
                },
                {
                    "org": "Careem - Dispatch Cluster Reliability",
                    "role": "DevOps Engineer",
                    "desc": "Automated Docker image vulnerability scanning in GitHub Actions CI pipelines and reduced pipeline build time by 45%.",
                    "start_date": date(2019, 8, 1),
                    "end_date": date(2021, 9, 30),
                    "skills": ["Docker", "Kubernetes", "CI/CD"],
                },
            ],
            "eval": {
                "quality": 4.95,
                "delivery": 4.90,
                "collab": 4.88,
                "summary": "Hesham is our premier DevOps and Terraform specialist. Outstanding execution, proactive automation, and zero-downtime rollouts.",
            },
            "metrics": {
                "velocity_ratio": 1.15,
                "code_review_acceptance": 0.99,
                "bug_rate_per_kloc": 0.04,
                "on_time_delivery_rate": 0.98,
            },
        },
        "EMP-032": {
            "name": "Radwa Essam",
            "email": "radwa.essam@nds.example",
            "title": "DevOps Engineer",
            "years": 4.5,
            "hire_date": date(2022, 8, 1),
            "skills": [
                ("Docker", 85, V),
                ("Kubernetes", 80, V),
                ("CI/CD", 85, V),
                ("AWS", 80, V),
                ("Linux & Bash", 82, V),
            ],
            "credentials": [
                {
                    "name": "Docker Certified Associate (DCA)",
                    "issuer": "Docker Inc.",
                    "skill": "Docker",
                    "issued_at": date(2023, 5, 19),
                }
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.70,
                "collab": 4.80,
                "summary": "Radwa is very proficient in container lifecycle management and CI/CD pipelines.",
            },
            "metrics": {
                "velocity_ratio": 1.02,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.10,
                "on_time_delivery_rate": 0.93,
            },
        },
        "EMP-033": {
            "name": "Marwan Ashraf",
            "email": "marwan.ashraf@nds.example",
            "title": "Cloud Infrastructure Engineer",
            "years": 5.5,
            "hire_date": date(2022, 2, 1),
            "skills": [
                ("Terraform", 88, V),
                ("AWS", 90, V),
                ("Docker", 82, V),
                ("CI/CD", 80, V),
            ],
            "credentials": [
                {
                    "name": "AWS Certified Solutions Architect - Associate",
                    "issuer": "Amazon Web Services (AWS)",
                    "skill": "AWS",
                    "issued_at": date(2022, 9, 14),
                }
            ],
            "eval": {
                "quality": 4.75,
                "delivery": 4.80,
                "collab": 4.70,
                "summary": "Marwan possesses deep AWS VPC and networking knowledge. Dependable Terraform author.",
            },
            "metrics": {
                "velocity_ratio": 1.04,
                "code_review_acceptance": 0.95,
                "bug_rate_per_kloc": 0.08,
                "on_time_delivery_rate": 0.94,
            },
        },
        "EMP-034": {
            "name": "Hoda Soliman",
            "email": "hoda.soliman@nds.example",
            "title": "Site Reliability Engineer",
            "years": 6.0,
            "hire_date": date(2022, 5, 15),
            "skills": [
                ("Kubernetes", 88, V),
                ("Docker", 86, V),
                ("AWS", 84, V),
                ("CI/CD", 85, V),
                ("Python", 78, V),
            ],
            "eval": {
                "quality": 4.80,
                "delivery": 4.75,
                "collab": 4.85,
                "summary": "Hoda excels in monitoring, Prometheus alerting, and proactive cluster scaling.",
            },
            "metrics": {
                "velocity_ratio": 1.05,
                "code_review_acceptance": 0.96,
                "bug_rate_per_kloc": 0.07,
                "on_time_delivery_rate": 0.95,
            },
        },
        "EMP-035": {
            "name": "Karim Zaher",
            "email": "karim.zaher@nds.example",
            "title": "DevOps Automation Specialist",
            "years": 4.0,
            "hire_date": date(2023, 3, 1),
            "skills": [
                ("CI/CD", 88, V),
                ("Docker", 84, V),
                ("Kubernetes", 78, V),
                ("Terraform", 80, V),
                ("Python", 82, V),
            ],
            "eval": {
                "quality": 4.70,
                "delivery": 4.75,
                "collab": 4.75,
                "summary": "Karim writes clean automation scripts and maintains high CI pipeline reliability.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.11,
                "on_time_delivery_rate": 0.92,
            },
        },

        # =========================================================================
        # 5. FRONTEND & WEB EXPERIENCE (NDS CAIRO)
        # =========================================================================
        "EMP-036": {
            "name": "Nouran El-Sayed",
            "email": "nouran.elsayed@nds.example",
            "title": "Senior Frontend Engineer",
            "years": 6.5,
            "hire_date": date(2021, 11, 1),
            "skills": [
                ("React", 94, V),
                ("TypeScript", 92, V),
                ("Next.js", 90, V),
                ("JavaScript", 95, V),
                ("Tailwind CSS", 92, V),
            ],
            "credentials": [
                {
                    "name": "Meta Certified Front-End Developer",
                    "issuer": "Meta",
                    "skill": "React",
                    "issued_at": date(2022, 10, 5),
                }
            ],
            "experiences": [
                {
                    "org": "Halan MNT - Merchant Web Dashboard",
                    "role": "Lead Frontend Developer",
                    "desc": "Built Next.js 14 merchant dashboard with real-time loan approval workflows and financial charts.",
                    "start_date": date(2021, 1, 1),
                    "end_date": date(2023, 10, 1),
                    "skills": ["React", "TypeScript", "Next.js", "JavaScript"],
                }
            ],
            "eval": {
                "quality": 4.92,
                "delivery": 4.88,
                "collab": 4.90,
                "summary": "Nouran is a top-tier frontend architect. Exceptional performance tuning and component reusability.",
            },
            "metrics": {
                "velocity_ratio": 1.10,
                "code_review_acceptance": 0.98,
                "bug_rate_per_kloc": 0.05,
                "on_time_delivery_rate": 0.97,
            },
            "commitment": {
                "title": "Merchant Portal Design System Refactoring",
                "hours": 8.0,
            },
        },
        "EMP-037": {
            "name": "Seif El-Din Mostafa",
            "email": "seif.mostafa@nds.example",
            "title": "Frontend Developer",
            "years": 4.0,
            "hire_date": date(2023, 2, 1),
            "skills": [
                ("React", 85, V),
                ("JavaScript", 88, V),
                ("TypeScript", 82, V),
                ("Next.js", 80, V),
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.65,
                "collab": 4.70,
                "summary": "Seif is consistent and communicative. Handles responsive UI tasks cleanly.",
            },
            "metrics": {
                "velocity_ratio": 1.00,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.12,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-038": {
            "name": "Menna Allah Samir",
            "email": "menna.samir@nds.example",
            "title": "React / Next.js Specialist",
            "years": 5.0,
            "hire_date": date(2022, 6, 1),
            "skills": [
                ("Next.js", 92, V),
                ("React", 90, V),
                ("TypeScript", 88, V),
                ("JavaScript", 90, V),
                ("Tailwind CSS", 86, V),
            ],
            "credentials": [
                {
                    "name": "Next.js Enterprise Certification",
                    "issuer": "Vercel",
                    "skill": "Next.js",
                    "issued_at": date(2023, 8, 12),
                }
            ],
            "eval": {
                "quality": 4.80,
                "delivery": 4.85,
                "collab": 4.80,
                "summary": "Menna has deep mastery of App Router, server actions, and SSR data fetching.",
            },
            "metrics": {
                "velocity_ratio": 1.07,
                "code_review_acceptance": 0.96,
                "bug_rate_per_kloc": 0.08,
                "on_time_delivery_rate": 0.95,
            },
        },
        "EMP-039": {
            "name": "Hazem Badawy",
            "email": "hazem.badawy@nds.example",
            "title": "UI/Frontend Developer",
            "years": 3.5,
            "hire_date": date(2023, 7, 1),
            "skills": [
                ("React", 82, V),
                ("JavaScript", 85, V),
                ("TypeScript", 78, V),
                ("Next.js", 75, V),
            ],
            "eval": {
                "quality": 4.55,
                "delivery": 4.60,
                "collab": 4.70,
                "summary": "Hazem implements clean UI layouts with good attention to mobile responsiveness.",
            },
            "metrics": {
                "velocity_ratio": 0.98,
                "code_review_acceptance": 0.92,
                "bug_rate_per_kloc": 0.14,
                "on_time_delivery_rate": 0.90,
            },
        },
        "EMP-040": {
            "name": "Salma Ezzat",
            "email": "salma.ezzat@nds.example",
            "title": "Frontend Engineer",
            "years": 4.5,
            "hire_date": date(2022, 10, 15),
            "skills": [
                ("React", 86, V),
                ("TypeScript", 85, V),
                ("JavaScript", 88, V),
                ("Next.js", 82, V),
            ],
            "eval": {
                "quality": 4.70,
                "delivery": 4.70,
                "collab": 4.80,
                "summary": "Salma is a very collaborative engineer who excels in accessibility and test coverage.",
            },
            "metrics": {
                "velocity_ratio": 1.02,
                "code_review_acceptance": 0.95,
                "bug_rate_per_kloc": 0.09,
                "on_time_delivery_rate": 0.93,
            },
        },

        # =========================================================================
        # 6. QUALITY ASSURANCE & TEST AUTOMATION (NDS CAIRO)
        # =========================================================================
        "EMP-041": {
            "name": "Tariq Mansour",
            "email": "tariq.mansour@nds.example",
            "title": "Lead QA Automation Engineer",
            "years": 8.0,
            "hire_date": date(2021, 5, 1),
            "skills": [
                ("Playwright", 95, V),
                ("Test Automation", 96, V),
                ("Python", 90, V),
                ("CI/CD", 88, V),
                ("API Security", 82, V),
            ],
            "credentials": [
                {
                    "name": "ISTQB Certified Tester - Advanced Level (Test Automation Engineer)",
                    "issuer": "ISTQB",
                    "skill": "Test Automation",
                    "issued_at": date(2021, 9, 20),
                }
            ],
            "experiences": [
                {
                    "org": "Vodafone Egypt - Digital QA Automation CoE",
                    "role": "QA Automation Lead",
                    "desc": "Built cross-browser Playwright test automation framework integrated into GitLab CI pipelines executing 1,200 daily tests.",
                    "start_date": date(2018, 5, 1),
                    "end_date": date(2021, 4, 30),
                    "skills": ["Playwright", "Test Automation", "Python", "CI/CD"],
                }
            ],
            "eval": {
                "quality": 4.95,
                "delivery": 4.90,
                "collab": 4.90,
                "summary": "Tariq is an industry benchmark for QA automation. Leads testing frameworks with rigor and elegance.",
            },
            "metrics": {
                "velocity_ratio": 1.08,
                "code_review_acceptance": 0.99,
                "bug_rate_per_kloc": 0.03,
                "on_time_delivery_rate": 0.97,
            },
            "commitment": {
                "title": "QA Framework CI/CD Matrix Standardization",
                "hours": 16.0,
            },
        },
        "EMP-042": {
            "name": "Maya Roushdy",
            "email": "maya.roushdy@nds.example",
            "title": "Senior Test Automation Engineer",
            "years": 6.0,
            "hire_date": date(2022, 1, 15),
            "skills": [
                ("Playwright", 92, V),
                ("Test Automation", 94, V),
                ("Python", 88, V),
                ("CI/CD", 84, V),
                ("Cypress", 80, V),
                ("Git & GitHub", 88, V),
            ],
            "credentials": [
                {
                    "name": "ISTQB Certified Tester - Foundation Level",
                    "issuer": "ISTQB",
                    "skill": "Test Automation",
                    "issued_at": date(2022, 4, 18),
                },
                {
                    "name": "Playwright Certified Automation Specialist",
                    "issuer": "Test Automation University",
                    "skill": "Playwright",
                    "issued_at": date(2023, 6, 10),
                },
            ],
            "experiences": [
                {
                    "org": "Breadfast - E-Commerce End-to-End Testing",
                    "role": "Senior QA Engineer",
                    "desc": "Architected Python Playwright regression suite covering mobile web checkout, reducing manual release verification from 8 hours to 15 minutes.",
                    "start_date": date(2021, 2, 1),
                    "end_date": date(2023, 12, 15),
                    "skills": ["Playwright", "Python", "Test Automation", "CI/CD"],
                }
            ],
            "eval": {
                "quality": 4.90,
                "delivery": 4.92,
                "collab": 4.95,
                "summary": "Maya is our leading candidate for Playwright and Python test automation. Incredibly reliable and dedicated.",
            },
            "metrics": {
                "velocity_ratio": 1.12,
                "code_review_acceptance": 0.98,
                "bug_rate_per_kloc": 0.04,
                "on_time_delivery_rate": 0.98,
            },
        },
        "EMP-043": {
            "name": "Mostafa Helmy",
            "email": "mostafa.helmy@nds.example",
            "title": "QA Automation Engineer",
            "years": 4.5,
            "hire_date": date(2022, 11, 1),
            "skills": [
                ("Test Automation", 86, V),
                ("Playwright", 84, V),
                ("Python", 80, V),
                ("API Security", 75, V),
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.70,
                "collab": 4.75,
                "summary": "Mostafa writes solid test fixtures and API automation scenarios.",
            },
            "metrics": {
                "velocity_ratio": 1.02,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.10,
                "on_time_delivery_rate": 0.93,
            },
        },
        "EMP-044": {
            "name": "Habiba Nabil",
            "email": "habiba.nabil@nds.example",
            "title": "Software Test Engineer",
            "years": 3.5,
            "hire_date": date(2023, 4, 1),
            "skills": [
                ("Test Automation", 82, V),
                ("Playwright", 80, V),
                ("Python", 78, V),
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.60,
                "collab": 4.70,
                "summary": "Habiba is detail-oriented with high diligence in regression test passes.",
            },
            "metrics": {
                "velocity_ratio": 0.98,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.12,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-045": {
            "name": "Yasser Abdel-Rahman",
            "email": "yasser.abdelrahman@nds.example",
            "title": "Performance & QA Engineer",
            "years": 5.0,
            "hire_date": date(2022, 7, 1),
            "skills": [
                ("Test Automation", 88, V),
                ("Playwright", 85, V),
                ("Python", 82, V),
                ("CI/CD", 82, V),
            ],
            "eval": {
                "quality": 4.75,
                "delivery": 4.75,
                "collab": 4.80,
                "summary": "Yasser specializes in load testing and automated CI verification gates.",
            },
            "metrics": {
                "velocity_ratio": 1.04,
                "code_review_acceptance": 0.95,
                "bug_rate_per_kloc": 0.08,
                "on_time_delivery_rate": 0.94,
            },
        },

        # =========================================================================
        # 7. MOBILE ENGINEERING (NDS CAIRO)
        # =========================================================================
        "EMP-046": {
            "name": "Sherine Shawky",
            "email": "sherine.shawky@nds.example",
            "title": "Lead Mobile Developer",
            "years": 7.5,
            "hire_date": date(2021, 6, 1),
            "skills": [
                ("Flutter", 96, V),
                ("JavaScript", 90, V),
                ("TypeScript", 88, V),
                ("API Security", 82, V),
                ("Git & GitHub", 90, V),
            ],
            "credentials": [
                {
                    "name": "Google Certified Associate Android Developer",
                    "issuer": "Google Developers",
                    "skill": "Flutter",
                    "issued_at": date(2022, 5, 14),
                },
                {
                    "name": "Meta Certified Mobile Developer",
                    "issuer": "Meta",
                    "skill": "Flutter",
                    "issued_at": date(2023, 1, 20),
                },
            ],
            "experiences": [
                {
                    "org": "Lucky App - Consumer Financial Super-App",
                    "role": "Principal Mobile Architect",
                    "desc": "Engineered multi-tenant Flutter mobile app with 4M+ downloads, offline SQLite synchronization, biometric security, and native platform channels.",
                    "start_date": date(2020, 2, 1),
                    "end_date": date(2023, 8, 31),
                    "skills": ["Flutter", "API Security", "TypeScript"],
                }
            ],
            "eval": {
                "quality": 4.95,
                "delivery": 4.90,
                "collab": 4.92,
                "summary": "Sherine is our definitive Flutter authority. Exceptional cross-platform expertise, architecture, and responsiveness.",
            },
            "metrics": {
                "velocity_ratio": 1.14,
                "code_review_acceptance": 0.99,
                "bug_rate_per_kloc": 0.04,
                "on_time_delivery_rate": 0.98,
            },
        },
        "EMP-047": {
            "name": "Fady George",
            "email": "fady.george@nds.example",
            "title": "Senior Flutter Engineer",
            "years": 6.0,
            "hire_date": date(2022, 3, 1),
            "skills": [
                ("Flutter", 92, V),
                ("JavaScript", 85, V),
                ("TypeScript", 82, V),
                ("API Security", 80, V),
            ],
            "eval": {
                "quality": 4.80,
                "delivery": 4.80,
                "collab": 4.85,
                "summary": "Fady writes clean Flutter widget trees with Bloc state architecture.",
            },
            "metrics": {
                "velocity_ratio": 1.06,
                "code_review_acceptance": 0.96,
                "bug_rate_per_kloc": 0.08,
                "on_time_delivery_rate": 0.94,
            },
        },
        "EMP-048": {
            "name": "Mariam Wagdy",
            "email": "mariam.wagdy@nds.example",
            "title": "Mobile Application Developer",
            "years": 4.0,
            "hire_date": date(2023, 1, 15),
            "skills": [
                ("Flutter", 85, V),
                ("JavaScript", 82, V),
                ("TypeScript", 80, V),
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.65,
                "collab": 4.70,
                "summary": "Mariam is consistent, quick to debug UI glitches, and reliable.",
            },
            "metrics": {
                "velocity_ratio": 1.00,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.12,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-049": {
            "name": "Aly Hegazy",
            "email": "aly.hegazy@nds.example",
            "title": "Flutter / Cross-Platform Developer",
            "years": 4.5,
            "hire_date": date(2022, 9, 1),
            "skills": [
                ("Flutter", 88, V),
                ("JavaScript", 85, V),
                ("TypeScript", 84, V),
            ],
            "eval": {
                "quality": 4.70,
                "delivery": 4.75,
                "collab": 4.75,
                "summary": "Aly delivers robust Flutter state implementations with clean tests.",
            },
            "metrics": {
                "velocity_ratio": 1.03,
                "code_review_acceptance": 0.95,
                "bug_rate_per_kloc": 0.09,
                "on_time_delivery_rate": 0.93,
            },
        },
        "EMP-050": {
            "name": "Nada Bahaa",
            "email": "nada.bahaa@nds.example",
            "title": "Mobile Engineer",
            "years": 3.5,
            "hire_date": date(2023, 6, 1),
            "skills": [
                ("Flutter", 82, V),
                ("JavaScript", 80, V),
                ("TypeScript", 78, V),
                ("API Security", 75, V),
            ],
            "eval": {
                "quality": 4.55,
                "delivery": 4.60,
                "collab": 4.70,
                "summary": "Nada has shown high motivation and quick turnaround on mobile feature stories.",
            },
            "metrics": {
                "velocity_ratio": 0.98,
                "code_review_acceptance": 0.92,
                "bug_rate_per_kloc": 0.13,
                "on_time_delivery_rate": 0.90,
            },
        },

        # =========================================================================
        # 8. ODOO ERP & BUSINESS SOLUTIONS (NDS CAIRO)
        # =========================================================================
        "EMP-051": {
            "name": "Mahmoud El-Gohary",
            "email": "mahmoud.elgohary@nds.example",
            "title": "Lead Odoo Technical Architect",
            "years": 8.5,
            "hire_date": date(2021, 1, 15),
            "skills": [
                ("Odoo Development", 96, V),
                ("ERP Implementation", 94, V),
                ("Python", 92, V),
                ("PostgreSQL", 90, V),
                ("Git & GitHub", 88, V),
            ],
            "credentials": [
                {
                    "name": "Odoo 17 Certified Technical Consultant",
                    "issuer": "Odoo S.A.",
                    "skill": "Odoo Development",
                    "issued_at": date(2023, 11, 20),
                },
                {
                    "name": "Odoo 16 Certified Functional Consultant",
                    "issuer": "Odoo S.A.",
                    "skill": "ERP Implementation",
                    "issued_at": date(2022, 12, 10),
                },
            ],
            "experiences": [
                {
                    "org": "Ezz Steel - Odoo Enterprise ERP Customization",
                    "role": "Principal Odoo Technical Architect",
                    "desc": "Designed enterprise Odoo 17 ERP modules for supply chain, multi-currency invoicing, sales automation pipelines, and custom manufacturing execution.",
                    "start_date": date(2020, 3, 1),
                    "end_date": date(2023, 11, 30),
                    "skills": ["Odoo Development", "ERP Implementation", "Python", "PostgreSQL"],
                }
            ],
            "eval": {
                "quality": 4.96,
                "delivery": 4.92,
                "collab": 4.90,
                "summary": "Mahmoud is the foremost Odoo technical expert in the company. Deep mastery of Odoo ORM, views, security models, and enterprise business processes.",
            },
            "metrics": {
                "velocity_ratio": 1.15,
                "code_review_acceptance": 0.99,
                "bug_rate_per_kloc": 0.04,
                "on_time_delivery_rate": 0.98,
            },
        },
        "EMP-052": {
            "name": "Ahmed El-Banna",
            "email": "ahmed.elbanna@nds.example",
            "title": "Senior Odoo Developer",
            "years": 6.5,
            "hire_date": date(2022, 2, 1),
            "skills": [
                ("Odoo Development", 90, V),
                ("ERP Implementation", 88, V),
                ("Python", 88, V),
                ("PostgreSQL", 85, V),
            ],
            "credentials": [
                {
                    "name": "Odoo 16 Certified Developer",
                    "issuer": "Odoo S.A.",
                    "skill": "Odoo Development",
                    "issued_at": date(2023, 3, 15),
                }
            ],
            "eval": {
                "quality": 4.80,
                "delivery": 4.85,
                "collab": 4.80,
                "summary": "Ahmed is a very strong Odoo developer with solid mastery of sales workflows and accounting moves.",
            },
            "metrics": {
                "velocity_ratio": 1.08,
                "code_review_acceptance": 0.96,
                "bug_rate_per_kloc": 0.07,
                "on_time_delivery_rate": 0.95,
            },
        },
        "EMP-053": {
            "name": "Sarah Abdel-Fattah",
            "email": "sarah.abdelfattah@nds.example",
            "title": "Odoo Techno-Functional Consultant",
            "years": 5.0,
            "hire_date": date(2022, 7, 15),
            "skills": [
                ("ERP Implementation", 92, V),
                ("Odoo Development", 85, V),
                ("Python", 82, V),
                ("PostgreSQL", 80, V),
            ],
            "eval": {
                "quality": 4.75,
                "delivery": 4.75,
                "collab": 4.90,
                "summary": "Sarah bridges technical customization with business requirements seamlessly.",
            },
            "metrics": {
                "velocity_ratio": 1.04,
                "code_review_acceptance": 0.95,
                "bug_rate_per_kloc": 0.08,
                "on_time_delivery_rate": 0.94,
            },
        },
        "EMP-054": {
            "name": "Omar El-Kassas",
            "email": "omar.elkassas@nds.example",
            "title": "Odoo Backend Developer",
            "years": 4.5,
            "hire_date": date(2023, 1, 10),
            "skills": [
                ("Python", 88, V),
                ("Odoo Development", 86, V),
                ("PostgreSQL", 85, V),
                ("ERP Implementation", 80, V),
            ],
            "eval": {
                "quality": 4.70,
                "delivery": 4.70,
                "collab": 4.75,
                "summary": "Omar specializes in complex SQL performance tuning and Odoo compute fields optimization.",
            },
            "metrics": {
                "velocity_ratio": 1.02,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.10,
                "on_time_delivery_rate": 0.92,
            },
        },
        "EMP-055": {
            "name": "Rana Metwally",
            "email": "rana.metwally@nds.example",
            "title": "Odoo ERP Implementation Specialist",
            "years": 4.0,
            "hire_date": date(2023, 5, 1),
            "skills": [
                ("ERP Implementation", 88, V),
                ("Odoo Development", 84, V),
                ("Python", 80, V),
                ("PostgreSQL", 80, V),
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.70,
                "collab": 4.80,
                "summary": "Rana is proactive in user training, data migration, and UAT execution.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.11,
                "on_time_delivery_rate": 0.92,
            },
        },

        # =========================================================================
        # 9. DATA ENGINEERING & APPLIED AI (NDS CAIRO)
        # =========================================================================
        "EMP-021": {
            "name": "Nada Mansour",
            "email": "nada.mansour@nds.example",
            "title": "Senior Data Engineer",
            "years": 6.5,
            "hire_date": date(2021, 9, 1),
            "skills": [
                ("Data Engineering", 88, V),
                ("Python", 85, V),
                ("PostgreSQL", 82, V),
                ("Pandas & NumPy", 88, V),
                ("Kafka & Event-Driven", 80, V),
            ],
            "credentials": [
                {
                    "name": "Databricks Certified Data Engineer Associate",
                    "issuer": "Databricks",
                    "skill": "Data Engineering",
                    "issued_at": date(2022, 11, 15),
                }
            ],
            "experiences": [
                {
                    "org": "Swvl Mobility - Demand Forecasting Pipeline",
                    "role": "Senior Data Engineer",
                    "desc": "Built Airflow DAG pipelines aggregating 150M weekly trip records into BigQuery tables with anomaly detection.",
                    "start_date": date(2020, 3, 1),
                    "end_date": date(2022, 8, 30),
                    "skills": ["Data Engineering", "Python", "PostgreSQL"],
                }
            ],
            "eval": {
                "quality": 4.85,
                "delivery": 4.80,
                "collab": 4.85,
                "summary": "Nada delivers robust, idempotent data pipelines with rigorous schema validation.",
            },
            "metrics": {
                "velocity_ratio": 1.08,
                "code_review_acceptance": 0.97,
                "bug_rate_per_kloc": 0.07,
                "on_time_delivery_rate": 0.95,
            },
        },
        "EMP-022": {
            "name": "Walid Zaki",
            "email": "walid.zaki@nds.example",
            "title": "Data Engineer",
            "years": 3.8,
            "hire_date": date(2023, 2, 1),
            "skills": [
                ("Python", 80, V),
                ("PostgreSQL", 76, V),
                ("Data Engineering", 82, V),
                ("Pandas & NumPy", 80, V),
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.65,
                "collab": 4.70,
                "summary": "Walid is dependable in pipeline monitoring and ETL backfills.",
            },
            "metrics": {
                "velocity_ratio": 1.00,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.12,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-023": {
            "name": "Farah Adly",
            "email": "farah.adly@nds.example",
            "title": "Lead ML & LLM Engineer",
            "years": 6.0,
            "hire_date": date(2022, 5, 1),
            "skills": [
                ("LLM Engineering", 92, V),
                ("Machine Learning", 90, V),
                ("Python", 92, V),
                ("FastAPI", 80, V),
            ],
            "credentials": [
                {
                    "name": "TensorFlow Developer Certificate",
                    "issuer": "Google",
                    "skill": "Machine Learning",
                    "issued_at": date(2022, 8, 20),
                }
            ],
            "experiences": [
                {
                    "org": "Affectiva / Smart Eye - Emotion AI Platform",
                    "role": "Computer Vision & ML Specialist",
                    "desc": "Trained deep learning models and deployed edge inference microservices with ONNX runtime.",
                    "start_date": date(2020, 6, 1),
                    "end_date": date(2022, 4, 15),
                    "skills": ["Machine Learning", "Python"],
                }
            ],
            "eval": {
                "quality": 4.90,
                "delivery": 4.85,
                "collab": 4.88,
                "summary": "Farah brings cutting-edge GenAI and retrieval-augmented generation (RAG) architecture into production.",
            },
            "metrics": {
                "velocity_ratio": 1.09,
                "code_review_acceptance": 0.97,
                "bug_rate_per_kloc": 0.06,
                "on_time_delivery_rate": 0.96,
            },
        },
        "EMP-024": {
            "name": "Bassem Nour",
            "email": "bassem.nour@nds.example",
            "title": "Machine Learning Engineer",
            "years": 4.5,
            "hire_date": date(2022, 10, 1),
            "skills": [
                ("Machine Learning", 84, V),
                ("Python", 86, V),
                ("LLM Engineering", 80, V),
                ("FastAPI", 75, SD),
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.70,
                "collab": 4.75,
                "summary": "Bassem develops clean evaluation pipelines and model serving endpoints.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.10,
                "on_time_delivery_rate": 0.92,
            },
        },
        "EMP-025": {
            "name": "Ehab Selim",
            "email": "ehab.selim@nds.example",
            "title": "Senior Data Analyst",
            "years": 5.2,
            "hire_date": date(2022, 6, 1),
            "skills": [
                ("PostgreSQL", 88, V),
                ("Python", 80, V),
                ("Pandas & NumPy", 85, V),
                ("Data Engineering", 72, SD),
            ],
            "eval": {
                "quality": 4.70,
                "delivery": 4.75,
                "collab": 4.85,
                "summary": "Ehab provides actionable business intelligence and automated dashboard reporting.",
            },
            "metrics": {
                "velocity_ratio": 1.03,
                "code_review_acceptance": 0.95,
                "bug_rate_per_kloc": 0.08,
                "on_time_delivery_rate": 0.93,
            },
        },

        # =========================================================================
        # 10. GENERAL ENGINEERING & REST OF TEAMS (EMP-014 to EMP-030)
        # =========================================================================
        "EMP-014": {
            "name": "Mohamed Saber",
            "email": "mohamed.saber@nds.example",
            "title": "Java Backend Engineer",
            "years": 5.5,
            "hire_date": date(2022, 4, 1),
            "skills": [
                ("Java", 88, V),
                ("Spring Boot", 86, V),
                ("PostgreSQL", 75, V),
                ("Docker", 65, SD),
            ],
            "credentials": [
                {
                    "name": "Oracle Certified Professional: Java SE 11 Developer",
                    "issuer": "Oracle",
                    "skill": "Java",
                    "issued_at": date(2021, 10, 10),
                }
            ],
            "eval": {
                "quality": 4.70,
                "delivery": 4.75,
                "collab": 4.70,
                "summary": "Mohamed handles enterprise legacy integrations and high-volume Spring Boot services cleanly.",
            },
            "metrics": {
                "velocity_ratio": 1.02,
                "code_review_acceptance": 0.95,
                "bug_rate_per_kloc": 0.09,
                "on_time_delivery_rate": 0.93,
            },
        },
        "EMP-015": {
            "name": "Aya Sherif",
            "email": "aya.sherif@nds.example",
            "title": "Backend Engineer",
            "years": 3.5,
            "hire_date": date(2023, 3, 1),
            "skills": [
                ("Java", 76, V),
                ("Spring Boot", 74, V),
                ("Python", 68, SD),
                ("PostgreSQL", 65, SD),
            ],
            "eval": {
                "quality": 4.55,
                "delivery": 4.60,
                "collab": 4.75,
                "summary": "Aya learns quickly and writes structured, testable Spring services.",
            },
            "metrics": {
                "velocity_ratio": 0.98,
                "code_review_acceptance": 0.92,
                "bug_rate_per_kloc": 0.13,
                "on_time_delivery_rate": 0.90,
            },
        },
        "EMP-016": {
            "name": "Ibrahim Lotfy",
            "email": "ibrahim.lotfy@nds.example",
            "title": "Junior Backend Engineer",
            "years": 2.2,
            "hire_date": date(2024, 1, 15),
            "skills": [
                ("Python", 74, V),
                ("FastAPI", 70, V),
                ("PostgreSQL", 62, SD),
            ],
            "eval": {
                "quality": 4.45,
                "delivery": 4.50,
                "collab": 4.70,
                "summary": "Ibrahim is enthusiastic and shows solid progress in unit testing.",
            },
            "metrics": {
                "velocity_ratio": 0.94,
                "code_review_acceptance": 0.90,
                "bug_rate_per_kloc": 0.16,
                "on_time_delivery_rate": 0.88,
            },
        },
        "EMP-017": {
            "name": "Dina Hamdy",
            "email": "dina.hamdy@nds.example",
            "title": "Frontend Engineer",
            "years": 3.8,
            "hire_date": date(2023, 2, 15),
            "skills": [
                ("JavaScript", 82, V),
                ("React", 78, V),
                ("TypeScript", 74, V),
                ("Tailwind CSS", 80, V),
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.65,
                "collab": 4.70,
                "summary": "Dina implements clean UI layouts with good attention to design systems.",
            },
            "metrics": {
                "velocity_ratio": 0.99,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.12,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-018": {
            "name": "Sherif Anwar",
            "email": "sherif.anwar@nds.example",
            "title": "Frontend Lead",
            "years": 8.5,
            "hire_date": date(2020, 8, 1),
            "skills": [
                ("JavaScript", 92, V),
                ("React", 88, V),
                ("TypeScript", 85, V),
                ("Next.js", 82, V),
                ("Agile & Scrum", 88, V),
            ],
            "eval": {
                "quality": 4.80,
                "delivery": 4.80,
                "collab": 4.90,
                "summary": "Sherif provides excellent mentorship for junior developers and leads frontend sprint ceremonies.",
            },
            "metrics": {
                "velocity_ratio": 1.04,
                "code_review_acceptance": 0.97,
                "bug_rate_per_kloc": 0.07,
                "on_time_delivery_rate": 0.94,
            },
        },
        "EMP-019": {
            "name": "Yasmin Fouad",
            "email": "yasmin.fouad@nds.example",
            "title": "Mobile Engineer",
            "years": 4.2,
            "hire_date": date(2022, 10, 1),
            "skills": [
                ("Flutter", 84, V),
                ("JavaScript", 76, SD),
                ("Git & GitHub", 82, V),
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.68,
                "collab": 4.75,
                "summary": "Yasmin is dependable on cross-platform mobile bug fixes and release distribution.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.11,
                "on_time_delivery_rate": 0.92,
            },
        },
        "EMP-020": {
            "name": "Hossam Ragab",
            "email": "hossam.ragab@nds.example",
            "title": "Junior Frontend Engineer",
            "years": 2.0,
            "hire_date": date(2024, 2, 1),
            "skills": [
                ("JavaScript", 78, V),
                ("React", 72, V),
                ("TypeScript", 66, SD),
            ],
            "eval": {
                "quality": 4.45,
                "delivery": 4.50,
                "collab": 4.75,
                "summary": "Hossam is eager to learn and responds quickly to review comments.",
            },
            "metrics": {
                "velocity_ratio": 0.93,
                "code_review_acceptance": 0.89,
                "bug_rate_per_kloc": 0.18,
                "on_time_delivery_rate": 0.87,
            },
        },
        "EMP-026": {
            "name": "Amr Fekry",
            "email": "amr.fekry@nds.example",
            "title": "DevOps Engineer",
            "years": 4.8,
            "hire_date": date(2022, 8, 1),
            "skills": [
                ("Docker", 82, V),
                ("CI/CD", 82, V),
                ("Linux & Bash", 84, V),
                ("Kubernetes", 75, SD),
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.70,
                "collab": 4.70,
                "summary": "Amr manages container build caches and runner infrastructure smoothly.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.10,
                "on_time_delivery_rate": 0.92,
            },
        },
        "EMP-027": {
            "name": "Reem Tawfik",
            "email": "reem.tawfik@nds.example",
            "title": "Cloud Infrastructure Engineer",
            "years": 3.8,
            "hire_date": date(2023, 4, 1),
            "skills": [
                ("AWS", 82, V),
                ("Terraform", 78, V),
                ("Docker", 75, SD),
                ("CI/CD", 76, SD),
            ],
            "eval": {
                "quality": 4.60,
                "delivery": 4.65,
                "collab": 4.75,
                "summary": "Reem writes clean Terraform definitions and manages S3 lifecycle policies efficiently.",
            },
            "metrics": {
                "velocity_ratio": 0.99,
                "code_review_acceptance": 0.93,
                "bug_rate_per_kloc": 0.11,
                "on_time_delivery_rate": 0.91,
            },
        },
        "EMP-028": {
            "name": "Khaled Azmy",
            "email": "khaled.azmy@nds.example",
            "title": "QA Automation Engineer",
            "years": 4.2,
            "hire_date": date(2022, 11, 1),
            "skills": [
                ("Test Automation", 84, V),
                ("Playwright", 80, V),
                ("Python", 78, V),
                ("Cypress", 76, SD),
            ],
            "eval": {
                "quality": 4.65,
                "delivery": 4.70,
                "collab": 4.75,
                "summary": "Khaled writes good regression scripts and maintains high test coverage.",
            },
            "metrics": {
                "velocity_ratio": 1.01,
                "code_review_acceptance": 0.94,
                "bug_rate_per_kloc": 0.10,
                "on_time_delivery_rate": 0.92,
            },
        },
        "EMP-029": {
            "name": "Noha Gaber",
            "email": "noha.gaber@nds.example",
            "title": "QA Engineer",
            "years": 3.6,
            "hire_date": date(2023, 5, 1),
            "skills": [
                ("Test Automation", 78, V),
                ("Playwright", 75, SD),
                ("Python", 72, SD),
            ],
            "eval": {
                "quality": 4.55,
                "delivery": 4.60,
                "collab": 4.80,
                "summary": "Noha is detail-oriented in manual verification and test matrix documentation.",
            },
            "metrics": {
                "velocity_ratio": 0.98,
                "code_review_acceptance": 0.92,
                "bug_rate_per_kloc": 0.13,
                "on_time_delivery_rate": 0.90,
            },
        },
        "EMP-030": {
            "name": "Ziad Hamed",
            "email": "ziad.hamed@nds.example",
            "title": "QA Test Engineer",
            "years": 2.5,
            "hire_date": date(2023, 10, 1),
            "skills": [
                ("Test Automation", 74, V),
                ("Python", 70, SD),
                ("Git & GitHub", 75, V),
            ],
            "eval": {
                "quality": 4.50,
                "delivery": 4.55,
                "collab": 4.70,
                "summary": "Ziad is diligent and quick to report functional defects with clear reproduction steps.",
            },
            "metrics": {
                "velocity_ratio": 0.95,
                "code_review_acceptance": 0.90,
                "bug_rate_per_kloc": 0.15,
                "on_time_delivery_rate": 0.89,
            },
        },
    }

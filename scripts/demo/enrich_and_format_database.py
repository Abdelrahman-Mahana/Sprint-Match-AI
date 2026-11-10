"""Enrich and format the database with realistic, diverse, and well-structured samples.

Every employee gets:
- Proper, realistic names and standardized titles
- Distinct experience years and hire dates
- Unique skill combinations and varied proficiencies
- Industry-standard verified credentials & certifications
- Rich past project experiences with linked skills
- Quarterly performance evaluations and metrics (velocity, quality, delivery)
- Daily attendance records (last 10 working days)
- Realistic leaves and ongoing commitments
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import create_engine, text
from app.core.config import get_settings

CAIRO_TZ = ZoneInfo("Africa/Cairo")
REF_DATE = date(2026, 11, 3)
NOW_TS = datetime(2026, 11, 3, 10, 0, 0, tzinfo=CAIRO_TZ)

NDS_COMPANY_ID = "d252a56d-8e1d-5c00-b12a-4060bbcb6d38"
DLT_COMPANY_ID = "04077ca6-43bc-5cf0-b749-0fa392ef94e1"


# 1. Extra realistic skills to add to NDS catalog
ADDITIONAL_SKILLS = [
    {
        "name": "Git & GitHub",
        "category": "devops",
        "description": "Version control, branching strategies, code reviews, and GitHub Actions workflows.",
        "aliases": ["git", "github"],
    },
    {
        "name": "GraphQL",
        "category": "backend_framework",
        "description": "GraphQL schema definition, query resolvers, mutations, and Apollo/Strawberry federation.",
        "aliases": ["graphql", "gql"],
    },
    {
        "name": "Node.js",
        "category": "language",
        "description": "Server-side JavaScript runtime, event-driven async I/O, Express and Fastify frameworks.",
        "aliases": ["nodejs", "node"],
    },
    {
        "name": "Tailwind CSS",
        "category": "frontend_framework",
        "description": "Utility-first CSS framework for rapid responsive and accessible UI development.",
        "aliases": ["tailwind", "tailwindcss"],
    },
    {
        "name": "Linux & Bash",
        "category": "devops",
        "description": "Linux system administration, shell scripting, process management, and OS optimization.",
        "aliases": ["linux", "bash", "shell"],
    },
    {
        "name": "Cypress",
        "category": "qa",
        "description": "Modern end-to-end web testing framework with automated snapshots and live debugging.",
        "aliases": ["cypress"],
    },
    {
        "name": "Pandas & NumPy",
        "category": "data",
        "description": "Data analysis, vectorization, dataframe manipulation, and time-series aggregation.",
        "aliases": ["pandas", "numpy"],
    },
    {
        "name": "Microservices Architecture",
        "category": "engineering",
        "description": "Distributed systems, domain-driven design, asynchronous messaging, and service boundaries.",
        "aliases": ["microservices", "distributed systems"],
    },
    {
        "name": "Kafka & Event-Driven",
        "category": "database",
        "description": "Apache Kafka event streaming, consumer groups, partition management, and schemas.",
        "aliases": ["kafka", "event-driven"],
    },
    {
        "name": "Agile & Scrum",
        "category": "engineering",
        "description": "Scrum sprint ceremonies, backlog refinement, story pointing, and retrospective leadership.",
        "aliases": ["agile", "scrum"],
    },
]


def run_enrichment():
    engine = create_engine(get_settings().database_url.get_secret_value())
    
    with engine.begin() as conn:
        print("1. Inserting additional enterprise skills...")
        for s in ADDITIONAL_SKILLS:
            exists = conn.execute(
                text("SELECT id FROM skills WHERE company_id = :cid AND name = :name"),
                {"cid": NDS_COMPANY_ID, "name": s["name"]}
            ).scalar()
            if not exists:
                conn.execute(
                    text("""
                        INSERT INTO skills (id, company_id, name, category, description, aliases)
                        VALUES (:id, :cid, :name, :cat, :desc, :aliases)
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "cid": NDS_COMPANY_ID,
                        "name": s["name"],
                        "cat": s["category"],
                        "desc": s["description"],
                        "aliases": s["aliases"],
                    }
                )
                print(f"   + Added skill: {s['name']}")

        # Cache all skills map: name -> id
        skills_rows = conn.execute(
            text("SELECT id, name, company_id FROM skills")
        ).fetchall()
        skill_map = {}
        for r in skills_rows:
            skill_map[(r[1], str(r[2]))] = str(r[0])
            skill_map[r[1]] = str(r[0])  # fallback

        print("\n2. Fetching existing employees...")
        emp_rows = conn.execute(
            text("SELECT id, employee_code, full_name, company_id, department_id, team_id FROM employees ORDER BY employee_code")
        ).fetchall()
        emp_by_code = {r[1]: {"id": str(r[0]), "name": r[2], "cid": str(r[3]), "dept_id": str(r[4]) if r[4] else None, "team_id": str(r[5]) if r[5] else None} for r in emp_rows}
        print(f"   Found {len(emp_by_code)} employees in database.")

        # Clean existing detail tables so we re-populate them fresh, clean, and distinct
        print("\n3. Cleaning detail tables to prevent duplicates...")
        conn.execute(text("DELETE FROM employee_experience_skills"))
        conn.execute(text("DELETE FROM employee_experiences"))
        conn.execute(text("DELETE FROM employee_credentials"))
        conn.execute(text("DELETE FROM performance_evaluations"))
        conn.execute(text("DELETE FROM performance_metrics"))
        conn.execute(text("DELETE FROM attendance_records"))
        conn.execute(text("DELETE FROM leave_records"))
        conn.execute(text("DELETE FROM employee_commitments"))
        conn.execute(text("DELETE FROM employee_skills"))
        print("   Detail tables cleaned successfully.")

        # Load Employee Detailed Blueprint
        from scripts.demo._employee_blueprints import get_employee_blueprints
        blueprints = get_employee_blueprints()

        print(f"\n4. Enriching {len(blueprints)} employee profiles with rich, distinct data...")
        
        credentials_to_insert = []
        experiences_to_insert = []
        exp_skills_to_insert = []
        evaluations_to_insert = []
        metrics_to_insert = []
        commitments_to_insert = []
        leaves_to_insert = []
        attendance_to_insert = []
        skills_to_insert = []

        # Dates for attendance: last 10 working days ending REF_DATE (Sunday-Thursday in Cairo)
        working_days = []
        cur_d = REF_DATE
        while len(working_days) < 10:
            if cur_d.weekday() in [6, 0, 1, 2, 3]:  # Sun to Thu
                working_days.append(cur_d)
            cur_d -= timedelta(days=1)
        working_days.reverse()

        for code, bp in blueprints.items():
            if code not in emp_by_code:
                continue
            h = sum(ord(c) for c in code)
            emp_meta = emp_by_code[code]
            emp_id = emp_meta["id"]
            cid = emp_meta["cid"]

            # Update base employee row: proper clean title, experience, email
            conn.execute(
                text("""
                    UPDATE employees
                    SET full_name = :name,
                        email = :email,
                        job_title = :title,
                        experience_years = :years,
                        hire_date = :hire_date
                    WHERE id = :id
                """),
                {
                    "id": emp_id,
                    "name": bp["name"],
                    "email": bp["email"],
                    "title": bp["title"],
                    "years": Decimal(str(bp["years"])),
                    "hire_date": bp["hire_date"],
                }
            )

            # Insert unique employee skills
            for sname, prof, v_status in bp["skills"]:
                sid = skill_map.get((sname, cid)) or skill_map.get(sname)
                if not sid:
                    continue
                skills_to_insert.append({
                    "id": str(uuid.uuid4()),
                    "employee_id": emp_id,
                    "skill_id": sid,
                    "proficiency_level": prof,
                    "verification_status": v_status,
                    "verified_at": NOW_TS if v_status == "VERIFIED" else None,
                    "last_assessed_at": NOW_TS - timedelta(days=20),
                })

            # Insert credentials / certifications
            for cred in bp.get("credentials", []):
                sid = skill_map.get((cred.get("skill", ""), cid)) or skill_map.get(cred.get("skill", ""))
                credentials_to_insert.append({
                    "id": str(uuid.uuid4()),
                    "employee_id": emp_id,
                    "name": cred["name"],
                    "issuer": cred["issuer"],
                    "credential_type": "CERTIFICATION",
                    "skill_id": sid,
                    "issued_at": cred["issued_at"],
                    "expires_at": cred.get("expires_at"),
                    "verification_status": "VERIFIED",
                })

            # Insert past experiences
            for exp in bp.get("experiences", []):
                exp_id = str(uuid.uuid4())
                experiences_to_insert.append({
                    "id": exp_id,
                    "employee_id": emp_id,
                    "organization_or_project": exp["org"],
                    "role": exp["role"],
                    "description": exp["desc"],
                    "relevant_start_date": exp["start_date"],
                    "relevant_end_date": exp.get("end_date"),
                })
                # Link skills to this experience
                for sname in exp.get("skills", []):
                    sid = skill_map.get((sname, cid)) or skill_map.get(sname)
                    if sid:
                        exp_skills_to_insert.append({
                            "experience_id": exp_id,
                            "skill_id": sid,
                        })

            # Insert quarterly performance evaluation
            eval_data = bp.get("eval", {})
            reviewer_code = bp.get("reviewer_code")
            if not reviewer_code or reviewer_code == code:
                if code == "EMP-002":
                    reviewer_code = "EMP-001"
                elif code.startswith("DLT"):
                    reviewer_code = "DLT-002" if code != "DLT-002" else "DLT-001"
                else:
                    reviewer_code = "EMP-002"
            reviewer_id = emp_by_code.get(reviewer_code, {}).get("id")
            evaluations_to_insert.append({
                "id": str(uuid.uuid4()),
                "company_id": cid,
                "employee_id": emp_id,
                "sprint_id": None,
                "reviewer_id": reviewer_id,
                "period_start": date(2026, 7, 1),
                "period_end": date(2026, 9, 30),
                "technical_quality": Decimal(str(eval_data.get("quality", 4.5))),
                "delivery": Decimal(str(eval_data.get("delivery", 4.6))),
                "collaboration": Decimal(str(eval_data.get("collab", 4.7))),
                "summary": eval_data.get("summary", f"{bp['name']} consistently demonstrates strong technical execution and team collaboration."),
                "created_at": NOW_TS - timedelta(days=35),
            })

            # Insert 5 key performance metrics
            metrics_dict = {
                "avg_quality": Decimal(str(eval_data.get("quality", 4.65))),
                "on_time_rate": Decimal(str(bp.get("metrics", {}).get("on_time_delivery_rate", 0.94))),
                "acceptance_rate": Decimal(str(bp.get("metrics", {}).get("code_review_acceptance", 0.97))),
                "rework_rate": Decimal(str(bp.get("metrics", {}).get("bug_rate_per_kloc", 0.05))),
                "tasks_completed": Decimal(str(15 + (h % 10))),
            }
            for m_name, m_val in metrics_dict.items():
                metrics_to_insert.append({
                    "id": str(uuid.uuid4()),
                    "company_id": cid,
                    "employee_id": emp_id,
                    "metric": m_name,
                    "period_start": date(2026, 7, 1),
                    "period_end": date(2026, 9, 30),
                    "value": m_val,
                    "sample_size": int(15 + (h % 10)),
                    "computed_at": NOW_TS - timedelta(days=15),
                })

            # Insert active commitment if defined
            if "commitment" in bp:
                comm = bp["commitment"]
                commitments_to_insert.append({
                    "id": str(uuid.uuid4()),
                    "employee_id": emp_id,
                    "title": comm["title"],
                    "start_at": NOW_TS - timedelta(days=2),
                    "end_at": NOW_TS + timedelta(days=5),
                    "hours": Decimal(str(comm["hours"])),
                })

            # Insert leave if defined
            if "leave" in bp:
                lv = bp["leave"]
                leaves_to_insert.append({
                    "id": str(uuid.uuid4()),
                    "employee_id": emp_id,
                    "leave_type": lv["type"],
                    "start_at": lv["start_at"],
                    "end_at": lv["end_at"],
                    "status": "APPROVED",
                })

            # Insert daily attendance for last 10 working days
            # slight jitter based on code hash
            h = sum(ord(c) for c in code)
            checkin_total_min = 8 * 60 + 45 + (h % 30)  # 08:45 to 09:14
            ci_hr = checkin_total_min // 60
            ci_min = checkin_total_min % 60

            checkout_total_min = 17 * 60 + 15 + ((h * 3) % 40)  # 17:15 to 17:54
            co_hr = checkout_total_min // 60
            co_min = checkout_total_min % 60

            for wd in working_days:
                # check if on leave on this day
                on_leave = False
                if "leave" in bp:
                    lv_start = bp["leave"]["start_at"].date()
                    lv_end = bp["leave"]["end_at"].date()
                    if lv_start <= wd <= lv_end:
                        on_leave = True

                if on_leave:
                    attendance_to_insert.append({
                        "id": str(uuid.uuid4()),
                        "employee_id": emp_id,
                        "work_date": wd,
                        "check_in": None,
                        "check_out": None,
                        "attendance_status": "ON_LEAVE",
                    })
                else:
                    ci = datetime.combine(wd, time(ci_hr, ci_min), tzinfo=CAIRO_TZ)
                    co = datetime.combine(wd, time(co_hr, co_min), tzinfo=CAIRO_TZ)
                    status = "REMOTE" if (h + wd.day) % 4 == 0 else "PRESENT"
                    attendance_to_insert.append({
                        "id": str(uuid.uuid4()),
                        "employee_id": emp_id,
                        "work_date": wd,
                        "check_in": ci,
                        "check_out": co,
                        "attendance_status": status,
                    })

        # Batch execute inserts
        print(f"5. Inserting {len(skills_to_insert)} employee skills...")
        for row in skills_to_insert:
            conn.execute(
                text("""
                    INSERT INTO employee_skills (id, employee_id, skill_id, proficiency_level, verification_status, verified_at, last_assessed_at)
                    VALUES (:id, :employee_id, :skill_id, :proficiency_level, :verification_status, :verified_at, :last_assessed_at)
                """), row
            )

        print(f"6. Inserting {len(credentials_to_insert)} verified credentials...")
        for row in credentials_to_insert:
            conn.execute(
                text("""
                    INSERT INTO employee_credentials (id, employee_id, name, issuer, credential_type, skill_id, issued_at, expires_at, verification_status)
                    VALUES (:id, :employee_id, :name, :issuer, :credential_type, :skill_id, :issued_at, :expires_at, :verification_status)
                """), row
            )

        print(f"7. Inserting {len(experiences_to_insert)} past career experiences...")
        for row in experiences_to_insert:
            conn.execute(
                text("""
                    INSERT INTO employee_experiences (id, employee_id, organization_or_project, role, description, relevant_start_date, relevant_end_date)
                    VALUES (:id, :employee_id, :organization_or_project, :role, :description, :relevant_start_date, :relevant_end_date)
                """), row
            )

        print(f"8. Linking {len(exp_skills_to_insert)} experience skills...")
        for row in exp_skills_to_insert:
            conn.execute(
                text("""
                    INSERT INTO employee_experience_skills (experience_id, skill_id)
                    VALUES (:experience_id, :skill_id)
                """), row
            )

        print(f"9. Inserting {len(evaluations_to_insert)} performance evaluations...")
        for row in evaluations_to_insert:
            conn.execute(
                text("""
                    INSERT INTO performance_evaluations (id, company_id, employee_id, sprint_id, reviewer_id, period_start, period_end, technical_quality, delivery, collaboration, summary, created_at)
                    VALUES (:id, :company_id, :employee_id, :sprint_id, :reviewer_id, :period_start, :period_end, :technical_quality, :delivery, :collaboration, :summary, :created_at)
                """), row
            )

        print(f"10. Inserting {len(metrics_to_insert)} performance metrics...")
        for row in metrics_to_insert:
            conn.execute(
                text("""
                    INSERT INTO performance_metrics (id, company_id, employee_id, metric, period_start, period_end, value, sample_size, computed_at)
                    VALUES (:id, :company_id, :employee_id, :metric, :period_start, :period_end, :value, :sample_size, :computed_at)
                """), row
            )

        print(f"11. Inserting {len(commitments_to_insert)} employee commitments...")
        for row in commitments_to_insert:
            conn.execute(
                text("""
                    INSERT INTO employee_commitments (id, employee_id, title, start_at, end_at, hours)
                    VALUES (:id, :employee_id, :title, :start_at, :end_at, :hours)
                """), row
            )

        print(f"12. Inserting {len(leaves_to_insert)} leave records...")
        for row in leaves_to_insert:
            conn.execute(
                text("""
                    INSERT INTO leave_records (id, employee_id, leave_type, start_at, end_at, status)
                    VALUES (:id, :employee_id, :leave_type, :start_at, :end_at, :status)
                """), row
            )

        print(f"13. Inserting {len(attendance_to_insert)} attendance records...")
        for row in attendance_to_insert:
            conn.execute(
                text("""
                    INSERT INTO attendance_records (id, employee_id, work_date, check_in, check_out, attendance_status)
                    VALUES (:id, :employee_id, :work_date, :check_in, :check_out, :attendance_status)
                """), row
            )

        print("\nALL DATABASE ENRICHMENT COMPLETED SUCCESSFULLY IN A SINGLE ATOMIC TRANSACTION!")


if __name__ == "__main__":
    run_enrichment()

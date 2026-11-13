"""Enrich the sprintmatch database with realistic diversity.

Features:
1. Availability status diversity:
   - AVAILABLE: ~28 developers
   - BUSY: ~12 developers (committed >= 80% weekly capacity)
   - PARTIALLY_AVAILABLE: ~10 developers (10h-24h commitments)
   - ON_LEAVE: ~3 developers (active approved leaves)
   - INACTIVE: 2 developers (EMP-025, EMP-051)
2. Attendance & Hours diversity:
   - High activity: 19-22 days, 140h-168h
   - Solid full-time: 15-18 days, 105h-135h
   - Moderate: 11-14 days, 75h-100h
   - Flexible: 7-10 days, 45h-70h
3. Completed past tasks with outcomes for EVERY developer:
   - Realistic titles & descriptions tailored to employee skills and domain.
   - TaskRequiredSkillModel linking to employee verified skills.
   - TaskAssignmentModel (COMPLETED, remaining=0).
   - TaskOutcomeModel (quality_score 78..96, accepted=True, rework=0..1, on_time=True/False).
   - TimesheetEntryModel for all completed tasks.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from app.core.config import get_settings

CAIRO_TZ = ZoneInfo("Africa/Cairo")
REF_DATE = date(2026, 11, 10)
NOW_TS = datetime(2026, 11, 10, 10, 0, 0, tzinfo=CAIRO_TZ)

# Domain tasks templates
DOMAIN_TASKS = {
    "Backend Engineering": [
        ("Implement Redis Caching for Payment Routing", "backend_development", ["Python", "FastAPI", "Redis"], "medium", 12.0),
        ("Refactor OAuth2 JWT Token Validation Flow", "backend_development", ["Python", "FastAPI", "API Security"], "high", 16.0),
        ("Build Asynchronous Webhook Dispatcher", "backend_development", ["Python", "PostgreSQL", "FastAPI"], "high", 20.0),
        ("Optimize SQL Indexes for Transaction Ledger", "database", ["PostgreSQL", "SQLAlchemy"], "medium", 10.0),
        ("Implement Idempotent Refund Processing Endpoint", "backend_development", ["Python", "FastAPI", "PostgreSQL"], "critical", 18.0),
        ("Spring Boot Microservice Event Listener", "backend_development", ["Java", "Spring Boot", "PostgreSQL"], "medium", 14.0),
    ],
    "Frontend & Mobile": [
        ("Build Responsive Transaction Analytics Dashboard", "frontend_development", ["React", "TypeScript", "Tailwind CSS"], "high", 16.0),
        ("Implement Multi-Step Merchant KYC Onboarding Wizard", "frontend_development", ["React", "TypeScript", "Next.js"], "medium", 14.0),
        ("Refactor App State Management with React Query", "frontend_development", ["React", "TypeScript"], "medium", 10.0),
        ("Build Biometric Fingerprint & FaceID Login Flow", "mobile_development", ["Flutter"], "high", 18.0),
        ("Implement Offline Transaction Caching in SQLite", "mobile_development", ["Flutter"], "medium", 12.0),
        ("Optimize Mobile Screen Rendering and Frame Rates", "mobile_development", ["Flutter"], "medium", 10.0),
    ],
    "DevOps & Cloud": [
        ("Automate Multi-AZ AWS EKS Cluster Provisioning", "devops", ["Terraform", "Kubernetes", "AWS"], "critical", 22.0),
        ("Implement Automated GitHub Actions CI/CD Pipeline", "devops", ["CI/CD", "Docker", "Git & GitHub"], "high", 16.0),
        ("Deploy Prometheus and Grafana Metric Dashboards", "devops", ["Kubernetes", "Linux & Bash"], "medium", 12.0),
        ("Configure Zero-Trust Network Policies and Istio Mesh", "devops", ["Kubernetes", "AWS", "Linux & Bash"], "high", 18.0),
        ("Automate Database Backup and Point-in-Time Recovery", "devops", ["Docker", "Linux & Bash", "PostgreSQL"], "medium", 10.0),
    ],
    "Quality Assurance": [
        ("Automate End-to-End Checkout Flow with Playwright", "qa_testing", ["Test Automation", "Playwright", "JavaScript"], "high", 16.0),
        ("Build Pytest Integration Suite for Payment APIs", "qa_testing", ["Test Automation", "Python", "FastAPI"], "medium", 14.0),
        ("Perform Automated OWASP API Security Vulnerability Scan", "qa_testing", ["API Security", "Test Automation"], "high", 12.0),
        ("Implement Cross-Browser Visual Regression Testing", "qa_testing", ["Test Automation", "Playwright"], "medium", 10.0),
        ("Build Load and Stress Test Suite using Locust", "qa_testing", ["Test Automation", "Python"], "medium", 12.0),
    ],
    "Data & AI": [
        ("Build Daily Financial Reconciliation ETL Pipeline", "data_engineering", ["Data Engineering", "Python", "PostgreSQL"], "high", 18.0),
        ("Fine-Tune LLM Intent Classification for Support Tickets", "machine_learning", ["LLM Engineering", "Python", "Machine Learning"], "critical", 24.0),
        ("Optimize Merchant Aggregation Pipeline with Pandas", "data_engineering", ["Data Engineering", "Pandas & NumPy", "Python"], "medium", 12.0),
        ("Implement Real-Time Fraud Anomaly Detection Model", "machine_learning", ["Machine Learning", "Python"], "high", 20.0),
    ],
    "Odoo Implementation": [
        ("Customize Odoo 17 Invoicing & Tax Engine for Egypt ETA", "backend_development", ["Odoo Development", "Python", "PostgreSQL"], "critical", 24.0),
        ("Build Automated XML-RPC Bank Reconciliation in Odoo", "backend_development", ["Odoo Development", "Python", "ERP Implementation"], "high", 18.0),
        ("Implement Multi-Company Warehouse Inventory Transfer in Odoo", "backend_development", ["Odoo Development", "ERP Implementation"], "medium", 14.0),
        ("Design Custom Sales Commission Calculation Rules in Odoo", "backend_development", ["Odoo Development", "Python"], "medium", 12.0),
        ("Migrate Legacy ERP Customers and Products into Odoo 17", "backend_development", ["Odoo Development", "PostgreSQL", "ERP Implementation"], "high", 16.0),
    ],
}


def run():
    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value())

    with engine.begin() as conn:
        print("1. Fetching company, employees, skills, and projects...")
        company_row = conn.execute(text("SELECT id FROM companies WHERE code = 'NDS'")).fetchone()
        if not company_row:
            print("Company NDS not found!")
            return
        company_id = company_row[0]

        emp_rows = conn.execute(
            text("""
                SELECT e.id, e.employee_code, e.full_name, e.job_title, e.employment_status, d.name as dept_name
                FROM employees e
                LEFT JOIN departments d ON d.id = e.department_id
                WHERE e.company_id = :cid
                ORDER BY e.employee_code
            """),
            {"cid": company_id},
        ).fetchall()

        skills_map = dict(
            conn.execute(
                text("SELECT name, id FROM skills WHERE company_id = :cid"),
                {"cid": company_id},
            ).fetchall()
        )

        projects_map = dict(
            conn.execute(
                text("SELECT code, id FROM projects WHERE company_id = :cid"),
                {"cid": company_id},
            ).fetchall()
        )
        default_proj_id = list(projects_map.values())[0]

        # Department to project code mapping
        dept_project = {
            "Backend Engineering": projects_map.get("PAY", default_proj_id),
            "Frontend & Mobile": projects_map.get("MOB", default_proj_id),
            "DevOps & Cloud": projects_map.get("CLD", default_proj_id),
            "Quality Assurance": projects_map.get("PAY", default_proj_id),
            "Data & AI": projects_map.get("ANA", default_proj_id),
            "Odoo Implementation": projects_map.get("HRP", default_proj_id),
        }

        # Check existing completed tasks count per employee
        existing_outcomes = dict(
            conn.execute(
                text("""
                    SELECT employee_id, count(*) 
                    FROM task_outcomes 
                    GROUP BY employee_id
                """)
            ).fetchall()
        )

        print(f"Loaded {len(emp_rows)} employees, {len(skills_map)} skills, {len(projects_map)} projects.")

        # 2. Insert Completed Past Tasks for all employees who have fewer than 3 tasks
        print("2. Generating completed past tasks with outcomes for employees...")
        tasks_created = 0
        rnd = random.Random(42)

        for emp_id, emp_code, full_name, job_title, emp_status, dept_name in emp_rows:
            current_tasks = existing_outcomes.get(emp_id, 0)
            target_tasks = rnd.randint(4, 7)
            needed = max(0, target_tasks - current_tasks)
            if needed <= 0:
                continue

            dept_key = dept_name if dept_name in DOMAIN_TASKS else "Backend Engineering"
            templates = DOMAIN_TASKS[dept_key]
            proj_id = dept_project.get(dept_key, default_proj_id)

            # Pick a reviewer from same company
            reviewer_candidates = [e[0] for e in emp_rows if e[0] != emp_id]
            reviewer_id = rnd.choice(reviewer_candidates) if reviewer_candidates else None

            for i in range(needed):
                t_tmpl = templates[i % len(templates)]
                title, cat, req_skills, comp, est_hrs = t_tmpl
                task_code = f"HIST-{emp_code}-{i+1:02d}"

                # Past date (completed 15 to 60 days ago)
                days_ago = rnd.randint(15, 60)
                finish_date = REF_DATE - timedelta(days=days_ago)
                start_date = finish_date - timedelta(days=rnd.randint(2, 5))
                planned_start = datetime.combine(start_date, time(9, 0), tzinfo=CAIRO_TZ)
                deadline = datetime.combine(finish_date, time(17, 0), tzinfo=CAIRO_TZ)
                reviewed_at = datetime.combine(finish_date + timedelta(days=1), time(14, 0), tzinfo=CAIRO_TZ)

                existing_t = conn.execute(
                    text("SELECT id FROM tasks WHERE company_id = :cid AND task_code = :code"),
                    {"cid": company_id, "code": task_code},
                ).fetchone()

                if existing_t:
                    task_id = existing_t[0]
                else:
                    task_id = uuid.uuid4()
                    # Insert task
                    conn.execute(
                        text("""
                            INSERT INTO tasks (
                                id, company_id, task_code, project_id, sprint_id, title, description,
                                complexity, priority, status, estimated_hours, remaining_estimated_hours,
                                planned_start, deadline, task_category, requires_project_membership, created_at, updated_at
                            ) VALUES (
                                :id, :company_id, :code, :project_id, NULL, :title, :description,
                                :complexity, :priority, 'DONE', :estimated_hours, 0.0,
                                :planned_start, :deadline, :task_category, false, :planned_start, :deadline
                            )
                        """),
                        {
                            "id": task_id,
                            "company_id": company_id,
                            "code": task_code,
                            "project_id": proj_id,
                            "title": f"{title} ({emp_code})",
                            "description": f"Completed historical work on {title} by {full_name}.",
                            "complexity": comp,
                            "priority": rnd.choice(["low", "medium", "high"]),
                            "estimated_hours": est_hrs,
                            "planned_start": planned_start,
                            "deadline": deadline,
                            "task_category": cat,
                        },
                    )

                # Insert required skills for the task (enabling similarity calculation)
                for s_name in req_skills:
                    if s_name in skills_map:
                        conn.execute(
                            text("""
                                INSERT INTO task_required_skills (
                                    id, task_id, skill_id, minimum_level, is_mandatory, requires_verification, weight
                                ) VALUES (
                                    :id, :task_id, :skill_id, :level, true, false, 1.0
                                ) ON CONFLICT (task_id, skill_id) DO NOTHING
                            """),
                            {
                                "id": uuid.uuid4(),
                                "task_id": task_id,
                                "skill_id": skills_map[s_name],
                                "level": rnd.randint(60, 80),
                            },
                        )

                # Insert task assignment (COMPLETED)
                conn.execute(
                    text("""
                        INSERT INTO task_assignments (
                            id, task_id, employee_id, assignment_status, planned_start, planned_end,
                            allocated_hours, remaining_estimated_hours, assigned_at
                        ) VALUES (
                            :id, :task_id, :employee_id, 'COMPLETED', :start, :end,
                            :allocated, 0.0, :assigned_at
                        ) ON CONFLICT (task_id, employee_id) DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4(),
                        "task_id": task_id,
                        "employee_id": emp_id,
                        "start": planned_start,
                        "end": deadline,
                        "allocated": est_hrs,
                        "assigned_at": planned_start - timedelta(days=1),
                    },
                )

                # Insert task outcome
                on_time = rnd.random() < 0.85
                quality = round(rnd.uniform(80.0, 97.0), 1)
                conn.execute(
                    text("""
                        INSERT INTO task_outcomes (
                            id, task_id, employee_id, quality_score, accepted, rework_count,
                            on_time, delay_reason, blocker_hours, reviewer_id, reviewed_at
                        ) VALUES (
                            :id, :task_id, :employee_id, :quality, true, :rework,
                            :on_time, :delay, :blocker, :reviewer, :reviewed_at
                        ) ON CONFLICT (task_id, employee_id) DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4(),
                        "task_id": task_id,
                        "employee_id": emp_id,
                        "quality": quality,
                        "rework": 0 if quality >= 85 else 1,
                        "on_time": on_time,
                        "delay": "NONE" if on_time else "EXTERNAL_BLOCKER",
                        "blocker": 0.0 if on_time else float(rnd.choice([4.0, 6.0])),
                        "reviewer": reviewer_id,
                        "reviewed_at": reviewed_at,
                    },
                )

                # Insert timesheet entry for this completed task
                actual_hrs = round(est_hrs * rnd.uniform(0.9, 1.15), 1)
                daily_hrs = min(actual_hrs, 7.5)
                conn.execute(
                    text("""
                        INSERT INTO timesheet_entries (
                            id, task_id, employee_id, entry_date, hours, description
                        ) VALUES (
                            :id, :task_id, :employee_id, :entry_date, :hours, :desc
                        )
                    """),
                    {
                        "id": uuid.uuid4(),
                        "task_id": task_id,
                        "employee_id": emp_id,
                        "entry_date": finish_date,
                        "hours": daily_hrs,
                        "desc": f"Completed {title}",
                    },
                )
                tasks_created += 1

        print(f"-> Created {tasks_created} new completed historical tasks across employees.")

        # 3. Configure Status Diversity (AVAILABLE, BUSY, PARTIALLY_AVAILABLE, ON_LEAVE, INACTIVE)
        print("3. Setting up status diversity across employees...")

        # Explicit role definitions:
        busy_codes = {"EMP-002", "EMP-007", "EMP-010", "EMP-018", "EMP-026", "EMP-031", "EMP-036", "EMP-041", "EMP-046", "EMP-054"}
        partial_codes = {"EMP-005", "EMP-009", "EMP-013", "EMP-017", "EMP-019", "EMP-022", "EMP-027", "EMP-033", "EMP-038", "EMP-048", "EMP-053"}
        leave_codes = {"EMP-006", "EMP-020", "EMP-044"}
        inactive_codes = {"EMP-025", "EMP-051"}
        # Everyone else is AVAILABLE (e.g. EMP-001, EMP-004, EMP-011, EMP-012, EMP-014, EMP-015, EMP-016, EMP-021, EMP-023, EMP-024, EMP-028, EMP-029, EMP-030, EMP-032, EMP-034, EMP-035, EMP-037, EMP-039, EMP-040, EMP-042, EMP-043, EMP-045, EMP-047, EMP-049, EMP-050, EMP-052, EMP-055)

        # Clear existing active commitments and active leaves
        conn.execute(text("DELETE FROM employee_commitments"))
        conn.execute(text("DELETE FROM leave_records"))

        # Clear existing active task assignments for available employees to keep their remaining work at 0
        conn.execute(
            text("""
                UPDATE task_assignments 
                SET assignment_status = 'COMPLETED', remaining_estimated_hours = 0.0
                WHERE assignment_status = 'ACTIVE'
            """)
        )

        COMMIT_START = datetime(2026, 11, 1, 0, 0, 0, tzinfo=CAIRO_TZ)
        COMMIT_END = datetime(2026, 11, 30, 23, 59, 59, tzinfo=CAIRO_TZ)

        for emp_id, emp_code, full_name, job_title, emp_status, dept_name in emp_rows:
            if emp_code in inactive_codes:
                conn.execute(
                    text("UPDATE employees SET employment_status = 'INACTIVE' WHERE id = :id"),
                    {"id": emp_id},
                )
            elif emp_code in leave_codes:
                # Active leave
                conn.execute(
                    text("UPDATE employees SET employment_status = 'ACTIVE' WHERE id = :id"),
                    {"id": emp_id},
                )
                conn.execute(
                    text("""
                        INSERT INTO leave_records (id, employee_id, leave_type, start_at, end_at, status)
                        VALUES (:id, :eid, 'ANNUAL', :start, :end, 'APPROVED')
                    """),
                    {
                        "id": uuid.uuid4(),
                        "eid": emp_id,
                        "start": COMMIT_START,
                        "end": COMMIT_END,
                    },
                )
            elif emp_code in busy_codes:
                # High commitment (34 hours) -> BUSY
                conn.execute(
                    text("UPDATE employees SET employment_status = 'ACTIVE' WHERE id = :id"),
                    {"id": emp_id},
                )
                conn.execute(
                    text("""
                        INSERT INTO employee_commitments (id, employee_id, title, start_at, end_at, hours)
                        VALUES (:id, :eid, 'Sprint 2 Core Delivery & High-Priority Tasks', :start, :end, :hrs)
                    """),
                    {
                        "id": uuid.uuid4(),
                        "eid": emp_id,
                        "start": COMMIT_START,
                        "end": COMMIT_END,
                        "hrs": float(rnd.choice([32.0, 36.0, 40.0])),
                    },
                )
            elif emp_code in partial_codes:
                # Moderate commitment (14-20 hours) -> PARTIALLY_AVAILABLE
                conn.execute(
                    text("UPDATE employees SET employment_status = 'ACTIVE' WHERE id = :id"),
                    {"id": emp_id},
                )
                conn.execute(
                    text("""
                        INSERT INTO employee_commitments (id, employee_id, title, start_at, end_at, hours)
                        VALUES (:id, :eid, 'Sprint 2 Ongoing Feature Development', :start, :end, :hrs)
                    """),
                    {
                        "id": uuid.uuid4(),
                        "eid": emp_id,
                        "start": COMMIT_START,
                        "end": COMMIT_END,
                        "hrs": float(rnd.choice([14.0, 18.0, 22.0])),
                    },
                )
            else:
                # 100% AVAILABLE
                conn.execute(
                    text("UPDATE employees SET employment_status = 'ACTIVE' WHERE id = :id"),
                    {"id": emp_id},
                )

        print("-> Status diversity configured (Available, Busy, Partially Available, On Leave, Inactive).")

        # 4. Configure Attendance & Timesheet Hours Diversity
        print("4. Enriching attendance records and monthly timesheets...")
        # Working days in current month (Nov 1 to Nov 10 = ~8 days; past month = 22 days)
        # We ensure a realistic distribution of total days present and hours worked
        nov_workdays = [
            date(2026, 11, d)
            for d in range(1, 11)
            if date(2026, 11, d).weekday() < 5
        ]  # Mon-Fri days in Nov

        oct_workdays = [
            date(2026, 10, d)
            for d in range(1, 32)
            if date(2026, 10, d).weekday() < 5
        ]  # Mon-Fri days in Oct

        all_workdays = oct_workdays + nov_workdays

        # Clear old attendance records to reseed cleanly with realistic varied days
        conn.execute(text("DELETE FROM attendance_records"))

        # Re-populate attendance with varied counts:
        # Group A (Senior high attendance): 20-22 days
        # Group B (Solid standard): 16-19 days
        # Group C (Moderate / Flexible): 12-15 days
        # Group D (Part-time / Leave): 7-10 days
        for idx, (emp_id, emp_code, full_name, job_title, emp_status, dept_name) in enumerate(emp_rows):
            if emp_code in inactive_codes:
                days_target = 6
                hrs_target = 42.0
            elif emp_code in leave_codes:
                days_target = 10
                hrs_target = 70.0
            elif emp_code in busy_codes:
                days_target = rnd.randint(19, 22)
                hrs_target = float(rnd.randint(145, 168))
            elif emp_code in partial_codes:
                days_target = rnd.randint(14, 18)
                hrs_target = float(rnd.randint(95, 128))
            else:
                # Available employees: varied!
                if idx % 3 == 0:
                    days_target = rnd.randint(19, 22)
                    hrs_target = float(rnd.randint(140, 165))
                elif idx % 3 == 1:
                    days_target = rnd.randint(15, 18)
                    hrs_target = float(rnd.randint(105, 135))
                else:
                    days_target = rnd.randint(11, 14)
                    hrs_target = float(rnd.randint(75, 100))

            chosen_days = sorted(rnd.sample(all_workdays, min(days_target, len(all_workdays))))
            for wd in chosen_days:
                is_remote = (wd.day + idx) % 3 == 0
                ci = datetime.combine(wd, time(8, rnd.randint(45, 59)), tzinfo=CAIRO_TZ)
                co = datetime.combine(wd, time(17, rnd.randint(0, 30)), tzinfo=CAIRO_TZ)
                conn.execute(
                    text("""
                        INSERT INTO attendance_records (
                            id, employee_id, work_date, check_in, check_out, attendance_status
                        ) VALUES (
                            :id, :eid, :wd, :ci, :co, :status
                        ) ON CONFLICT (employee_id, work_date) DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4(),
                        "eid": emp_id,
                        "wd": wd,
                        "ci": ci,
                        "co": co,
                        "status": "REMOTE" if is_remote else "PRESENT",
                    },
                )

            # Ensure timesheet entries match target hours
            # Find an existing task for this employee to attach timesheet entries to
            first_task = conn.execute(
                text("SELECT task_id FROM task_assignments WHERE employee_id = :eid LIMIT 1"),
                {"eid": emp_id},
            ).fetchone()
            if first_task:
                tid = first_task[0]
                # Check current logged hours
                cur_hrs = conn.execute(
                    text("SELECT coalesce(sum(hours), 0) FROM timesheet_entries WHERE employee_id = :eid"),
                    {"eid": emp_id},
                ).scalar() or 0.0

                diff_hrs = hrs_target - float(cur_hrs)
                if diff_hrs > 0:
                    # Insert daily entries of <= 7.5h
                    remaining_to_add = diff_hrs
                    day_idx = 0
                    while remaining_to_add > 0 and day_idx < len(chosen_days):
                        chunk = min(remaining_to_add, 7.5)
                        conn.execute(
                            text("""
                                INSERT INTO timesheet_entries (
                                    id, task_id, employee_id, entry_date, hours, description
                                ) VALUES (
                                    :id, :tid, :eid, :ed, :hrs, 'Standard sprint delivery work'
                                )
                            """),
                            {
                                "id": uuid.uuid4(),
                                "tid": tid,
                                "eid": emp_id,
                                "ed": chosen_days[day_idx],
                                "hrs": round(chunk, 1),
                            },
                        )
                        remaining_to_add -= chunk
                        day_idx += 1

        print("-> Attendance & timesheet hours diversity successfully written to database.")


if __name__ == "__main__":
    run()

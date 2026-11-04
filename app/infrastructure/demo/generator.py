"""Reproducible synthetic dataset generator.

Same (reference_date, seed) -> byte-identical rows, including UUIDs (uuid5 of natural keys). All dates are
relative to the reference date, which is moved forward to the next company working day if needed.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.infrastructure.demo.catalog import (
    DEPARTMENTS,
    FILLER_EMPLOYEES,
    PROJECTS,
    SCENARIO_ACTIVE,
    SCENARIO_EMPLOYEES,
    SCENARIO_TASKS,
    SECOND_COMPANY,
    SKILLS,
    SUBJECTS,
    TASK_DEPENDENCIES,
    TASK_KINDS,
    EmpSpec,
    Exp,
    Outcome,
)
from app.intelligence.workforce.calendar import is_working_day, next_working_days

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

    # --- helpers ------------------------------------------------------------------------------------
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

    # --- build ------------------------------------------------------------------------------------------
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

    # --- history ------------------------------------------------------------------------------------------
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

    # --- current workload ---------------------------------------------------------------------------
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

    # --- profile details ----------------------------------------------------------------------------
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

    # --- second tenant ------------------------------------------------------------------------------
    # --- periodic reviews and metric snapshots (derived from the reviewed outcomes) ---------------
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

"""Read-only queries behind the research agent's tools.

Tenant-scoped (one company), evaluated at the run's ``as_of``. Every figure comes from repositories or from the
deterministic engines (capacity, eligibility, scoring); nothing here re-implements a formula. Employees and
tasks are addressed by their business codes (``EMP-001``, ``TASK-SCN-01``) or UUIDs.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.application.container import Services
from app.application.pipeline import RecommendationPipeline
from app.core.exceptions import IncompleteDataError, InputValidationError, NotFoundError
from app.domain.entities.employee import Credential, Employee, EmployeeProfile
from app.domain.entities.organization import Company
from app.domain.entities.performance import PerformanceSummary
from app.domain.entities.recommendation import CandidateEvaluation, ExclusionDetail
from app.domain.entities.task import (
    ExecutionWindow,
    RequiredSkill,
    TaskRecord,
    TaskRequirements,
)
from app.domain.entities.workforce import AvailabilityResult, EmployeeWorkforceData
from app.domain.enums import (
    EmploymentStatus,
    LeaveStatus,
    RemainingEffortBasis,
    RequirementSource,
    SkillVerificationStatus,
    TaskCategory,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
    WindowPreset,
)
from app.domain.interfaces import UnitOfWork
from app.domain.policies import ALGORITHM_VERSION, ScoringConfig
from app.intelligence.employee_profiles.skills import assess_skill
from app.intelligence.performance.metrics import summarize_performance, task_similarity
from app.intelligence.recommendation.engine import CandidateInput
from app.intelligence.task_analysis.analyzer import TaskAnalyzer
from app.schemas.agent_tools import (
    AssignmentOut,
    AssignmentsOut,
    AttendanceOut,
    AttendanceRecordOut,
    AvailabilityOut,
    CandidateScoreOut,
    CapacityOut,
    CredentialOut,
    CriterionOut,
    DependencyOut,
    EligibilityOut,
    EmployeeAvailabilityOut,
    EmployeeExperienceOut,
    EmployeePerformanceOut,
    EmployeeProfileOut,
    EmployeeSkillsOut,
    EmployeeSummaryOut,
    EvaluationOut,
    ExclusionExplanationOut,
    ExclusionOut,
    ExperienceEntryOut,
    LeaveOut,
    LeavesOut,
    OutcomeOut,
    PerformanceOut,
    ProjectRoleOut,
    RankedCandidateOut,
    RankingOut,
    RecommendationEvidenceOut,
    RequiredSkillOut,
    ScheduleDayOut,
    ScheduleOut,
    SearchEmployeesOut,
    SimilarTaskOut,
    SimilarTasksOut,
    SkillOut,
    SprintOut,
    TaskDetailsOut,
    TaskSkillsOut,
    WorkloadItemOut,
    WorkloadOut,
)

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MAX_RANGE_DAYS = 62


def _summary(e: Employee) -> EmployeeSummaryOut:
    return EmployeeSummaryOut(
        employee_code=e.employee_code,
        name=e.full_name,
        job_title=e.job_title,
        department=e.department_name,
        team=e.team_name,
        employment_status=e.employment_status.value,
        experience_years=e.experience_years,
    )


def _performance(p: PerformanceSummary) -> PerformanceOut:
    return PerformanceOut(
        outcomes_reviewed=p.outcome_count,
        quality_samples=p.quality_sample_count,
        quality_mean=p.raw_quality_mean,
        acceptance_rate=p.acceptance_rate,
        average_rework=p.average_rework,
        reliability_samples=p.reliability_sample_count,
        on_time_rate=p.on_time_rate,
        attributable_delays=p.attributable_delays,
        excused_delays=p.excused_delays,
    )


def _availability(a: AvailabilityResult) -> AvailabilityOut:
    return AvailabilityOut(
        window_start=a.window.start,
        window_end=a.window.end,
        status=a.status.value,
        presence=a.presence.value,
        scheduled_hours=a.scheduled_hours,
        holiday_hours=a.holiday_hours,
        leave_hours=a.leave_hours,
        pending_leave_hours=a.pending_leave_hours,
        available_working_hours=a.available_working_hours,
        committed_work_hours=a.committed_work_hours,
        other_commitment_hours=a.other_commitment_hours,
        available_capacity_hours=a.available_capacity_hours,
        overcommitted_hours=a.overcommitted_hours,
        workload_certainty=a.workload_certainty.value,
        has_schedule=a.has_schedule,
        warnings=list(a.warnings),
    )


def _required_skill(s: RequiredSkill) -> RequiredSkillOut:
    return RequiredSkillOut(
        skill=s.skill_name,
        minimum_level=s.minimum_level,
        mandatory=s.mandatory,
        weight=s.weight,
        requires_verification=s.requires_verification,
        source=s.source.value,
    )


def _exclusions(reasons: tuple[ExclusionDetail, ...]) -> list[ExclusionOut]:
    return [ExclusionOut(code=r.code.value, message=r.message, details=dict(r.details)) for r in reasons]


class AgentQueryService:
    def __init__(
        self,
        services: Services,
        company: Company,
        *,
        as_of: datetime,
        config: ScoringConfig,
        analyzer: TaskAnalyzer,
        current_requirements: TaskRequirements | None = None,
        current_task_code: str | None = None,
        current_window: ExecutionWindow | None = None,
    ) -> None:
        if analyzer.llm is not None:  # pragma: no cover - guarded by the container
            raise ValueError("tool queries must use the deterministic analyzer (no nested LLM calls)")
        self.s = services
        self.uow: UnitOfWork = services.uow
        self.company = company
        self.as_of = as_of
        self.config = config
        self.analyzer = analyzer
        self.current_requirements = current_requirements
        self.current_task_code = current_task_code
        self.current_window = current_window
        self.pipeline = RecommendationPipeline(services, company)
        self.tz = ZoneInfo(company.timezone)

    # --- resolution helpers ------------------------------------------------------------------------
    def employee(self, ref: str) -> Employee:
        ref = ref.strip()
        try:
            found = self.uow.employees.get_employee(self.company.id, uuid.UUID(ref))
        except ValueError:
            found = self.uow.employees.get_employee_by_code(self.company.id, ref)
        if found is None:
            raise NotFoundError(f"Employee {ref!r} not found", details={"employee_id": ref})
        return found

    def profile(self, ref: str) -> EmployeeProfile:
        e = self.employee(ref)
        profile = self.uow.employees.get_profiles(self.company.id, [e.id]).get(e.id)
        if profile is None:  # pragma: no cover - same transaction
            raise NotFoundError(f"Employee {ref!r} not found")
        return profile

    def task(self, ref: str) -> TaskRecord:
        ref = ref.strip()
        try:
            found = self.uow.tasks.get_task(self.company.id, uuid.UUID(ref))
        except ValueError:
            found = self.uow.tasks.get_task_by_code(self.company.id, ref)
        if found is None:
            raise NotFoundError(f"Task {ref!r} not found", details={"task_id": ref})
        return found

    def requirements(self, task_ref: str | None) -> tuple[TaskRequirements, str | None]:
        """Requirements of ``task_ref``, or of the task under analysis in this run when omitted."""
        if task_ref is None or (self.current_task_code and task_ref.strip() == self.current_task_code):
            if self.current_requirements is None:
                raise IncompleteDataError("The task under analysis has no validated requirements yet")
            return self.current_requirements, self.current_task_code
        from app.application.tasks import task_input_from_record

        record = self.task(task_ref)
        catalog = self.uow.tasks.get_skill_catalog(self.company.id)
        analysis = self.analyzer.analyze(task_input_from_record(record), catalog)
        if analysis.requirements is None:
            raise IncompleteDataError(
                f"Task {record.task_code} lacks critical information",
                details={"missing_information": list(analysis.missing_information)},
            )
        return analysis.requirements, record.task_code

    def window(self, start: datetime | None, end: datetime | None) -> ExecutionWindow:
        if start is None and end is None:
            if self.current_window is None:
                raise InputValidationError("window_start and window_end are required")
            return self.current_window
        if start is None or end is None:
            raise InputValidationError("Provide both window_start and window_end, or neither")
        start = start if start.tzinfo else start.replace(tzinfo=self.tz)
        end = end if end.tzinfo else end.replace(tzinfo=self.tz)
        if end - start > timedelta(days=_MAX_RANGE_DAYS):
            raise InputValidationError(f"Execution windows are limited to {_MAX_RANGE_DAYS} days")
        return ExecutionWindow(start=start, end=end, preset=WindowPreset.EXPLICIT)

    def date_range(
        self, start: date | None, end: date | None, *, offset_days: int, length_days: int
    ) -> tuple[date, date]:
        """Defaults: [today + offset, today + offset + length]; one given bound anchors the other."""
        today = self.as_of.astimezone(self.tz).date()
        if start is None and end is None:
            start = today + timedelta(days=offset_days)
        if start is None:
            assert end is not None
            start = end - timedelta(days=length_days)
        if end is None:
            end = start + timedelta(days=length_days)
        if end < start:
            raise InputValidationError("end_date must not be before start_date")
        if (end - start).days > _MAX_RANGE_DAYS:
            raise InputValidationError(f"Date ranges are limited to {_MAX_RANGE_DAYS} days")
        return start, end

    def _range_window(self, start: date, end: date) -> ExecutionWindow:
        return ExecutionWindow(
            start=datetime.combine(start, time.min, tzinfo=self.tz),
            end=datetime.combine(end + timedelta(days=1), time.min, tzinfo=self.tz),
        )

    def _workforce(
        self, e: Employee, window: ExecutionWindow, *, attendance_since: datetime | None = None
    ) -> tuple[EmployeeWorkforceData, AvailabilityResult]:
        if attendance_since is None:
            data, holidays = self.s.workforce.workforce_data(self.company, [e.id], window, self.as_of)
        else:
            data = self.uow.workforce.get_workforce_data(
                self.company.id,
                [e.id],
                window_start=window.start,
                window_end=window.end,
                attendance_since=attendance_since,
            )
            holidays = self.s.workforce.holidays(
                self.company, attendance_since.date(), window.end.date() + timedelta(days=1)
            )
        wf = data[e.id]
        return wf, self.s.workforce.calculate(data, window, self.as_of, holidays)[e.id]

    def _credential(self, c: Credential) -> CredentialOut:
        valid = c.verification_status != SkillVerificationStatus.REJECTED and (
            c.expires_at is None or c.expires_at >= self.as_of.date()
        )
        return CredentialOut(
            name=c.name,
            credential_type=c.credential_type.value,
            issuer=c.issuer,
            verification=c.verification_status.value,
            issued_at=c.issued_at,
            expires_at=c.expires_at,
            valid_on_evaluation_date=valid,
        )

    def _skills(self, profile: EmployeeProfile) -> list[SkillOut]:
        evidence: dict[uuid.UUID, list[str]] = defaultdict(list)
        for ev in profile.evidence:
            evidence[ev.skill_id].append(ev.evidence_type.value)
        out = []
        for s in profile.skills:
            a = assess_skill(s, self.as_of, self.config.skills)
            out.append(
                SkillOut(
                    skill=s.skill_name,
                    declared_level=s.proficiency_level,
                    verification=s.verification_status.value,
                    effective_level=a.effective_level,
                    months_since_assessment=a.months_since_assessment,
                    stale=a.stale,
                    evidence_count=len(evidence[s.skill_id]),
                    evidence_types=sorted(set(evidence[s.skill_id])),
                )
            )
        return out

    def _projects(self, profile: EmployeeProfile) -> list[ProjectRoleOut]:
        history = self.uow.performance.get_project_history(self.company.id, [profile.employee.id]).get(
            profile.employee.id, {}
        )
        roles = {m.project_id: (m.project_name, m.role) for m in profile.memberships}
        out = [
            ProjectRoleOut(project=name, role=role, completed_tasks=history.get(pid, 0))
            for pid, (name, role) in roles.items()
        ]
        return sorted(out, key=lambda p: (-p.completed_tasks, p.project))

    # --- Group A: employees ------------------------------------------------------------------------
    def search_employees(
        self,
        *,
        department: str | None,
        team: str | None,
        job_title: str | None,
        required_skills: list[str],
        employment_status: EmploymentStatus | None,
        limit: int,
    ) -> SearchEmployeesOut:
        page = self.uow.employees.search_employees(
            self.company.id,
            department=department,
            team=team,
            job_title=job_title,
            skill_names=tuple(required_skills),
            status=employment_status,
            limit=limit,
        )
        return SearchEmployeesOut(
            total=page.total, returned=len(page.items), employees=[_summary(e) for e in page.items]
        )

    def employee_profile(self, ref: str) -> EmployeeProfileOut:
        profile = self.profile(ref)
        e = profile.employee
        window = self.current_window or self.window(self.as_of, self.as_of + timedelta(days=7))
        wf, availability = self._workforce(e, window)
        outcomes = self.uow.performance.get_outcomes(self.company.id, [e.id]).get(e.id, [])
        return EmployeeProfileOut(
            employee=_summary(e),
            hire_date=e.hire_date,
            skills=self._skills(profile),
            certifications=[self._credential(c) for c in profile.credentials],
            projects=self._projects(profile),
            performance=_performance(summarize_performance(e.id, outcomes, self.config.performance)),
            availability_in_window=_availability(availability),
            current_assignments=len(wf.assignments),
        )

    def employee_skills(self, ref: str) -> EmployeeSkillsOut:
        profile = self.profile(ref)
        return EmployeeSkillsOut(employee_code=profile.employee.employee_code, skills=self._skills(profile))

    def employee_experience(self, ref: str) -> EmployeeExperienceOut:
        profile = self.profile(ref)
        today = self.as_of.date()
        return EmployeeExperienceOut(
            employee_code=profile.employee.employee_code,
            experience_years=profile.employee.experience_years,
            experiences=[
                ExperienceEntryOut(
                    organization_or_project=x.organization_or_project,
                    role=x.role,
                    start_date=x.start_date,
                    end_date=x.end_date,
                    years=round(((x.end_date or today) - x.start_date).days / 365.25, 1),
                    skills=list(x.skill_names),
                )
                for x in profile.experiences
            ],
            projects=self._projects(profile),
            certifications_and_training=[self._credential(c) for c in profile.credentials],
        )

    def employee_performance(
        self,
        ref: str,
        *,
        date_from: date | None,
        date_to: date | None,
        task_category: TaskCategory | None,
        recent_limit: int,
    ) -> EmployeePerformanceOut:
        e = self.employee(ref)
        outcomes = self.uow.performance.get_outcomes(self.company.id, [e.id]).get(e.id, [])
        selected = [
            o
            for o in outcomes
            if (task_category is None or o.task_category == task_category)
            and (date_from is None or (o.reviewed_at is not None and o.reviewed_at.date() >= date_from))
            and (date_to is None or (o.reviewed_at is not None and o.reviewed_at.date() <= date_to))
        ]
        recent = sorted(selected, key=lambda o: o.reviewed_at or datetime.min.replace(tzinfo=self.tz))
        return EmployeePerformanceOut(
            employee_code=e.employee_code,
            filters={
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "task_category": task_category.value if task_category else None,
            },
            summary=_performance(summarize_performance(e.id, selected, self.config.performance)),
            recent_outcomes=[
                OutcomeOut(
                    task_code=o.task_code,
                    task_category=o.task_category.value if o.task_category else None,
                    complexity=o.complexity.value if o.complexity else None,
                    quality_score=o.quality_score,
                    accepted=o.accepted,
                    rework_count=o.rework_count,
                    on_time=o.on_time,
                    delay_reason=o.delay_reason.value if o.delay_reason else None,
                    reviewed_at=o.reviewed_at.date() if o.reviewed_at else None,
                )
                for o in reversed(recent[-recent_limit:])
            ],
            periodic_reviews=[
                EvaluationOut(
                    period_start=v.period_start,
                    period_end=v.period_end,
                    technical_quality=v.technical_quality,
                    delivery=v.delivery,
                    collaboration=v.collaboration,
                )
                for v in self.uow.performance.get_evaluations(self.company.id, e.id)
            ],
        )

    # --- Group B: workforce ------------------------------------------------------------------------
    def attendance(self, ref: str, start: date | None, end: date | None) -> AttendanceOut:
        e = self.employee(ref)
        start, end = self.date_range(start, end, offset_days=-14, length_days=14)
        since = datetime.combine(start, time.min, tzinfo=self.tz)
        window = self.current_window or self._range_window(end, end)
        wf, availability = self._workforce(e, window, attendance_since=since)
        return AttendanceOut(
            employee_code=e.employee_code,
            presence_at_evaluation_time=availability.presence.value,
            start_date=start,
            end_date=end,
            records=[
                AttendanceRecordOut(
                    work_date=r.work_date, status=r.status.value, check_in=r.check_in, check_out=r.check_out
                )
                for r in sorted(wf.attendance, key=lambda r: r.work_date)
                if start <= r.work_date <= end
            ],
        )

    def leaves(self, ref: str, start: date | None, end: date | None) -> LeavesOut:
        e = self.employee(ref)
        start, end = self.date_range(start, end, offset_days=0, length_days=14)
        window = self._range_window(start, end)
        wf, availability = self._workforce(e, window)
        return LeavesOut(
            employee_code=e.employee_code,
            start_date=start,
            end_date=end,
            leaves=[
                LeaveOut(
                    leave_type=lv.leave_type.value,
                    status=lv.status.value,
                    start_at=lv.start_at,
                    end_at=lv.end_at,
                )
                for lv in sorted(wf.leaves, key=lambda lv: lv.start_at)
                if lv.status in (LeaveStatus.APPROVED, LeaveStatus.REQUESTED)
            ],
            approved_leave_hours_in_range=availability.leave_hours,
            pending_leave_hours_in_range=availability.pending_leave_hours,
        )

    def schedule(self, ref: str, start: date | None, end: date | None) -> ScheduleOut:
        e = self.employee(ref)
        start, end = self.date_range(start, end, offset_days=0, length_days=6)
        window = self._range_window(start, end)
        wf, availability = self._workforce(e, window)
        holidays = sorted(d for d in self.s.workforce.holidays(self.company, start, end) if start <= d <= end)
        return ScheduleOut(
            employee_code=e.employee_code,
            timezone=wf.timezone,
            has_schedule=bool(wf.schedule),
            weekly_schedule=[
                ScheduleDayOut(
                    weekday=_WEEKDAYS[d.weekday],
                    start_time=d.start_time.isoformat(timespec="minutes"),
                    end_time=d.end_time.isoformat(timespec="minutes"),
                    break_minutes=d.break_minutes,
                )
                for d in sorted(wf.schedule, key=lambda d: d.weekday)
            ],
            start_date=start,
            end_date=end,
            holidays_in_range=holidays,
            scheduled_hours_in_range=availability.scheduled_hours,
            holiday_hours_in_range=availability.holiday_hours,
        )

    def assignments(self, ref: str, window: ExecutionWindow) -> AssignmentsOut:
        e = self.employee(ref)
        wf, _ = self._workforce(e, window)
        return AssignmentsOut(
            employee_code=e.employee_code,
            window_start=window.start,
            window_end=window.end,
            assignments=[
                AssignmentOut(
                    task_code=a.task_code,
                    task_title=a.task_title,
                    status=a.assignment_status.value,
                    planned_start=a.planned_start,
                    planned_end=a.planned_end,
                    allocated_hours=a.allocated_hours,
                    remaining_estimated_hours=a.remaining_estimated_hours,
                    active_assignees=a.active_assignee_count,
                    logged_hours=a.logged_hours,
                )
                for a in sorted(wf.assignments, key=lambda a: a.task_code)
            ],
        )

    def workload(self, ref: str, window: ExecutionWindow) -> WorkloadOut:
        e = self.employee(ref)
        _, a = self._workforce(e, window)
        return WorkloadOut(
            employee_code=e.employee_code,
            window_start=window.start,
            window_end=window.end,
            committed_work_hours=a.committed_work_hours,
            other_commitment_hours=a.other_commitment_hours,
            workload_certainty=a.workload_certainty.value,
            items=[
                WorkloadItemOut(
                    task_code=i.task_code,
                    remaining_hours=i.remaining_hours,
                    basis=i.basis.value,
                    committed_hours_in_window=i.committed_hours_in_window,
                    certainty=i.certainty.value,
                    notes=list(i.notes),
                )
                for i in a.items
            ],
            unknown_remaining_effort_tasks=[
                i.task_code for i in a.items if i.basis == RemainingEffortBasis.UNKNOWN
            ],
        )

    def capacity(self, ref: str, window: ExecutionWindow) -> CapacityOut:
        e = self.employee(ref)
        _, a = self._workforce(e, window)
        return CapacityOut(
            employee_code=e.employee_code,
            window_start=window.start,
            window_end=window.end,
            scheduled_hours=a.scheduled_hours,
            holiday_hours=a.holiday_hours,
            leave_hours=a.leave_hours,
            available_working_hours=a.available_working_hours,
            committed_work_hours=a.committed_work_hours,
            other_commitment_hours=a.other_commitment_hours,
            available_capacity_hours=a.available_capacity_hours,
            overcommitted_hours=a.overcommitted_hours,
            utilization=a.utilization,
        )

    def availability(self, ref: str, window: ExecutionWindow) -> EmployeeAvailabilityOut:
        e = self.employee(ref)
        _, a = self._workforce(e, window)
        return EmployeeAvailabilityOut(employee_code=e.employee_code, availability=_availability(a))

    # --- Group C: tasks ----------------------------------------------------------------------------
    def task_details(self, task_ref: str | None) -> TaskDetailsOut:
        if task_ref is None and self.current_task_code is None:
            req = self._current()
            return TaskDetailsOut(
                task_code=None,
                title=req.title,
                status=None,
                project=None,
                sprint=None,
                priority=req.priority.value,
                complexity=req.complexity.value,
                task_category=req.task_category.value,
                estimated_hours=req.estimated_hours,
                remaining_estimated_hours=None,
                planned_start=None,
                deadline=None,
                required_skills=[_required_skill(s) for s in req.required_skills],
                required_certifications=list(req.required_certifications),
                dependencies=[],
                description_excerpt=None,
            )
        t = self.task(task_ref or str(self.current_task_code))
        sprint = self.uow.tasks.get_sprint(self.company.id, t.sprint_id) if t.sprint_id else None
        return TaskDetailsOut(
            task_code=t.task_code,
            title=t.title,
            status=t.status.value,
            project=t.project_name,
            sprint=SprintOut(
                name=sprint.name, start_date=sprint.start_date, end_date=sprint.end_date, status=sprint.status
            )
            if sprint
            else None,
            priority=t.priority.value if t.priority else None,
            complexity=t.complexity.value if t.complexity else None,
            task_category=t.task_category.value if t.task_category else None,
            estimated_hours=t.estimated_hours,
            remaining_estimated_hours=t.remaining_estimated_hours,
            planned_start=t.planned_start,
            deadline=t.deadline,
            required_skills=[_required_skill(s) for s in t.required_skills],
            required_certifications=list(t.required_certifications),
            dependencies=[
                DependencyOut(
                    task_code=d.task_code,
                    title=d.title,
                    status=d.status.value,
                    dependency_type=d.dependency_type.value,
                )
                for d in t.dependencies
            ],
            description_excerpt=(t.description or "")[:500] or None,
        )

    def _current(self) -> TaskRequirements:
        if self.current_requirements is None:
            raise IncompleteDataError("The task under analysis has no validated requirements yet")
        return self.current_requirements

    def task_required_skills(self, task_ref: str | None) -> TaskSkillsOut:
        req, code = self.requirements(task_ref)
        return TaskSkillsOut(
            task_code=code,
            mandatory=[_required_skill(s) for s in req.required_skills if s.mandatory],
            optional=[_required_skill(s) for s in req.required_skills if not s.mandatory],
            required_certifications=list(req.required_certifications),
        )

    def similar_tasks(
        self,
        *,
        skills: list[str],
        task_category: TaskCategory | None,
        complexity: TaskComplexity | None,
        limit: int,
    ) -> SimilarTasksOut:
        catalog = {s.name.lower(): s for s in self.uow.tasks.get_skill_catalog(self.company.id)}
        unknown = [s for s in skills if s.lower() not in catalog]
        if unknown:
            raise InputValidationError(
                f"Unknown skill(s): {', '.join(unknown)}", details={"unknown": unknown}
            )
        if not skills and task_category is None:
            raise InputValidationError("Provide at least one skill or a task_category")
        probe = TaskRequirements(
            title="similarity probe",
            task_category=task_category or TaskCategory.OTHER,
            required_skills=tuple(
                RequiredSkill(
                    skill_id=catalog[s.lower()].id,
                    skill_name=catalog[s.lower()].name,
                    minimum_level=0,
                    mandatory=False,
                    source=RequirementSource.MANAGER,
                )
                for s in skills
            ),
            complexity=complexity or TaskComplexity.MEDIUM,
            complexity_source=RequirementSource.MANAGER,
            estimated_hours=1,
            estimated_hours_source=RequirementSource.MANAGER,
            priority=TaskPriority.MEDIUM,
        )
        ids = self.uow.employees.list_employee_ids(self.company.id)
        code_of = {
            p.employee.id: p.employee.employee_code
            for p in self.uow.employees.get_profiles(self.company.id, ids).values()
        }
        by_task: dict[str, list[tuple[str, float | None, float]]] = defaultdict(list)
        meta: dict[str, tuple[str | None, str | None]] = {}
        for eid, outcomes in self.uow.performance.get_outcomes(self.company.id, ids).items():
            for o in outcomes:
                if complexity is not None and o.complexity != complexity:
                    continue
                sim = task_similarity(o, probe)
                if sim <= 0:
                    continue
                by_task[o.task_code].append((code_of.get(eid, "?"), o.quality_score, sim))
                meta[o.task_code] = (
                    o.task_category.value if o.task_category else None,
                    o.complexity.value if o.complexity else None,
                )
        results = []
        for code, rows in by_task.items():
            qualities = [q for _, q, _ in rows if q is not None]
            results.append(
                SimilarTaskOut(
                    task_code=code,
                    task_category=meta[code][0],
                    complexity=meta[code][1],
                    similarity=round(max(s for _, _, s in rows), 3),
                    completed_by=sorted({c for c, _, _ in rows}),
                    average_quality=round(sum(qualities) / len(qualities), 1) if qualities else None,
                )
            )
        results.sort(key=lambda r: (-r.similarity, r.task_code))
        return SimilarTasksOut(
            criteria={
                "skills": [catalog[s.lower()].name for s in skills],
                "task_category": task_category.value if task_category else None,
                "complexity": complexity.value if complexity else None,
                "status": TaskStatus.DONE.value,
            },
            results=results[:limit],
        )

    # --- Group D: deterministic evaluation -------------------------------------------------------------
    def _candidate(self, ref: str, requirements: TaskRequirements, window: ExecutionWindow) -> CandidateInput:
        profile = self.profile(ref)
        e = profile.employee
        _, availability = self._workforce(e, window)
        perf = self.pipeline.performance([e.id])
        return CandidateInput(
            profile=profile,
            availability=availability,
            outcomes=perf.outcomes.get(e.id, ()),
            project_history=perf.project_history.get(e.id, {}),
        )

    def eligibility(self, ref: str, task_ref: str | None, window: ExecutionWindow) -> EligibilityOut:
        req, code = self.requirements(task_ref)
        candidate = self._candidate(ref, req, window)
        result = self.s.engine.check_eligibility(req, candidate, self.as_of)
        return EligibilityOut(
            employee_code=candidate.profile.employee.employee_code,
            task_code=code,
            eligible=result.eligible,
            exclusion_reasons=_exclusions(result.exclusion_reasons),
            warnings=list(result.warnings),
        )

    def candidate_score(self, ref: str, task_ref: str | None, window: ExecutionWindow) -> CandidateScoreOut:
        req, code = self.requirements(task_ref)
        candidate = self._candidate(ref, req, window)
        eligibility = self.s.engine.check_eligibility(req, candidate, self.as_of)
        ev: CandidateEvaluation = self.s.engine.score_candidate(req, candidate, eligibility, self.as_of)
        return CandidateScoreOut(
            employee_code=ev.employee_code,
            task_code=code,
            eligible=ev.eligible,
            suitability_score=ev.suitability_score,
            data_coverage=ev.data_coverage,
            confidence=ev.confidence,
            confidence_level=ev.confidence_level.value,
            criteria=[
                CriterionOut(
                    criterion=c.criterion.value,
                    applicable=c.applicable,
                    has_evidence=c.has_evidence,
                    raw_score=c.raw_score,
                    score_used=c.score_used,
                    weight=round(c.weight, 4),
                    weighted_score=round(c.weighted_score, 2),
                    evidence=list(c.evidence),
                )
                for c in ev.criteria
            ],
            exclusion_reasons=_exclusions(ev.exclusion_reasons),
            algorithm_version=ALGORITHM_VERSION,
        )

    def rank(self, task_ref: str | None, window: ExecutionWindow, top_n: int) -> RankingOut:
        req, code = self.requirements(task_ref)
        result = self.pipeline.evaluate(req, window, self.as_of)
        summary: dict[str, int] = defaultdict(int)
        for c in result.excluded:
            for r in {r.code.value for r in c.exclusion_reasons}:
                summary[r] += 1
        return RankingOut(
            task_code=code,
            status=result.status.value,
            algorithm_version=result.algorithm_version,
            ranked=[
                RankedCandidateOut(
                    rank=c.rank or 0,
                    employee_code=c.employee_code,
                    name=c.full_name,
                    suitability_score=c.suitability_score or 0.0,
                    data_coverage=c.data_coverage,
                    confidence_level=c.confidence_level.value,
                    availability_status=c.availability_status.value,
                )
                for c in result.ranked[:top_n]
            ],
            eligible_count=len(result.ranked),
            excluded_count=len(result.excluded),
            exclusion_summary=dict(sorted(summary.items())),
            warnings=list(result.warnings),
        )

    # --- Group E: evidence -----------------------------------------------------------------------------
    def recommendation_evidence(self, recommendation_id: str, employee_ref: str) -> RecommendationEvidenceOut:
        e = self.employee(employee_ref)
        try:
            rid = uuid.UUID(recommendation_id.strip())
        except ValueError as exc:
            raise InputValidationError("recommendation_id must be a UUID") from exc
        stored = self.uow.recommendations.get_run(self.company.id, rid)
        if stored is None:
            raise NotFoundError("Recommendation not found", details={"recommendation_id": recommendation_id})
        snap = stored.result_snapshot
        candidates = [
            *([snap["primary_candidate"]] if snap.get("primary_candidate") else []),
            *snap.get("alternatives", []),
            *snap.get("excluded_candidates", []),
        ]
        match = next((c for c in candidates if c.get("employee_code") == e.employee_code), None)
        if match is None:
            return RecommendationEvidenceOut(
                recommendation_id=str(rid),
                employee_code=e.employee_code,
                found=False,
                status=stored.status.value,
            )
        return RecommendationEvidenceOut(
            recommendation_id=str(rid),
            employee_code=e.employee_code,
            found=True,
            status=stored.status.value,
            rank=match.get("rank"),
            eligible=match.get("eligible"),
            suitability_score=match.get("suitability_score"),
            score_components=[
                {
                    k: c.get(k)
                    for k in ("criterion", "raw_score", "score_used", "weight", "has_evidence", "evidence")
                }
                for c in match.get("score_components", [])
            ],
            exclusion_reasons=list(match.get("exclusion_reasons", [])),
        )

    def explain_exclusion(
        self, ref: str, task_ref: str | None, window: ExecutionWindow
    ) -> ExclusionExplanationOut:
        result = self.eligibility(ref, task_ref, window)
        summary = (
            "Eligible: no hard constraint excludes this employee."
            if result.eligible
            else "Excluded by: " + "; ".join(r.message for r in result.exclusion_reasons)
        )
        return ExclusionExplanationOut(
            employee_code=result.employee_code,
            task_code=result.task_code,
            eligible=result.eligible,
            reasons=result.exclusion_reasons,
            summary=summary,
        )

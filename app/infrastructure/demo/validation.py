"""Consistency checks over the demo (or any) dataset. Each check is a SQL query returning violating rows."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

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

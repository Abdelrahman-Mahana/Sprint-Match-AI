"""Importing this package registers every table on ``Base.metadata`` (used by Alembic)."""

from app.infrastructure.database.models.ai import (
    AgentExecutionLogModel,
    AgentRunErrorModel,
    AgentToolExecutionModel,
    AuditEventModel,
    RecommendationCandidateModel,
    RecommendationRunModel,
    RecommendationScoreComponentModel,
    TaskAnalysisModel,
)
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.employee import (
    EmployeeCredentialModel,
    EmployeeExperienceModel,
    EmployeeExperienceSkillModel,
    EmployeeModel,
    EmployeeSkillEvidenceModel,
    EmployeeSkillModel,
    SkillModel,
)
from app.infrastructure.database.models.organization import (
    CalendarHolidayModel,
    CompanyModel,
    DepartmentModel,
    TeamModel,
)
from app.infrastructure.database.models.performance import (
    PerformanceEvaluationModel,
    PerformanceMetricModel,
    TaskOutcomeModel,
)
from app.infrastructure.database.models.project import (
    ProjectMemberModel,
    ProjectModel,
    SprintModel,
    TaskDependencyModel,
    TaskModel,
    TaskRequiredSkillModel,
)
from app.infrastructure.database.models.workforce import (
    AttendanceRecordModel,
    EmployeeCommitmentModel,
    LeaveRecordModel,
    TaskAssignmentModel,
    TimesheetEntryModel,
    WorkScheduleDayModel,
)

__all__ = [
    "AgentExecutionLogModel",
    "AgentRunErrorModel",
    "AgentToolExecutionModel",
    "AttendanceRecordModel",
    "AuditEventModel",
    "Base",
    "CalendarHolidayModel",
    "CompanyModel",
    "DepartmentModel",
    "EmployeeCommitmentModel",
    "EmployeeCredentialModel",
    "EmployeeExperienceModel",
    "EmployeeExperienceSkillModel",
    "EmployeeModel",
    "EmployeeSkillEvidenceModel",
    "EmployeeSkillModel",
    "LeaveRecordModel",
    "PerformanceEvaluationModel",
    "PerformanceMetricModel",
    "ProjectMemberModel",
    "ProjectModel",
    "RecommendationCandidateModel",
    "RecommendationRunModel",
    "RecommendationScoreComponentModel",
    "SkillModel",
    "SprintModel",
    "TaskAnalysisModel",
    "TaskAssignmentModel",
    "TaskDependencyModel",
    "TaskModel",
    "TaskOutcomeModel",
    "TaskRequiredSkillModel",
    "TeamModel",
    "TimesheetEntryModel",
    "WorkScheduleDayModel",
]

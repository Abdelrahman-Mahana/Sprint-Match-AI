"""Domain enumerations. Values are persisted as strings (validated by CHECK constraints)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    MANAGER = "manager"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return {"viewer": 1, "manager": 2, "admin": 3}[self.value]


class EmploymentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class SkillVerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PENDING_REVIEW = "PENDING_REVIEW"
    SELF_DECLARED = "SELF_DECLARED"
    REJECTED = "REJECTED"


class EvidenceType(StrEnum):
    TASK_OUTCOME = "TASK_OUTCOME"
    CODE_REVIEW = "CODE_REVIEW"
    ASSESSMENT = "ASSESSMENT"
    CERTIFICATION = "CERTIFICATION"
    PROJECT_DELIVERY = "PROJECT_DELIVERY"


class CredentialType(StrEnum):
    CERTIFICATION = "CERTIFICATION"
    TRAINING = "TRAINING"


class ProjectStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"


class SprintStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class TaskStatus(StrEnum):
    BACKLOG = "BACKLOG"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskComplexity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def level(self) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskCategory(StrEnum):
    BACKEND_DEVELOPMENT = "backend_development"
    FRONTEND_DEVELOPMENT = "frontend_development"
    MOBILE_DEVELOPMENT = "mobile_development"
    DATA_ENGINEERING = "data_engineering"
    MACHINE_LEARNING = "machine_learning"
    DEVOPS = "devops"
    QA_TESTING = "qa_testing"
    DATABASE = "database"
    SECURITY = "security"
    OTHER = "other"


class AssignmentStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    @property
    def consumes_capacity(self) -> bool:
        return self in (AssignmentStatus.PLANNED, AssignmentStatus.ACTIVE)


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    REMOTE = "REMOTE"
    ABSENT = "ABSENT"
    ON_LEAVE = "ON_LEAVE"


class LeaveType(StrEnum):
    ANNUAL = "ANNUAL"
    CASUAL = "CASUAL"
    SICK = "SICK"
    TRAINING = "TRAINING"
    OTHER = "OTHER"


class LeaveStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class DelayReason(StrEnum):
    NONE = "NONE"
    EMPLOYEE = "EMPLOYEE"
    EXTERNAL_BLOCKER = "EXTERNAL_BLOCKER"
    DEPENDENCY = "DEPENDENCY"
    SCOPE_CHANGE = "SCOPE_CHANGE"
    UNSPECIFIED = "UNSPECIFIED"

    @property
    def attributable_to_employee(self) -> bool:
        # UNSPECIFIED is conservatively attributed to the employee (documented in SCORING.md).
        return self in (DelayReason.EMPLOYEE, DelayReason.UNSPECIFIED)


class PresenceStatus(StrEnum):
    PRESENT = "PRESENT"
    CHECKED_OUT = "CHECKED_OUT"
    ABSENT = "ABSENT"
    ON_LEAVE = "ON_LEAVE"
    NOT_SCHEDULED = "NOT_SCHEDULED"
    NO_RECORD = "NO_RECORD"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIALLY_AVAILABLE = "PARTIALLY_AVAILABLE"
    BUSY = "BUSY"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class WorkloadCertainty(StrEnum):
    CERTAIN = "CERTAIN"
    ESTIMATED = "ESTIMATED"
    UNCERTAIN = "UNCERTAIN"


class RemainingEffortBasis(StrEnum):
    ASSIGNMENT_REMAINING_ESTIMATE = "ASSIGNMENT_REMAINING_ESTIMATE"
    TASK_REMAINING_SOLE_ASSIGNEE = "TASK_REMAINING_SOLE_ASSIGNEE"
    ALLOCATION_MINUS_LOGGED = "ALLOCATION_MINUS_LOGGED"
    UNKNOWN = "UNKNOWN"


class WindowPreset(StrEnum):
    TODAY = "TODAY"
    REMAINING_DAY = "REMAINING_DAY"
    NEXT_WORKING_DAY = "NEXT_WORKING_DAY"
    NEXT_WORKING_DAYS = "NEXT_WORKING_DAYS"
    CURRENT_SPRINT = "CURRENT_SPRINT"
    TASK_PLANNED = "TASK_PLANNED"
    EXPLICIT = "EXPLICIT"


class RequirementSource(StrEnum):
    MANAGER = "MANAGER"
    TASK_RECORD = "TASK_RECORD"
    LLM_SUGGESTED = "LLM_SUGGESTED"
    TEXT_MATCH = "TEXT_MATCH"
    DEFAULT = "DEFAULT"


class Criterion(StrEnum):
    SKILL_MATCH = "SKILL_MATCH"
    EXPERIENCE = "EXPERIENCE"
    QUALITY = "QUALITY"
    RELIABILITY = "RELIABILITY"
    CAPACITY = "CAPACITY"
    COMPLEXITY_FIT = "COMPLEXITY_FIT"
    PROJECT_FAMILIARITY = "PROJECT_FAMILIARITY"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExclusionReason(StrEnum):
    EMPLOYEE_INACTIVE = "EMPLOYEE_INACTIVE"
    MANDATORY_SKILL_MISSING = "MANDATORY_SKILL_MISSING"
    MANDATORY_SKILL_BELOW_THRESHOLD = "MANDATORY_SKILL_BELOW_THRESHOLD"
    MANDATORY_SKILL_NOT_VERIFIED = "MANDATORY_SKILL_NOT_VERIFIED"
    NO_REQUIRED_SKILL_COVERAGE = "NO_REQUIRED_SKILL_COVERAGE"
    TEAM_ACCESS_REQUIRED = "TEAM_ACCESS_REQUIRED"
    PROJECT_ACCESS_REQUIRED = "PROJECT_ACCESS_REQUIRED"
    ON_LEAVE_DURING_WINDOW = "ON_LEAVE_DURING_WINDOW"
    NO_WORKING_TIME_IN_WINDOW = "NO_WORKING_TIME_IN_WINDOW"
    AVAILABILITY_UNKNOWN = "AVAILABILITY_UNKNOWN"
    INSUFFICIENT_AVAILABLE_CAPACITY = "INSUFFICIENT_AVAILABLE_CAPACITY"
    REQUIRED_CERTIFICATION_MISSING = "REQUIRED_CERTIFICATION_MISSING"

    @property
    def is_data_gap(self) -> bool:
        """True when exclusion is caused by missing data rather than a confirmed hard constraint."""
        return self is ExclusionReason.AVAILABILITY_UNKNOWN


class RecommendationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NEEDS_INPUT = "NEEDS_INPUT"
    NO_SUITABLE_CANDIDATE = "NO_SUITABLE_CANDIDATE"
    PARTIAL_DATA = "PARTIAL_DATA"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    FAILED = "FAILED"


class DependencyType(StrEnum):
    FINISH_TO_START = "FINISH_TO_START"
    RELATED = "RELATED"


class AgentMode(StrEnum):
    """How the workforce research agent talks to the model (resolved from provider capabilities)."""

    NATIVE = "native"  # provider-native tool calling, executed by LangGraph's ToolNode
    JSON_PLAN = "json_plan"  # validated JSON tool plan for models without native tool calling
    DISABLED = "disabled"  # no suitable model: deterministic pipeline only


class ToolExecutionStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    REJECTED = "rejected"  # unknown tool, malformed arguments, permission or budget violation
    DUPLICATE = "duplicate"  # identical call already executed in this run; cached result returned
    TIMEOUT = "timeout"

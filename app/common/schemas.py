from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime


# ---------------------------------------------------------------------------
# Auth schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None


class AuthResponse(BaseModel):
    accessToken: str
    refreshToken: str
    message: str


class RefreshRequest(BaseModel):
    refreshToken: str


class RefreshResponse(BaseModel):
    accessToken: str


# ---------------------------------------------------------------------------
# User profile schemas
# ---------------------------------------------------------------------------


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None


class UserProfileResponse(BaseModel):
    id: int
    username: str
    display_name: str
    email: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Job schemas
# ---------------------------------------------------------------------------


class JobCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="")
    script_content: str = Field(..., min_length=1)
    script_type: str = Field(default="python", pattern="^(python|bash)$")
    cron_expression: str = Field(..., min_length=1, max_length=100)
    is_active: bool = Field(default=True)
    timeout_seconds: int = Field(default=0, ge=0)
    depends_on: List[str] = Field(default_factory=list)
    tag_ids: List[int] = Field(default_factory=list)


class JobUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    script_content: Optional[str] = Field(default=None, min_length=1)
    script_type: Optional[str] = Field(default=None, pattern="^(python|bash)$")
    cron_expression: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=0)
    depends_on: Optional[List[str]] = None
    tag_ids: Optional[List[int]] = None


class TagBrief(BaseModel):
    id: int
    name: str
    color: str

    model_config = ConfigDict(from_attributes=True)


class DependencyBrief(BaseModel):
    id: str
    name: str


class JobResponse(BaseModel):
    id: str
    user_id: int
    name: str
    description: str
    script_content: str
    script_type: str
    cron_expression: str
    is_active: bool
    timeout_seconds: int
    depends_on: List[str]
    tags: List[TagBrief] = []
    dependency_names: List[DependencyBrief] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobListResponse(BaseModel):
    jobs: List[JobResponse]
    total: int


# ---------------------------------------------------------------------------
# Environment variable schemas
# ---------------------------------------------------------------------------


class EnvVarCreateRequest(BaseModel):
    """Create or update a single env var for a job"""

    var_key: str = Field(..., min_length=1, max_length=200)
    var_value: str


class EnvVarBulkRequest(BaseModel):
    """Set multiple env vars at once (replaces all existing for the job)"""

    env_vars: List[EnvVarCreateRequest]


class EnvVarResponse(BaseModel):
    id: int
    job_id: str
    var_key: str
    # Value is returned decrypted in the API response
    var_value: str
    created_at: datetime
    updated_at: datetime


class EnvVarListResponse(BaseModel):
    env_vars: List[EnvVarResponse]
    total: int


# ---------------------------------------------------------------------------
# Execution history schemas
# ---------------------------------------------------------------------------


class ExecutionResponse(BaseModel):
    id: int
    job_id: str
    started_at: datetime
    ended_at: Optional[datetime]
    duration_seconds: Optional[float]
    status: str
    exit_code: Optional[int]
    error_summary: Optional[str]
    log_output: Optional[str]
    script_version_id: Optional[int]
    pid: Optional[int]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExecutionListResponse(BaseModel):
    executions: List[ExecutionResponse]
    total: int


# ---------------------------------------------------------------------------
# Requirements schemas
# ---------------------------------------------------------------------------


class RequirementsUpdateRequest(BaseModel):
    content: str


class RequirementsResponse(BaseModel):
    content: str
    last_install_output: Optional[str] = None


# ---------------------------------------------------------------------------
# Trigger / Cancel / Replay
# ---------------------------------------------------------------------------


class TriggerJobResponse(BaseModel):
    message: str
    execution_id: Optional[int] = None


class CancelJobResponse(BaseModel):
    message: str
    cancelled: bool


class ReplayExecutionRequest(BaseModel):
    """Replay a past execution using the exact script version from that run."""

    execution_id: int


# ---------------------------------------------------------------------------
# Tag schemas
# ---------------------------------------------------------------------------


class TagCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(default="#6366f1", pattern="^#[0-9a-fA-F]{6}$")


class TagUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")


class TagResponse(BaseModel):
    id: int
    name: str
    color: str
    job_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TagListResponse(BaseModel):
    tags: List[TagResponse]
    total: int


# ---------------------------------------------------------------------------
# Notification settings schemas
# ---------------------------------------------------------------------------


class NotificationSettingsUpdate(BaseModel):
    telegram_enabled: Optional[bool] = None
    telegram_chat_id: Optional[str] = None
    email_enabled: Optional[bool] = None
    notify_on: Optional[str] = Field(
        default=None, pattern="^(failure_only|always|never)$"
    )


class NotificationSettingsResponse(BaseModel):
    telegram_enabled: bool
    telegram_chat_id: Optional[str]
    email_enabled: bool
    notify_on: str

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Job template schemas
# ---------------------------------------------------------------------------


class JobTemplateResponse(BaseModel):
    id: int
    name: str
    description: str
    category: str
    script_content: str
    script_type: str
    default_cron: str

    model_config = ConfigDict(from_attributes=True)


class JobTemplateListResponse(BaseModel):
    templates: List[JobTemplateResponse]
    total: int

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Auth schemas (existing)
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    accessToken: str
    refreshToken: str
    message: str


class RefreshRequest(BaseModel):
    refreshToken: str


class RefreshResponse(BaseModel):
    accessToken: str


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


class JobUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    script_content: Optional[str] = Field(default=None, min_length=1)
    script_type: Optional[str] = Field(default=None, pattern="^(python|bash)$")
    cron_expression: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None


class JobResponse(BaseModel):
    id: str
    user_id: int
    name: str
    description: str
    script_content: str
    script_type: str
    cron_expression: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


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
    created_at: datetime

    class Config:
        from_attributes = True


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
# Trigger job manually
# ---------------------------------------------------------------------------


class TriggerJobResponse(BaseModel):
    message: str
    execution_id: Optional[int] = None

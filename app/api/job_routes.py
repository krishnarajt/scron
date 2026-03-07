"""
API routes for job management, environment variables, execution history,
and manual triggering.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from croniter import croniter

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.common.schemas import (
    JobCreateRequest,
    JobUpdateRequest,
    JobResponse,
    JobListResponse,
    EnvVarCreateRequest,
    EnvVarBulkRequest,
    EnvVarResponse,
    EnvVarListResponse,
    ExecutionListResponse,
    TriggerJobResponse,
)
from app.services import job_service
from app.services.scheduler_service import register_job, unregister_job, trigger_job_now
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["Jobs"])


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    request: JobCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new cron job."""
    # Validate cron expression
    if not croniter.is_valid(request.cron_expression):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid cron expression: '{request.cron_expression}'",
        )

    job = job_service.create_job(
        db=db,
        user_id=current_user.id,
        name=request.name,
        description=request.description,
        script_content=request.script_content,
        script_type=request.script_type,
        cron_expression=request.cron_expression,
        is_active=request.is_active,
    )

    # Register with live scheduler if active
    if job.is_active:
        register_job(job.id, job.cron_expression)

    return job


@router.get("", response_model=JobListResponse)
def list_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all jobs for the current user."""
    jobs, total = job_service.list_jobs(db, current_user.id)
    return JobListResponse(jobs=jobs, total=total)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single job by ID."""
    job = job_service.get_job(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/{job_id}", response_model=JobResponse)
def update_job(
    job_id: str,
    request: JobUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a job. Only provided fields are changed."""
    # Validate new cron expression if provided
    if request.cron_expression and not croniter.is_valid(request.cron_expression):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid cron expression: '{request.cron_expression}'",
        )

    update_data = request.model_dump(exclude_unset=True)
    job = job_service.update_job(db, job_id, current_user.id, **update_data)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Update scheduler registration
    if job.is_active:
        register_job(job.id, job.cron_expression)
    else:
        unregister_job(job.id)

    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a job and all its env vars and execution history."""
    # Unregister from scheduler first
    unregister_job(job_id)

    deleted = job_service.delete_job(db, job_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")


# ---------------------------------------------------------------------------
# Manual trigger
# ---------------------------------------------------------------------------


@router.post("/{job_id}/trigger", response_model=TriggerJobResponse)
def trigger_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger a job to run immediately (outside its cron schedule)."""
    job = job_service.get_job(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    trigger_job_now(job_id)
    return TriggerJobResponse(
        message=f"Job '{job.name}' triggered for immediate execution"
    )


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------


@router.get("/{job_id}/env", response_model=EnvVarListResponse)
def list_env_vars(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all environment variables for a job (decrypted)."""
    job = job_service.get_job(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    env_vars = job_service.get_env_vars(db, job_id, current_user.id)
    return EnvVarListResponse(
        env_vars=[
            EnvVarResponse(
                id=ev["id"],
                job_id=ev["job_id"],
                var_key=ev["var_key"],
                var_value=ev["var_value"],
                created_at=ev["created_at"],
                updated_at=ev["updated_at"],
            )
            for ev in env_vars
        ],
        total=len(env_vars),
    )


@router.put("/{job_id}/env", response_model=EnvVarListResponse)
def set_env_vars_bulk(
    job_id: str,
    request: EnvVarBulkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace ALL environment variables for a job."""
    job = job_service.get_job(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    env_dicts = [
        {"var_key": ev.var_key, "var_value": ev.var_value} for ev in request.env_vars
    ]
    job_service.set_env_vars_bulk(db, job_id, current_user.id, env_dicts)

    # Return the updated list
    env_vars = job_service.get_env_vars(db, job_id, current_user.id)
    return EnvVarListResponse(
        env_vars=[
            EnvVarResponse(
                id=ev["id"],
                job_id=ev["job_id"],
                var_key=ev["var_key"],
                var_value=ev["var_value"],
                created_at=ev["created_at"],
                updated_at=ev["updated_at"],
            )
            for ev in env_vars
        ],
        total=len(env_vars),
    )


@router.post(
    "/{job_id}/env", response_model=EnvVarResponse, status_code=status.HTTP_201_CREATED
)
def set_env_var(
    job_id: str,
    request: EnvVarCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or update a single environment variable for a job."""
    job = job_service.get_job(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_service.set_env_var(
        db, job_id, current_user.id, request.var_key, request.var_value
    )

    # Return the var (decrypted)
    env_vars = job_service.get_env_vars(db, job_id, current_user.id)
    for ev in env_vars:
        if ev["var_key"] == request.var_key:
            return EnvVarResponse(
                id=ev["id"],
                job_id=ev["job_id"],
                var_key=ev["var_key"],
                var_value=ev["var_value"],
                created_at=ev["created_at"],
                updated_at=ev["updated_at"],
            )

    raise HTTPException(status_code=500, detail="Failed to retrieve saved env var")


@router.delete("/{job_id}/env/{var_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_env_var(
    job_id: str,
    var_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a single environment variable by key."""
    job = job_service.get_job(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    deleted = job_service.delete_env_var(db, job_id, var_key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Env var '{var_key}' not found")


# ---------------------------------------------------------------------------
# Execution history
# ---------------------------------------------------------------------------


@router.get("/{job_id}/executions", response_model=ExecutionListResponse)
def list_executions(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get execution history for a job (most recent first)."""
    job = job_service.get_job(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    executions, total = job_service.get_executions(
        db, job_id, limit=limit, offset=offset
    )
    return ExecutionListResponse(executions=executions, total=total)

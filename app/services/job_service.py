"""
Business logic for managing jobs, their environment variables,
execution history records, tags, templates, and DAG dependencies.

All DB operations go through this module.  The scheduler and API
routes call these functions — they never touch models directly.
"""

from typing import Optional, List, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.models import (
    Job,
    JobEnvVar,
    JobExecution,
    JobScriptVersion,
    User,
    Tag,
    JobTag,
    NotificationSettings,
    JobTemplate,
)
from app.services.crypto_service import encrypt_value, decrypt_value
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_user_salt(db: Session, user_id: int) -> str:
    """Fetch user's salt from DB. Raises ValueError if user not found."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    return user.salt


def _enrich_job_response(db: Session, job: Job) -> dict:
    """Add tags and dependency names to a job for API response."""
    tags = (
        db.query(Tag)
        .join(JobTag, JobTag.tag_id == Tag.id)
        .filter(JobTag.job_id == job.id)
        .all()
    )
    dep_names = []
    if job.depends_on:
        deps = db.query(Job.id, Job.name).filter(Job.id.in_(job.depends_on)).all()
        dep_names = [{"id": d.id, "name": d.name} for d in deps]

    return {
        "id": job.id,
        "user_id": job.user_id,
        "name": job.name,
        "description": job.description,
        "script_content": job.script_content,
        "script_type": job.script_type,
        "cron_expression": job.cron_expression,
        "is_active": job.is_active,
        "timeout_seconds": job.timeout_seconds,
        "depends_on": job.depends_on or [],
        "tags": [{"id": t.id, "name": t.name, "color": t.color} for t in tags],
        "dependency_names": dep_names,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------


def create_job(
    db: Session,
    user_id: int,
    name: str,
    script_content: str,
    cron_expression: str,
    description: str = "",
    script_type: str = "python",
    is_active: bool = True,
    timeout_seconds: int = 0,
    depends_on: List[str] = None,
    tag_ids: List[int] = None,
) -> dict:
    """Create a new job and return it. Also saves the initial script as version 1."""
    job = Job(
        user_id=user_id,
        name=name,
        description=description,
        script_content=script_content,
        script_type=script_type,
        cron_expression=cron_expression,
        is_active=is_active,
        timeout_seconds=timeout_seconds,
        depends_on=depends_on or [],
    )
    db.add(job)
    db.flush()  # get job.id without committing

    # Save initial script version
    version = JobScriptVersion(
        job_id=job.id,
        version=1,
        script_content=script_content,
        script_type=script_type,
        change_summary="Initial version",
    )
    db.add(version)

    # Associate tags
    if tag_ids:
        for tid in tag_ids:
            db.add(JobTag(job_id=job.id, tag_id=tid))

    db.commit()
    db.refresh(job)
    logger.info(f"Created job '{name}' (id={job.id}) for user {user_id}")
    return _enrich_job_response(db, job)


def get_job(db: Session, job_id: str, user_id: int) -> Optional[Job]:
    """Get a single job by ID, scoped to the user."""
    return db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()


def get_job_response(db: Session, job_id: str, user_id: int) -> Optional[dict]:
    """Get a single job enriched with tags and dependency names."""
    job = get_job(db, job_id, user_id)
    if not job:
        return None
    return _enrich_job_response(db, job)


def list_jobs(db: Session, user_id: int, tag_id: int = None) -> Tuple[List[dict], int]:
    """Return all jobs for a user, optionally filtered by tag."""
    query = db.query(Job).filter(Job.user_id == user_id)
    if tag_id:
        query = query.join(JobTag, JobTag.job_id == Job.id).filter(
            JobTag.tag_id == tag_id
        )
    jobs = query.order_by(Job.created_at.desc()).all()
    enriched = [_enrich_job_response(db, j) for j in jobs]
    return enriched, len(enriched)


def update_job(db: Session, job_id: str, user_id: int, **kwargs) -> Optional[dict]:
    """
    Update fields on an existing job.
    Only keys present in kwargs (and not None) are updated.
    If script_content changes, a new version snapshot is saved.
    Returns the updated job, or None if not found.
    """
    job = get_job(db, job_id, user_id)
    if not job:
        return None

    # Handle tag_ids separately
    tag_ids = kwargs.pop("tag_ids", None)

    # Detect if script content is changing
    new_script = kwargs.get("script_content")
    script_changed = new_script is not None and new_script != job.script_content

    updatable_fields = {
        "name",
        "description",
        "script_content",
        "script_type",
        "cron_expression",
        "is_active",
        "timeout_seconds",
        "depends_on",
    }
    for key, value in kwargs.items():
        if key in updatable_fields and value is not None:
            setattr(job, key, value)

    # Save a new script version if script changed
    if script_changed:
        latest = (
            db.query(func.max(JobScriptVersion.version))
            .filter(JobScriptVersion.job_id == job_id)
            .scalar()
        ) or 0
        version = JobScriptVersion(
            job_id=job_id,
            version=latest + 1,
            script_content=new_script,
            script_type=kwargs.get("script_type") or job.script_type,
            change_summary=None,
        )
        db.add(version)

    # Update tags if provided
    if tag_ids is not None:
        db.query(JobTag).filter(JobTag.job_id == job_id).delete()
        for tid in tag_ids:
            db.add(JobTag(job_id=job_id, tag_id=tid))

    db.commit()
    db.refresh(job)
    logger.info(f"Updated job '{job.name}' (id={job.id})")
    return _enrich_job_response(db, job)


def delete_job(db: Session, job_id: str, user_id: int) -> bool:
    """Delete a job and all its env vars / executions (cascade). Returns True if deleted."""
    job = get_job(db, job_id, user_id)
    if not job:
        return False
    db.delete(job)
    db.commit()
    logger.info(f"Deleted job id={job_id}")
    return True


# ---------------------------------------------------------------------------
# Environment Variable CRUD
# ---------------------------------------------------------------------------


def set_env_var(
    db: Session, job_id: str, user_id: int, var_key: str, var_value: str
) -> JobEnvVar:
    """
    Create or update a single environment variable for a job.
    The value is encrypted before storage.
    """
    user_salt = _get_user_salt(db, user_id)
    encrypted = encrypt_value(var_value, user_salt)

    existing = (
        db.query(JobEnvVar)
        .filter(JobEnvVar.job_id == job_id, JobEnvVar.var_key == var_key)
        .first()
    )
    if existing:
        existing.encrypted_value = encrypted
        db.commit()
        db.refresh(existing)
        return existing
    else:
        env_var = JobEnvVar(
            job_id=job_id,
            var_key=var_key,
            encrypted_value=encrypted,
        )
        db.add(env_var)
        db.commit()
        db.refresh(env_var)
        return env_var


def set_env_vars_bulk(
    db: Session, job_id: str, user_id: int, env_vars: List[dict]
) -> List[JobEnvVar]:
    """Replace ALL environment variables for a job."""
    user_salt = _get_user_salt(db, user_id)
    db.query(JobEnvVar).filter(JobEnvVar.job_id == job_id).delete()

    created = []
    for ev in env_vars:
        encrypted = encrypt_value(ev["var_value"], user_salt)
        env_var = JobEnvVar(
            job_id=job_id,
            var_key=ev["var_key"],
            encrypted_value=encrypted,
        )
        db.add(env_var)
        created.append(env_var)

    db.commit()
    for ev in created:
        db.refresh(ev)
    return created


def get_env_vars(db: Session, job_id: str, user_id: int) -> List[dict]:
    """Return all env vars for a job, decrypted."""
    user_salt = _get_user_salt(db, user_id)
    env_vars = (
        db.query(JobEnvVar)
        .filter(JobEnvVar.job_id == job_id)
        .order_by(JobEnvVar.var_key)
        .all()
    )
    result = []
    for ev in env_vars:
        try:
            decrypted = decrypt_value(ev.encrypted_value, user_salt)
        except Exception:
            logger.error(f"Failed to decrypt env var {ev.var_key} for job {job_id}")
            decrypted = "<decryption_failed>"
        result.append(
            {
                "id": ev.id,
                "job_id": ev.job_id,
                "var_key": ev.var_key,
                "var_value": decrypted,
                "created_at": ev.created_at,
                "updated_at": ev.updated_at,
            }
        )
    return result


def get_env_vars_decrypted_dict(db: Session, job_id: str, user_id: int) -> dict:
    """Return a plain {KEY: VALUE} dict of all env vars for a job."""
    env_list = get_env_vars(db, job_id, user_id)
    return {ev["var_key"]: ev["var_value"] for ev in env_list}


def delete_env_var(db: Session, job_id: str, var_key: str) -> bool:
    """Delete a single env var by key."""
    deleted = (
        db.query(JobEnvVar)
        .filter(JobEnvVar.job_id == job_id, JobEnvVar.var_key == var_key)
        .delete()
    )
    db.commit()
    return deleted > 0


# ---------------------------------------------------------------------------
# Execution History
# ---------------------------------------------------------------------------


def create_execution(
    db: Session, job_id: str, script_version_id: int = None
) -> JobExecution:
    """Create a new execution record in 'running' status."""
    # Auto-detect current script version if not provided
    if script_version_id is None:
        latest = (
            db.query(func.max(JobScriptVersion.version))
            .filter(JobScriptVersion.job_id == job_id)
            .scalar()
        )
        if latest:
            ver = (
                db.query(JobScriptVersion)
                .filter(
                    JobScriptVersion.job_id == job_id,
                    JobScriptVersion.version == latest,
                )
                .first()
            )
            script_version_id = ver.id if ver else None

    execution = JobExecution(
        job_id=job_id,
        started_at=_utcnow(),
        status="running",
        script_version_id=script_version_id,
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)
    return execution


def complete_execution(
    db: Session,
    execution_id: int,
    status: str,
    exit_code: Optional[int] = None,
    error_summary: Optional[str] = None,
    log_output: Optional[str] = None,
) -> JobExecution:
    """Mark an execution as completed (success or failure)."""
    execution = db.query(JobExecution).filter(JobExecution.id == execution_id).first()
    if not execution:
        raise ValueError(f"Execution {execution_id} not found")

    now = _utcnow()
    execution.ended_at = now
    started = execution.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    execution.duration_seconds = (now - started).total_seconds()
    execution.status = status
    execution.exit_code = exit_code
    execution.pid = None  # Clear PID on completion
    if error_summary:
        execution.error_summary = error_summary[:500]
    if log_output:
        execution.log_output = log_output

    db.commit()
    db.refresh(execution)
    return execution


def set_execution_pid(db: Session, execution_id: int, pid: int) -> None:
    """Store the subprocess PID for a running execution (for cancellation)."""
    execution = db.query(JobExecution).filter(JobExecution.id == execution_id).first()
    if execution:
        execution.pid = pid
        db.commit()


def get_executions(
    db: Session,
    job_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[JobExecution], int]:
    """Return execution history for a job, most recent first."""
    query = (
        db.query(JobExecution)
        .filter(JobExecution.job_id == job_id)
        .order_by(JobExecution.started_at.desc())
    )
    total = query.count()
    executions = query.offset(offset).limit(limit).all()
    return executions, total


def get_all_active_jobs(db: Session) -> List[Job]:
    """Return all active jobs across all users. Used on scheduler startup."""
    return db.query(Job).filter(Job.is_active).all()


# ---------------------------------------------------------------------------
# DAG Dependency Check
# ---------------------------------------------------------------------------


def check_dependencies_met(db: Session, job: Job) -> bool:
    """
    Check if all jobs in job.depends_on succeeded in their most recent execution.
    Returns True if all dependencies are met (or if there are no dependencies).
    """
    deps = job.depends_on or []
    if not deps:
        return True

    for dep_id in deps:
        last_exec = (
            db.query(JobExecution)
            .filter(JobExecution.job_id == dep_id)
            .order_by(JobExecution.started_at.desc())
            .first()
        )
        if not last_exec or last_exec.status != "success":
            logger.info(
                f"Dependency not met for job {job.id}: "
                f"dep {dep_id} last status = {last_exec.status if last_exec else 'never_run'}"
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Script Version History
# ---------------------------------------------------------------------------


def get_script_versions(
    db: Session, job_id: str, user_id: int, limit: int = 50
) -> Tuple[List[JobScriptVersion], int]:
    """Return all script versions for a job, newest first."""
    job = get_job(db, job_id, user_id)
    if not job:
        return [], 0
    query = (
        db.query(JobScriptVersion)
        .filter(JobScriptVersion.job_id == job_id)
        .order_by(JobScriptVersion.version.desc())
    )
    total = query.count()
    versions = query.limit(limit).all()
    return versions, total


def get_script_version(
    db: Session, job_id: str, user_id: int, version: int
) -> Optional[JobScriptVersion]:
    """Return a specific script version."""
    job = get_job(db, job_id, user_id)
    if not job:
        return None
    return (
        db.query(JobScriptVersion)
        .filter(JobScriptVersion.job_id == job_id, JobScriptVersion.version == version)
        .first()
    )


def restore_script_version(
    db: Session, job_id: str, user_id: int, version: int
) -> Optional[dict]:
    """Restore a job's script to a previous version."""
    job = get_job(db, job_id, user_id)
    if not job:
        return None
    target = get_script_version(db, job_id, user_id, version)
    if not target:
        return None

    job.script_content = target.script_content
    job.script_type = target.script_type

    latest = (
        db.query(func.max(JobScriptVersion.version))
        .filter(JobScriptVersion.job_id == job_id)
        .scalar()
    ) or 0
    new_version = JobScriptVersion(
        job_id=job_id,
        version=latest + 1,
        script_content=target.script_content,
        script_type=target.script_type,
        change_summary=f"Restored from version {version}",
    )
    db.add(new_version)
    db.commit()
    db.refresh(job)
    logger.info(f"Restored job {job_id} to script version {version}")
    return _enrich_job_response(db, job)


# ---------------------------------------------------------------------------
# Duplicate Job
# ---------------------------------------------------------------------------


def duplicate_job(db: Session, job_id: str, user_id: int) -> Optional[dict]:
    """Duplicate a job — copies name, description, script, cron, and env vars."""
    original = get_job(db, job_id, user_id)
    if not original:
        return None

    new_job = Job(
        user_id=user_id,
        name=f"{original.name} (copy)",
        description=original.description,
        script_content=original.script_content,
        script_type=original.script_type,
        cron_expression=original.cron_expression,
        is_active=False,
        timeout_seconds=original.timeout_seconds,
        depends_on=original.depends_on or [],
    )
    db.add(new_job)
    db.flush()

    version = JobScriptVersion(
        job_id=new_job.id,
        version=1,
        script_content=original.script_content,
        script_type=original.script_type,
        change_summary=f"Duplicated from '{original.name}'",
    )
    db.add(version)

    # Copy env vars
    original_env = db.query(JobEnvVar).filter(JobEnvVar.job_id == job_id).all()
    for ev in original_env:
        db.add(
            JobEnvVar(
                job_id=new_job.id,
                var_key=ev.var_key,
                encrypted_value=ev.encrypted_value,
            )
        )

    # Copy tags
    original_tags = db.query(JobTag).filter(JobTag.job_id == job_id).all()
    for jt in original_tags:
        db.add(JobTag(job_id=new_job.id, tag_id=jt.tag_id))

    db.commit()
    db.refresh(new_job)
    logger.info(
        f"Duplicated job '{original.name}' -> '{new_job.name}' (id={new_job.id})"
    )
    return _enrich_job_response(db, new_job)


# ---------------------------------------------------------------------------
# Next Scheduled Runs (using croniter)
# ---------------------------------------------------------------------------


def get_next_runs(cron_expression: str, count: int = 5) -> List[str]:
    """Compute the next N scheduled run times for a cron expression."""
    from croniter import croniter

    if not croniter.is_valid(cron_expression):
        return []
    base = _utcnow()
    cron = croniter(cron_expression, base)
    return [cron.get_next(datetime).isoformat() for _ in range(count)]


# ---------------------------------------------------------------------------
# Tag CRUD
# ---------------------------------------------------------------------------


def create_tag(db: Session, user_id: int, name: str, color: str = "#6366f1") -> Tag:
    """Create a new tag for a user."""
    tag = Tag(user_id=user_id, name=name, color=color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def list_tags(db: Session, user_id: int) -> List[dict]:
    """Return all tags for a user with job counts."""
    tags = db.query(Tag).filter(Tag.user_id == user_id).order_by(Tag.name).all()
    result = []
    for t in tags:
        count = (
            db.query(func.count(JobTag.id)).filter(JobTag.tag_id == t.id).scalar() or 0
        )
        result.append(
            {
                "id": t.id,
                "name": t.name,
                "color": t.color,
                "job_count": count,
                "created_at": t.created_at,
            }
        )
    return result


def update_tag(db: Session, tag_id: int, user_id: int, **kwargs) -> Optional[Tag]:
    """Update a tag."""
    tag = db.query(Tag).filter(Tag.id == tag_id, Tag.user_id == user_id).first()
    if not tag:
        return None
    for key, value in kwargs.items():
        if value is not None and hasattr(tag, key):
            setattr(tag, key, value)
    db.commit()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, tag_id: int, user_id: int) -> bool:
    """Delete a tag."""
    tag = db.query(Tag).filter(Tag.id == tag_id, Tag.user_id == user_id).first()
    if not tag:
        return False
    db.delete(tag)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Notification Settings
# ---------------------------------------------------------------------------


def get_notification_settings(
    db: Session, user_id: int
) -> Optional[NotificationSettings]:
    """Get notification settings for a user."""
    return (
        db.query(NotificationSettings)
        .filter(NotificationSettings.user_id == user_id)
        .first()
    )


def upsert_notification_settings(
    db: Session, user_id: int, **kwargs
) -> NotificationSettings:
    """Create or update notification settings."""
    settings = get_notification_settings(db, user_id)
    if not settings:
        settings = NotificationSettings(user_id=user_id)
        db.add(settings)

    for key, value in kwargs.items():
        if value is not None and hasattr(settings, key):
            setattr(settings, key, value)

    db.commit()
    db.refresh(settings)
    return settings


# ---------------------------------------------------------------------------
# Job Templates
# ---------------------------------------------------------------------------


def list_templates(db: Session, user_id: int = None) -> List[JobTemplate]:
    """Return all templates: system-wide + user's own."""
    query = db.query(JobTemplate).filter(
        (JobTemplate.user_id == None) | (JobTemplate.user_id == user_id)  # noqa: E711
    )
    return query.order_by(JobTemplate.category, JobTemplate.name).all()

"""
Scheduler service — the heart of sCron.

Responsibilities:
    1. Maintain an APScheduler instance that fires jobs on their cron schedules.
    2. Enforce a concurrency cap (default 3) using a threading.Semaphore.
       Jobs that arrive while all slots are occupied BLOCK until a slot frees up.
    3. Before executing a script, decrypt its env vars and inject them into
       the subprocess environment.
    4. Record every execution (start, end, duration, exit code) in the DB.

Lifecycle:
    - Call ``startup()`` during FastAPI startup (loads active jobs from DB).
    - Call ``shutdown()`` during FastAPI shutdown (graceful stop).
    - Call ``register_job()`` / ``unregister_job()`` when jobs are created,
      updated, or deleted via the API.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

from app.common import constants
from app.db.database import SessionLocal
from app.services import job_service
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_scheduler: Optional[BackgroundScheduler] = None
_concurrency_semaphore: Optional[threading.Semaphore] = None
_scripts_dir: str = constants.JOBS_SCRIPTS_DIR


def _ensure_scripts_dir():
    """Create the scripts directory if it doesn't exist."""
    os.makedirs(_scripts_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------


def startup() -> None:
    """
    Initialise the scheduler and load all active jobs from the database.
    Should be called once at application startup (FastAPI lifespan).
    """
    global _scheduler, _concurrency_semaphore

    _ensure_scripts_dir()

    _concurrency_semaphore = threading.Semaphore(constants.MAX_CONCURRENT_JOBS)

    _scheduler = BackgroundScheduler(
        # Use a generous thread pool — the semaphore is the real limiter.
        # We want enough threads so that waiting jobs can park on the semaphore
        # without starving the scheduler's internal bookkeeping.
        executors={
            "default": {
                "type": "threadpool",
                "max_workers": constants.MAX_CONCURRENT_JOBS + 5,
            }
        },
        job_defaults={
            # If a job fires while the previous instance is still running,
            # allow it to coalesce (skip the missed fire) rather than stacking.
            "coalesce": True,
            # Allow up to N waiting instances per job (covers semaphore waits).
            "max_instances": 3,
            "misfire_grace_time": 60,  # seconds
        },
    )

    # Log missed jobs
    _scheduler.add_listener(_on_scheduler_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    # Load all active jobs from DB and register them
    db = SessionLocal()
    try:
        active_jobs = job_service.get_all_active_jobs(db)
        for job in active_jobs:
            _add_job_to_scheduler(job.id, job.cron_expression)
        logger.info(
            f"Scheduler started with {len(active_jobs)} active job(s), "
            f"max concurrency = {constants.MAX_CONCURRENT_JOBS}"
        )
    finally:
        db.close()

    _scheduler.start()


def shutdown() -> None:
    """Gracefully stop the scheduler. Call during FastAPI shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler shut down gracefully")
    _scheduler = None


def _on_scheduler_event(event):
    """Listener for scheduler-level events (errors, misfires)."""
    if hasattr(event, "exception") and event.exception:
        logger.error(
            f"Scheduler job {event.job_id} raised an exception: {event.exception}"
        )
    elif hasattr(event, "code"):
        logger.warning(f"Scheduler event for job {event.job_id}: code={event.code}")


# ---------------------------------------------------------------------------
# Job registration (called by API layer)
# ---------------------------------------------------------------------------


def register_job(job_id: str, cron_expression: str) -> None:
    """
    Add or replace a job in the live scheduler.
    Call this after creating or updating a job via the API.
    """
    if not _scheduler:
        logger.warning("Scheduler not running — cannot register job")
        return

    # Remove existing if present (idempotent)
    _remove_job_from_scheduler(job_id)
    _add_job_to_scheduler(job_id, cron_expression)
    logger.info(f"Registered job {job_id} with cron '{cron_expression}'")


def unregister_job(job_id: str) -> None:
    """
    Remove a job from the live scheduler.
    Call this when a job is deleted or deactivated via the API.
    """
    _remove_job_from_scheduler(job_id)
    logger.info(f"Unregistered job {job_id}")


def _add_job_to_scheduler(job_id: str, cron_expression: str) -> None:
    """Internal: parse cron expression and add to APScheduler."""
    if not _scheduler:
        return
    try:
        trigger = _parse_cron(cron_expression)
        _scheduler.add_job(
            func=_execute_job,
            trigger=trigger,
            args=[job_id],
            id=job_id,
            name=f"scron-{job_id}",
            replace_existing=True,
        )
    except Exception as e:
        logger.error(f"Failed to add job {job_id} to scheduler: {e}")


def _remove_job_from_scheduler(job_id: str) -> None:
    """Internal: safely remove a job if it exists."""
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass  # job wasn't in the scheduler — that's fine


def _parse_cron(expression: str) -> CronTrigger:
    """
    Parse a standard 5-field cron expression into an APScheduler CronTrigger.
    Format: minute hour day_of_month month day_of_week
    Example: "*/5 * * * *"  (every 5 minutes)
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron expression must have exactly 5 fields (minute hour dom month dow), "
            f"got {len(parts)}: '{expression}'"
        )

    return CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )


# ---------------------------------------------------------------------------
# Trigger a job manually (outside of cron schedule)
# ---------------------------------------------------------------------------


def trigger_job_now(job_id: str) -> Optional[int]:
    """
    Run a job immediately in a background thread.
    Returns the execution_id, or None if the scheduler isn't running.
    """
    if not _scheduler:
        logger.warning("Scheduler not running — cannot trigger job")
        return None

    # Run in a daemon thread so we don't block the API request
    t = threading.Thread(target=_execute_job, args=(job_id,), daemon=True)
    t.start()
    return None  # execution_id isn't known until the thread starts


# ---------------------------------------------------------------------------
# Job execution — the core orchestrator function
# ---------------------------------------------------------------------------


def _execute_job(job_id: str) -> None:
    """
    The function that APScheduler calls on each cron fire.

    Steps:
        1. Acquire the concurrency semaphore (blocks if all slots full).
        2. Open a fresh DB session and load the job.
        3. Create a JobExecution record (status=running).
        4. Materialise the script to a temp file on disk.
        5. Decrypt env vars and build a subprocess environment.
        6. Execute the script via subprocess.
        7. Record result (success/failure, duration, exit code).
        8. Release the semaphore.
    """
    import subprocess

    # Block until a concurrency slot is available
    logger.debug(f"Job {job_id}: waiting for concurrency slot …")
    _concurrency_semaphore.acquire()
    logger.info(f"Job {job_id}: acquired concurrency slot, starting execution")

    db = SessionLocal()
    execution = None
    try:
        # Load job from DB
        job = db.query(job_service.Job).filter(job_service.Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found in DB — skipping execution")
            return
        if not job.is_active:
            logger.info(f"Job {job_id} is inactive — skipping execution")
            return

        # Create execution record
        execution = job_service.create_execution(db, job_id)
        logger.info(f"Job {job_id}: execution {execution.id} started")

        # Materialise script to disk
        script_path = _materialise_script(job)

        # Decrypt environment variables
        env_dict = job_service.get_env_vars_decrypted_dict(db, job_id, job.user_id)

        # Build subprocess environment:
        # Start with current process env, then overlay job-specific vars
        proc_env = os.environ.copy()
        proc_env.update(env_dict)

        # Determine the command based on script type
        if job.script_type == "bash":
            cmd = ["bash", script_path]
        else:
            cmd = ["python3", script_path]

        # Execute the script
        result = subprocess.run(
            cmd,
            env=proc_env,
            capture_output=True,
            text=True,
            timeout=3600,  # 1-hour hard timeout per job
            cwd=_scripts_dir,
        )

        # Record success or failure
        if result.returncode == 0:
            job_service.complete_execution(
                db, execution.id, status="success", exit_code=0
            )
            logger.info(f"Job {job_id}: execution {execution.id} succeeded")
        else:
            error_summary = (result.stderr or "")[-constants.MAX_ERROR_SUMMARY_LENGTH :]
            job_service.complete_execution(
                db,
                execution.id,
                status="failure",
                exit_code=result.returncode,
                error_summary=error_summary,
            )
            logger.warning(
                f"Job {job_id}: execution {execution.id} failed "
                f"(exit code {result.returncode})"
            )

    except subprocess.TimeoutExpired:
        if execution:
            job_service.complete_execution(
                db,
                execution.id,
                status="failure",
                exit_code=-1,
                error_summary="Job timed out after 3600 seconds",
            )
        logger.error(f"Job {job_id}: timed out")

    except Exception as e:
        logger.error(f"Job {job_id}: unexpected error during execution: {e}")
        if execution:
            try:
                job_service.complete_execution(
                    db,
                    execution.id,
                    status="failure",
                    exit_code=-1,
                    error_summary=str(e)[: constants.MAX_ERROR_SUMMARY_LENGTH],
                )
            except Exception:
                logger.error(f"Job {job_id}: failed to record execution failure")

    finally:
        db.close()
        _concurrency_semaphore.release()
        logger.debug(f"Job {job_id}: released concurrency slot")


def _materialise_script(job) -> str:
    """
    Write the job's script content to a file in the scripts directory.
    Returns the absolute path to the script file.

    Files are named {job_id}.{ext} so they're stable across runs
    and easy to inspect on-disk.
    """
    ext = "sh" if job.script_type == "bash" else "py"
    script_path = os.path.join(_scripts_dir, f"{job.id}.{ext}")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(job.script_content)

    # Make bash scripts executable
    if job.script_type == "bash":
        os.chmod(script_path, 0o755)

    return script_path

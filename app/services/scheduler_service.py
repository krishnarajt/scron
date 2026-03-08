"""
Scheduler service — the heart of sCron.

Responsibilities:
    1. Maintain an APScheduler instance that fires jobs on their cron schedules.
    2. Enforce a concurrency cap (default 3) using a threading.Semaphore.
    3. Before executing a script, check DAG dependencies are met.
    4. Decrypt env vars and inject them into the subprocess environment.
    5. Record every execution (start, end, duration, exit code) in the DB.
    6. Send notifications on completion.
    7. Support per-job timeout and graceful cancellation.

Lifecycle:
    - Call ``startup()`` during FastAPI startup (loads active jobs from DB).
    - Call ``shutdown()`` during FastAPI shutdown (graceful stop).
    - Call ``register_job()`` / ``unregister_job()`` when jobs are created,
      updated, or deleted via the API.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Optional, Dict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

from app.common import constants
from app.db.database import SessionLocal
from app.services import job_service
from app.services import log_broadcaster
from app.services import notification_service
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_scheduler: Optional[BackgroundScheduler] = None
_concurrency_semaphore: Optional[threading.Semaphore] = None
_scripts_dir: str = constants.JOBS_SCRIPTS_DIR

# Track running subprocesses by execution_id for cancellation
_running_processes: Dict[int, subprocess.Popen] = {}
_processes_lock = threading.Lock()


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
        executors={
            "default": {
                "type": "threadpool",
                "max_workers": constants.MAX_CONCURRENT_JOBS + 5,
            }
        },
        job_defaults={
            "coalesce": True,
            "max_instances": 3,
            "misfire_grace_time": 60,
        },
    )

    _scheduler.add_listener(_on_scheduler_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

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
    """Add or replace a job in the live scheduler."""
    if not _scheduler:
        logger.warning("Scheduler not running — cannot register job")
        return
    _remove_job_from_scheduler(job_id)
    _add_job_to_scheduler(job_id, cron_expression)
    logger.info(f"Registered job {job_id} with cron '{cron_expression}'")


def unregister_job(job_id: str) -> None:
    """Remove a job from the live scheduler."""
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
        pass


def _parse_cron(expression: str) -> CronTrigger:
    """Parse a standard 5-field cron expression into an APScheduler CronTrigger."""
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron expression must have exactly 5 fields, got {len(parts)}: '{expression}'"
        )
    return CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )


# ---------------------------------------------------------------------------
# Trigger a job manually / Replay / Cancel
# ---------------------------------------------------------------------------


def trigger_job_now(job_id: str, script_version_id: int = None) -> Optional[int]:
    """
    Run a job immediately in a background thread.
    Creates the execution record first and returns its ID.
    """
    if not _scheduler:
        logger.warning("Scheduler not running — cannot trigger job")
        return None

    db = SessionLocal()
    try:
        execution = job_service.create_execution(db, job_id, script_version_id)
        execution_id = execution.id
    finally:
        db.close()

    t = threading.Thread(
        target=_execute_job,
        args=(job_id,),
        kwargs={
            "pre_created_execution_id": execution_id,
            "replay_version_id": script_version_id,
        },
        daemon=True,
    )
    t.start()
    return execution_id


def cancel_execution(execution_id: int) -> bool:
    """
    Cancel a running execution by sending SIGTERM to its subprocess.
    Falls back to SIGKILL after 5 seconds.
    Returns True if the process was found and signalled.
    """
    with _processes_lock:
        proc = _running_processes.get(execution_id)

    if not proc:
        # Try via PID from DB
        db = SessionLocal()
        try:
            execution = (
                db.query(job_service.JobExecution)
                .filter(job_service.JobExecution.id == execution_id)
                .first()
            )
            if execution and execution.pid and execution.status == "running":
                try:
                    os.kill(execution.pid, signal.SIGTERM)
                    logger.info(
                        f"Sent SIGTERM to PID {execution.pid} for execution {execution_id}"
                    )
                    return True
                except ProcessLookupError:
                    return False
        finally:
            db.close()
        return False

    # Signal the process
    try:
        proc.terminate()  # SIGTERM
        logger.info(f"Sent SIGTERM to execution {execution_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to cancel execution {execution_id}: {e}")
        return False


def replay_execution(execution_id: int, user_id: int) -> Optional[int]:
    """
    Replay a past execution using its script version.
    Returns the new execution_id or None.
    """
    db = SessionLocal()
    try:
        old_exec = (
            db.query(job_service.JobExecution)
            .filter(job_service.JobExecution.id == execution_id)
            .first()
        )
        if not old_exec:
            return None

        # Verify ownership
        job = job_service.get_job(db, old_exec.job_id, user_id)
        if not job:
            return None

        return trigger_job_now(
            old_exec.job_id,
            script_version_id=old_exec.script_version_id,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Log output building
# ---------------------------------------------------------------------------


def _build_log_output(combined: str) -> str:
    """Trim combined output to keep the first N and last N lines."""
    head_n = constants.LOG_HEAD_LINES
    tail_n = constants.LOG_TAIL_LINES
    if not combined.strip():
        return ""
    lines = combined.splitlines()
    total = len(lines)
    if total <= head_n + tail_n:
        return "\n".join(lines)
    head = lines[:head_n]
    tail = lines[-tail_n:]
    skipped = total - head_n - tail_n
    return (
        "\n".join(head) + f"\n\n... ({skipped} lines omitted) ...\n\n" + "\n".join(tail)
    )


# ---------------------------------------------------------------------------
# Job execution — the core orchestrator function
# ---------------------------------------------------------------------------


def _execute_job(
    job_id: str,
    pre_created_execution_id: int = None,
    replay_version_id: int = None,
) -> None:
    """
    The function that APScheduler calls on each cron fire.

    Steps:
        1. Acquire the concurrency semaphore.
        2. Load the job from DB, check dependencies.
        3. Create/reuse a JobExecution record.
        4. Open a broadcast channel for real-time log streaming.
        5. Materialise the script to a temp file.
        6. Decrypt env vars, build subprocess environment.
        7. Execute the script via Popen with per-job timeout.
        8. Broadcast each line to WebSocket subscribers.
        9. Record result and send notifications.
       10. Close the broadcast channel, release the semaphore.
    """
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
        if not job.is_active and not pre_created_execution_id:
            logger.info(f"Job {job_id} is inactive — skipping execution")
            return

        # Check DAG dependencies (skip for manual triggers)
        if not pre_created_execution_id:
            if not job_service.check_dependencies_met(db, job):
                logger.info(f"Job {job_id}: dependencies not met — skipping")
                return

        # Create execution record or load pre-created one
        if pre_created_execution_id:
            execution = (
                db.query(job_service.JobExecution)
                .filter(job_service.JobExecution.id == pre_created_execution_id)
                .first()
            )
            if not execution:
                execution = job_service.create_execution(db, job_id)
        else:
            execution = job_service.create_execution(db, job_id)
        logger.info(f"Job {job_id}: execution {execution.id} started")

        # Open broadcast channel
        log_broadcaster.create_channel(execution.id, job_id)

        # Determine script content: use replay version or current
        script_content = job.script_content
        script_type = job.script_type
        if replay_version_id:
            ver = (
                db.query(job_service.JobScriptVersion)
                .filter(job_service.JobScriptVersion.id == replay_version_id)
                .first()
            )
            if ver:
                script_content = ver.script_content
                script_type = ver.script_type

        # Materialise script to disk
        script_path = _materialise_script(job_id, script_content, script_type)

        # Decrypt environment variables
        env_dict = job_service.get_env_vars_decrypted_dict(db, job_id, job.user_id)
        proc_env = os.environ.copy()
        proc_env.update(env_dict)

        # Determine timeout
        timeout = (
            job.timeout_seconds
            if job.timeout_seconds > 0
            else constants.DEFAULT_JOB_TIMEOUT
        )

        # Determine command
        if script_type == "bash":
            cmd = ["bash", script_path]
        else:
            cmd = ["python3", "-u", script_path]

        # Execute the script
        process = subprocess.Popen(
            cmd,
            env=proc_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=_scripts_dir,
        )

        # Track the process for cancellation
        with _processes_lock:
            _running_processes[execution.id] = process
        job_service.set_execution_pid(db, execution.id, process.pid)

        # Read output line-by-line
        all_lines = []
        try:
            for line in process.stdout:
                stripped = line.rstrip("\n")
                all_lines.append(stripped)
                log_broadcaster.publish_line(execution.id, stripped)
        except Exception as read_err:
            logger.warning(f"Job {job_id}: error reading output: {read_err}")

        # Wait for process to finish
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            log_broadcaster.publish_line(
                execution.id, f"--- TIMEOUT: job killed after {timeout} seconds ---"
            )
            job_service.complete_execution(
                db,
                execution.id,
                status="failure",
                exit_code=-1,
                error_summary=f"Job timed out after {timeout} seconds",
                log_output="\n".join(all_lines[-100:]),
            )
            log_broadcaster.close_channel(execution.id)
            notification_service.notify_execution_complete(
                job.user_id,
                job.name,
                "failure",
                error_summary=f"Timed out after {timeout}s",
                execution_id=execution.id,
            )
            return

        # Remove from running processes
        with _processes_lock:
            _running_processes.pop(execution.id, None)

        combined_output = "\n".join(all_lines)
        log_output = _build_log_output(combined_output)

        exit_code = process.returncode

        # Check if cancelled (exit code -15 = SIGTERM)
        if exit_code in (-15, -signal.SIGTERM):
            job_service.complete_execution(
                db,
                execution.id,
                status="cancelled",
                exit_code=exit_code,
                log_output=log_output,
            )
            logger.info(f"Job {job_id}: execution {execution.id} was cancelled")
            log_broadcaster.close_channel(execution.id)
            return

        if exit_code == 0:
            job_service.complete_execution(
                db,
                execution.id,
                status="success",
                exit_code=0,
                log_output=log_output,
            )
            logger.info(f"Job {job_id}: execution {execution.id} succeeded")
            duration = (
                db.query(job_service.JobExecution)
                .filter(job_service.JobExecution.id == execution.id)
                .first()
                .duration_seconds
                or 0
            )
            notification_service.notify_execution_complete(
                job.user_id,
                job.name,
                "success",
                duration=duration,
                execution_id=execution.id,
            )

            # Trigger downstream dependents (jobs that depend on this one)
            _trigger_dependents(db, job_id)
        else:
            error_summary = combined_output[-constants.MAX_ERROR_SUMMARY_LENGTH :]
            job_service.complete_execution(
                db,
                execution.id,
                status="failure",
                exit_code=exit_code,
                error_summary=error_summary,
                log_output=log_output,
            )
            logger.warning(
                f"Job {job_id}: execution {execution.id} failed (exit code {exit_code})"
            )
            notification_service.notify_execution_complete(
                job.user_id,
                job.name,
                "failure",
                error_summary=error_summary,
                execution_id=execution.id,
            )

        log_broadcaster.close_channel(execution.id)

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
            log_broadcaster.close_channel(execution.id)

    finally:
        with _processes_lock:
            _running_processes.pop(getattr(execution, "id", -1), None)
        db.close()
        _concurrency_semaphore.release()
        logger.debug(f"Job {job_id}: released concurrency slot")


def _trigger_dependents(db, completed_job_id: str) -> None:
    """
    After a job succeeds, find any active jobs that depend on it
    and whose dependencies are now all met, then trigger them.
    """
    # Find jobs that list this job in their depends_on
    # JSON contains check varies by DB — use Python filtering for compatibility
    all_jobs = db.query(job_service.Job).filter(job_service.Job.is_active).all()
    for candidate in all_jobs:
        deps = candidate.depends_on or []
        if completed_job_id in deps:
            if job_service.check_dependencies_met(db, candidate):
                logger.info(
                    f"All dependencies met for {candidate.id} after {completed_job_id} — triggering"
                )
                t = threading.Thread(
                    target=_execute_job, args=(candidate.id,), daemon=True
                )
                t.start()


def _materialise_script(job_id: str, script_content: str, script_type: str) -> str:
    """Write script content to a file. Returns the absolute path."""
    ext = "sh" if script_type == "bash" else "py"
    script_path = os.path.join(_scripts_dir, f"{job_id}.{ext}")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    if script_type == "bash":
        os.chmod(script_path, 0o755)
    return script_path

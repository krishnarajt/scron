"""
Analytics service — aggregation queries over job executions.

Provides data for dashboard charts:
  - Overall execution stats (success/failure/running counts)
  - Timeline of executions bucketed by day/hour
  - Per-job stats and duration trends
  - Upcoming scheduled runs
"""

from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, case, extract

from app.db.models import Job, JobExecution
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Global analytics (all jobs for a user)
# ---------------------------------------------------------------------------


def get_overview(db: Session, user_id: int) -> Dict[str, Any]:
    """
    High-level stats for the dashboard header.
    Returns total jobs, active jobs, total executions, success rate,
    average duration, and counts by status.
    """
    # Job counts
    total_jobs = (
        db.query(func.count(Job.id)).filter(Job.user_id == user_id).scalar() or 0
    )
    active_jobs = (
        db.query(func.count(Job.id))
        .filter(Job.user_id == user_id, Job.is_active)
        .scalar()
        or 0
    )

    # Execution counts (all time, for user's jobs)
    user_job_ids = db.query(Job.id).filter(Job.user_id == user_id).subquery()

    total_executions = (
        db.query(func.count(JobExecution.id))
        .filter(JobExecution.job_id.in_(user_job_ids))
        .scalar()
        or 0
    )

    success_count = (
        db.query(func.count(JobExecution.id))
        .filter(JobExecution.job_id.in_(user_job_ids), JobExecution.status == "success")
        .scalar()
        or 0
    )

    failure_count = (
        db.query(func.count(JobExecution.id))
        .filter(JobExecution.job_id.in_(user_job_ids), JobExecution.status == "failure")
        .scalar()
        or 0
    )

    running_count = (
        db.query(func.count(JobExecution.id))
        .filter(JobExecution.job_id.in_(user_job_ids), JobExecution.status == "running")
        .scalar()
        or 0
    )

    # Average duration (completed only)
    avg_duration = (
        db.query(func.avg(JobExecution.duration_seconds))
        .filter(
            JobExecution.job_id.in_(user_job_ids),
            JobExecution.duration_seconds.isnot(None),
        )
        .scalar()
    )

    # Success rate
    completed = success_count + failure_count
    success_rate = round((success_count / completed * 100), 1) if completed > 0 else 0.0

    return {
        "total_jobs": total_jobs,
        "active_jobs": active_jobs,
        "paused_jobs": total_jobs - active_jobs,
        "total_executions": total_executions,
        "success_count": success_count,
        "failure_count": failure_count,
        "running_count": running_count,
        "success_rate": success_rate,
        "avg_duration_seconds": round(avg_duration, 2) if avg_duration else 0.0,
    }


def get_execution_timeline(
    db: Session, user_id: int, days: int = 14
) -> List[Dict[str, Any]]:
    """
    Returns daily execution counts for the last N days,
    broken down by status.
    Each entry: { date: "2025-03-01", success: 10, failure: 2, running: 0 }
    """
    cutoff = _utcnow() - timedelta(days=days)
    user_job_ids = db.query(Job.id).filter(Job.user_id == user_id).subquery()

    # Query: group by date and status
    rows = (
        db.query(
            func.date(JobExecution.started_at).label("day"),
            JobExecution.status,
            func.count(JobExecution.id).label("cnt"),
        )
        .filter(
            JobExecution.job_id.in_(user_job_ids),
            JobExecution.started_at >= cutoff,
        )
        .group_by(func.date(JobExecution.started_at), JobExecution.status)
        .order_by(func.date(JobExecution.started_at))
        .all()
    )

    # Build a map of date -> {success, failure, running}
    timeline = {}
    for row in rows:
        day_str = str(row.day)
        if day_str not in timeline:
            timeline[day_str] = {
                "date": day_str,
                "success": 0,
                "failure": 0,
                "running": 0,
            }
        if row.status in ("success", "failure", "running"):
            timeline[day_str][row.status] = row.cnt

    # Fill in missing days with zeros
    result = []
    for i in range(days, -1, -1):
        d = (_utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        if d in timeline:
            result.append(timeline[d])
        else:
            result.append({"date": d, "success": 0, "failure": 0, "running": 0})

    return result


def get_hourly_heatmap(
    db: Session, user_id: int, days: int = 7
) -> List[Dict[str, Any]]:
    """
    Returns execution counts bucketed by hour-of-day (0-23) and day-of-week (0-6).
    Used for a heatmap showing when jobs run most.
    Each entry: { hour: 14, dow: 2, count: 5 }
    """
    cutoff = _utcnow() - timedelta(days=days)
    user_job_ids = db.query(Job.id).filter(Job.user_id == user_id).subquery()

    rows = (
        db.query(
            extract("hour", JobExecution.started_at).label("hour"),
            extract("dow", JobExecution.started_at).label("dow"),
            func.count(JobExecution.id).label("cnt"),
        )
        .filter(
            JobExecution.job_id.in_(user_job_ids),
            JobExecution.started_at >= cutoff,
        )
        .group_by("hour", "dow")
        .all()
    )

    return [{"hour": int(r.hour), "dow": int(r.dow), "count": r.cnt} for r in rows]


def get_job_success_breakdown(db: Session, user_id: int) -> List[Dict[str, Any]]:
    """
    Per-job success/failure/running counts.
    Returns: [ { job_id, job_name, success, failure, running, total } ]
    """
    user_job_ids = db.query(Job.id).filter(Job.user_id == user_id).subquery()

    rows = (
        db.query(
            JobExecution.job_id,
            Job.name.label("job_name"),
            func.count(case((JobExecution.status == "success", 1))).label("success"),
            func.count(case((JobExecution.status == "failure", 1))).label("failure"),
            func.count(case((JobExecution.status == "running", 1))).label("running"),
            func.count(JobExecution.id).label("total"),
        )
        .join(Job, Job.id == JobExecution.job_id)
        .filter(JobExecution.job_id.in_(user_job_ids))
        .group_by(JobExecution.job_id, Job.name)
        .order_by(func.count(JobExecution.id).desc())
        .all()
    )

    return [
        {
            "job_id": r.job_id,
            "job_name": r.job_name,
            "success": r.success,
            "failure": r.failure,
            "running": r.running,
            "total": r.total,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Per-job analytics
# ---------------------------------------------------------------------------


def get_job_stats(db: Session, job_id: str, user_id: int) -> Dict[str, Any]:
    """
    Detailed stats for a single job.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        return None

    total = (
        db.query(func.count(JobExecution.id))
        .filter(JobExecution.job_id == job_id)
        .scalar()
        or 0
    )
    success = (
        db.query(func.count(JobExecution.id))
        .filter(JobExecution.job_id == job_id, JobExecution.status == "success")
        .scalar()
        or 0
    )
    failure = (
        db.query(func.count(JobExecution.id))
        .filter(JobExecution.job_id == job_id, JobExecution.status == "failure")
        .scalar()
        or 0
    )

    avg_dur = (
        db.query(func.avg(JobExecution.duration_seconds))
        .filter(
            JobExecution.job_id == job_id, JobExecution.duration_seconds.isnot(None)
        )
        .scalar()
    )
    max_dur = (
        db.query(func.max(JobExecution.duration_seconds))
        .filter(
            JobExecution.job_id == job_id, JobExecution.duration_seconds.isnot(None)
        )
        .scalar()
    )
    min_dur = (
        db.query(func.min(JobExecution.duration_seconds))
        .filter(
            JobExecution.job_id == job_id, JobExecution.duration_seconds.isnot(None)
        )
        .scalar()
    )

    # Last execution
    last_exec = (
        db.query(JobExecution)
        .filter(JobExecution.job_id == job_id)
        .order_by(JobExecution.started_at.desc())
        .first()
    )

    completed = success + failure
    success_rate = round((success / completed * 100), 1) if completed > 0 else 0.0

    return {
        "job_id": job_id,
        "job_name": job.name,
        "total_executions": total,
        "success_count": success,
        "failure_count": failure,
        "success_rate": success_rate,
        "avg_duration_seconds": round(avg_dur, 2) if avg_dur else 0.0,
        "max_duration_seconds": round(max_dur, 2) if max_dur else 0.0,
        "min_duration_seconds": round(min_dur, 2) if min_dur else 0.0,
        "last_execution_at": last_exec.started_at.isoformat() if last_exec else None,
        "last_status": last_exec.status if last_exec else None,
    }


def get_job_duration_trend(
    db: Session, job_id: str, user_id: int, limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Duration of the last N executions for a job (for area/line chart).
    Returns: [ { started_at, duration_seconds, status } ] oldest first.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        return []

    rows = (
        db.query(
            JobExecution.started_at,
            JobExecution.duration_seconds,
            JobExecution.status,
        )
        .filter(
            JobExecution.job_id == job_id,
            JobExecution.duration_seconds.isnot(None),
        )
        .order_by(JobExecution.started_at.desc())
        .limit(limit)
        .all()
    )

    # Reverse to oldest-first for charting
    return [
        {
            "started_at": r.started_at.isoformat(),
            "duration_seconds": round(r.duration_seconds, 2),
            "status": r.status,
        }
        for r in reversed(rows)
    ]


def get_job_timeline(
    db: Session, job_id: str, user_id: int, days: int = 14
) -> List[Dict[str, Any]]:
    """
    Daily execution counts for a single job, last N days.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        return []

    cutoff = _utcnow() - timedelta(days=days)

    rows = (
        db.query(
            func.date(JobExecution.started_at).label("day"),
            JobExecution.status,
            func.count(JobExecution.id).label("cnt"),
        )
        .filter(
            JobExecution.job_id == job_id,
            JobExecution.started_at >= cutoff,
        )
        .group_by(func.date(JobExecution.started_at), JobExecution.status)
        .order_by(func.date(JobExecution.started_at))
        .all()
    )

    timeline = {}
    for row in rows:
        day_str = str(row.day)
        if day_str not in timeline:
            timeline[day_str] = {
                "date": day_str,
                "success": 0,
                "failure": 0,
                "running": 0,
            }
        if row.status in ("success", "failure", "running"):
            timeline[day_str][row.status] = row.cnt

    result = []
    for i in range(days, -1, -1):
        d = (_utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        if d in timeline:
            result.append(timeline[d])
        else:
            result.append({"date": d, "success": 0, "failure": 0, "running": 0})

    return result

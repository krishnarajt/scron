"""
API routes for analytics — dashboard charts and per-job stats.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.services import analytics_service
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


# ---------------------------------------------------------------------------
# Global dashboard analytics
# ---------------------------------------------------------------------------


@router.get("/overview")
def get_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """High-level stats: total jobs, executions, success rate, avg duration."""
    return analytics_service.get_overview(db, current_user.id)


@router.get("/timeline")
def get_execution_timeline(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Daily execution counts for the last N days, by status."""
    return analytics_service.get_execution_timeline(db, current_user.id, days)


@router.get("/heatmap")
def get_hourly_heatmap(
    days: int = Query(default=7, ge=1, le=30),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execution counts by hour-of-day and day-of-week."""
    return analytics_service.get_hourly_heatmap(db, current_user.id, days)


@router.get("/jobs/breakdown")
def get_job_success_breakdown(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-job success/failure/running counts."""
    return analytics_service.get_job_success_breakdown(db, current_user.id)


# ---------------------------------------------------------------------------
# Per-job analytics
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/stats")
def get_job_stats(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Detailed stats for a single job."""
    result = analytics_service.get_job_stats(db, job_id, current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.get("/jobs/{job_id}/duration")
def get_job_duration_trend(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Duration trend for the last N executions of a job."""
    return analytics_service.get_job_duration_trend(db, job_id, current_user.id, limit)


@router.get("/jobs/{job_id}/timeline")
def get_job_timeline(
    job_id: str,
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Daily execution counts for a single job."""
    return analytics_service.get_job_timeline(db, job_id, current_user.id, days)

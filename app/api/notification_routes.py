"""
API routes for managing notification preferences (Telegram / Email).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.common.schemas import NotificationSettingsUpdate, NotificationSettingsResponse
from app.services import job_service
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationSettingsResponse)
def get_notification_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get current notification settings."""
    settings = job_service.get_notification_settings(db, current_user.id)
    if not settings:
        # Return defaults
        return NotificationSettingsResponse(
            telegram_enabled=False,
            telegram_chat_id=None,
            email_enabled=False,
            notify_on="failure_only",
        )
    return settings


@router.put("", response_model=NotificationSettingsResponse)
def update_notification_settings(
    request: NotificationSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update notification settings."""
    # Validate email is set if email_enabled
    if request.email_enabled and not current_user.email:
        raise HTTPException(
            status_code=400,
            detail="Set your email address in profile before enabling email notifications",
        )

    update_data = request.model_dump(exclude_unset=True)
    settings = job_service.upsert_notification_settings(
        db, current_user.id, **update_data
    )
    return settings

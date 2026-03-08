"""
API routes for user profile management.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.common.schemas import UserProfileUpdate, UserProfileResponse
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("", response_model=UserProfileResponse)
def get_profile(
    current_user: User = Depends(get_current_user),
):
    """Get the current user's profile."""
    return current_user


@router.patch("", response_model=UserProfileResponse)
def update_profile(
    request: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update profile fields (display_name, email)."""
    if request.display_name is not None:
        current_user.display_name = request.display_name
    if request.email is not None:
        current_user.email = request.email
    db.commit()
    db.refresh(current_user)
    return current_user

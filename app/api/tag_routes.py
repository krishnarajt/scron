"""
API routes for managing user tags.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.common.schemas import (
    TagCreateRequest,
    TagUpdateRequest,
    TagResponse,
    TagListResponse,
)
from app.services import job_service
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/tags", tags=["Tags"])


@router.get("", response_model=TagListResponse)
def list_tags(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all tags for the current user, with job counts."""
    tags = job_service.list_tags(db, current_user.id)
    return TagListResponse(tags=tags, total=len(tags))


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
def create_tag(
    request: TagCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new tag."""
    tag = job_service.create_tag(db, current_user.id, request.name, request.color)
    return TagResponse(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        job_count=0,
        created_at=tag.created_at,
    )


@router.patch("/{tag_id}", response_model=TagResponse)
def update_tag(
    tag_id: int,
    request: TagUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a tag's name or color."""
    update_data = request.model_dump(exclude_unset=True)
    tag = job_service.update_tag(db, tag_id, current_user.id, **update_data)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    # Get job count
    from sqlalchemy import func
    from app.db.models import JobTag

    count = (
        db.query(func.count(JobTag.id)).filter(JobTag.tag_id == tag.id).scalar() or 0
    )
    return TagResponse(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        job_count=count,
        created_at=tag.created_at,
    )


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(
    tag_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a tag. Removes it from all associated jobs."""
    deleted = job_service.delete_tag(db, tag_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tag not found")

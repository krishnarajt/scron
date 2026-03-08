"""
API routes for job templates — pre-built scripts for common tasks.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.common.schemas import JobTemplateListResponse
from app.services import job_service
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("", response_model=JobTemplateListResponse)
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all available job templates (system + user's own)."""
    templates = job_service.list_templates(db, current_user.id)
    return JobTemplateListResponse(templates=templates, total=len(templates))

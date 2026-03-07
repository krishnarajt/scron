"""
API routes for managing the shared requirements.txt.
Lives under /config/requirements — separate from /jobs to avoid path collisions.
"""

import os
import subprocess

from fastapi import APIRouter, Depends
from app.db.models import User
from app.api.deps import get_current_user
from app.common.schemas import RequirementsUpdateRequest, RequirementsResponse
from app.common import constants
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/config", tags=["Config"])


@router.get("/requirements", response_model=RequirementsResponse)
def get_requirements(current_user: User = Depends(get_current_user)):
    """Get the current shared requirements.txt content."""
    req_path = os.path.join(constants.JOBS_SCRIPTS_DIR, "requirements.txt")
    content = ""
    if os.path.exists(req_path):
        with open(req_path, "r") as f:
            content = f.read()
    return RequirementsResponse(content=content)


@router.put("/requirements", response_model=RequirementsResponse)
def update_requirements(
    request: RequirementsUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Update the shared requirements.txt and run pip install.
    All jobs share the same Python environment.
    """
    os.makedirs(constants.JOBS_SCRIPTS_DIR, exist_ok=True)
    req_path = os.path.join(constants.JOBS_SCRIPTS_DIR, "requirements.txt")

    with open(req_path, "w") as f:
        f.write(request.content)

    # Run pip install
    try:
        result = subprocess.run(
            ["pip", "install", "-r", req_path, "--break-system-packages"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout for pip
        )
        install_output = result.stdout + result.stderr
        if result.returncode != 0:
            logger.warning(f"pip install exited with code {result.returncode}")
    except subprocess.TimeoutExpired:
        install_output = "pip install timed out after 300 seconds"
    except Exception as e:
        install_output = f"pip install failed: {str(e)}"

    return RequirementsResponse(
        content=request.content,
        last_install_output=install_output[-2000:],  # truncate
    )

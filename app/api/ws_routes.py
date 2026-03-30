"""
WebSocket routes for real-time log streaming.

Clients connect to:
    ws://host/api/ws/logs/{execution_id}?token=<access_token>

    OR for streaming the latest running execution of a job:
    ws://host/api/ws/logs/job/{job_id}?token=<access_token>

Auth is via query parameter since WebSocket doesn't support
Authorization headers in the browser.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.services import log_broadcaster
from app.services.auth_service import verify_access_token
from app.db.database import SessionLocal
from app.db.models import Job, JobExecution
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/logs/active")
async def list_active_streams(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    """
    One-shot: returns list of currently active log streams and closes.
    Only returns streams for jobs owned by the authenticated user.
    """
    user_id = await _authenticate_ws(websocket, token)
    if user_id is None:
        return

    await websocket.accept()

    # Filter active channels to only those owned by this user
    all_channels = log_broadcaster.get_active_channels()
    owned_job_ids = set()
    db = SessionLocal()
    try:
        jobs = db.query(Job.id).filter(Job.user_id == user_id).all()
        owned_job_ids = {j.id for j in jobs}
    finally:
        db.close()

    user_channels = [ch for ch in all_channels if ch["job_id"] in owned_job_ids]
    await websocket.send_json({"type": "active_streams", "streams": user_channels})
    await websocket.close()


async def _authenticate_ws(websocket: WebSocket, token: str) -> int | None:
    """Validate token from query param. Returns user_id or None."""
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return None
    user_id = verify_access_token(token)
    if user_id is None:
        await websocket.close(code=4001, reason="Invalid token")
        return None
    return user_id


def _user_owns_job(user_id: int, job_id: str) -> bool:
    """Check if the user owns the given job. Uses a short-lived DB session."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        return job is not None
    finally:
        db.close()


def _user_owns_execution(user_id: int, execution_id: int) -> bool:
    """Check if the user owns the job that produced the given execution."""
    db = SessionLocal()
    try:
        execution = (
            db.query(JobExecution)
            .join(Job, Job.id == JobExecution.job_id)
            .filter(JobExecution.id == execution_id, Job.user_id == user_id)
            .first()
        )
        return execution is not None
    finally:
        db.close()


@router.websocket("/logs/{execution_id}")
async def stream_execution_logs(
    websocket: WebSocket,
    execution_id: int,
    token: str = Query(default=""),
):
    """
    Stream real-time log lines for a specific execution.

    The client receives JSON messages:
        {"type": "log", "line": "..."}       — a log line
        {"type": "done"}                      — execution finished
        {"type": "error", "message": "..."}  — error message

    Buffered lines (from before the client connected) are sent first,
    then new lines stream in real time.
    """
    user_id = await _authenticate_ws(websocket, token)
    if user_id is None:
        return

    # Verify ownership before accepting the connection
    if not _user_owns_execution(user_id, execution_id):
        await websocket.accept()
        await websocket.send_json(
            {"type": "error", "message": "Not authorized to view this execution"}
        )
        await websocket.close(code=4003)
        return

    await websocket.accept()
    logger.info(f"WS client connected for execution {execution_id}")

    queue = await log_broadcaster.subscribe(execution_id)
    if queue is None:
        await websocket.send_json(
            {"type": "error", "message": "No active stream for this execution"}
        )
        await websocket.close()
        return

    try:
        while True:
            line = await queue.get()
            if line is None:
                # Execution finished
                await websocket.send_json({"type": "done"})
                break
            await websocket.send_json({"type": "log", "line": line})
    except WebSocketDisconnect:
        logger.info(f"WS client disconnected from execution {execution_id}")
    except Exception as e:
        logger.warning(f"WS error for execution {execution_id}: {e}")
    finally:
        log_broadcaster.unsubscribe(execution_id, queue)


@router.websocket("/logs/job/{job_id}")
async def stream_job_logs(
    websocket: WebSocket,
    job_id: str,
    token: str = Query(default=""),
):
    """
    Stream real-time logs for the currently running execution of a job.
    If no execution is running, returns an error and closes.
    """
    user_id = await _authenticate_ws(websocket, token)
    if user_id is None:
        return

    # Verify ownership before accepting the connection
    if not _user_owns_job(user_id, job_id):
        await websocket.accept()
        await websocket.send_json(
            {"type": "error", "message": "Not authorized to view this job"}
        )
        await websocket.close(code=4003)
        return

    await websocket.accept()

    # Find active execution for this job
    execution_id = log_broadcaster.get_channel_for_job(job_id)
    if execution_id is None:
        await websocket.send_json(
            {"type": "error", "message": "No running execution for this job"}
        )
        await websocket.close()
        return

    logger.info(f"WS client connected for job {job_id} (execution {execution_id})")

    queue = await log_broadcaster.subscribe(execution_id)
    if queue is None:
        await websocket.send_json({"type": "error", "message": "Stream ended"})
        await websocket.close()
        return

    try:
        # Send execution_id so client knows which execution it's watching
        await websocket.send_json({"type": "meta", "execution_id": execution_id})

        while True:
            line = await queue.get()
            if line is None:
                await websocket.send_json({"type": "done"})
                break
            await websocket.send_json({"type": "log", "line": line})
    except WebSocketDisconnect:
        logger.info(f"WS client disconnected from job {job_id}")
    except Exception as e:
        logger.warning(f"WS error for job {job_id}: {e}")
    finally:
        log_broadcaster.unsubscribe(execution_id, queue)

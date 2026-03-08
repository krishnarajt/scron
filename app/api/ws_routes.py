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
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


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


@router.websocket("/logs/active")
async def list_active_streams(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    """
    One-shot: returns list of currently active log streams and closes.
    Useful for the UI to know which executions can be live-tailed.
    """
    user_id = await _authenticate_ws(websocket, token)
    if user_id is None:
        return

    await websocket.accept()
    channels = log_broadcaster.get_active_channels()
    await websocket.send_json({"type": "active_streams", "streams": channels})
    await websocket.close()

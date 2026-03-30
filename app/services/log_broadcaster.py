"""
Log broadcaster — thread-safe pub/sub for real-time log streaming.

The scheduler executes jobs in threads (APScheduler threadpool) and publishes
log lines here. WebSocket handlers (async) subscribe and relay lines to clients.

Architecture:
    - Each running execution gets a "channel" keyed by execution_id.
    - Subscribers receive an asyncio.Queue that gets lines pushed into it
      from the scheduler thread via call_soon_threadsafe.
    - When the execution finishes, a sentinel (None) is pushed to all queues
      and the channel is cleaned up.

Thread safety:
    - _channels is protected by a threading.Lock (accessed from scheduler threads).
    - Lines are pushed into asyncio.Queues via loop.call_soon_threadsafe (thread→async bridge).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class _Channel:
    """A broadcast channel for a single execution."""

    execution_id: int
    job_id: str
    # List of (asyncio.Queue, asyncio.AbstractEventLoop) pairs
    subscribers: List[tuple] = field(default_factory=list)
    # Buffer of recent lines (so late-joiners can catch up)
    buffer: List[str] = field(default_factory=list)
    buffer_max: int = 500
    finished: bool = False


# ── Module-level state ────────────────────────────────────────

_lock = threading.Lock()
_channels: Dict[int, _Channel] = {}


# ── Publisher API (called from scheduler threads) ─────────────


def create_channel(execution_id: int, job_id: str) -> None:
    """Create a broadcast channel for a new execution. Call before the job starts."""
    with _lock:
        _channels[execution_id] = _Channel(execution_id=execution_id, job_id=job_id)
    logger.debug(f"Log channel created for execution {execution_id}")


def publish_line(execution_id: int, line: str) -> None:
    """
    Publish a single log line to all subscribers of an execution.
    Called from scheduler threads — must be thread-safe.
    """
    with _lock:
        channel = _channels.get(execution_id)
        if not channel:
            return

        # Buffer the line
        channel.buffer.append(line)
        if len(channel.buffer) > channel.buffer_max:
            channel.buffer = channel.buffer[-channel.buffer_max :]

        # Push to all subscriber queues
        for queue, loop in channel.subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, line)
            except Exception:
                pass  # queue full or loop closed — skip


def close_channel(execution_id: int) -> None:
    """
    Signal that the execution is finished. Pushes None sentinel to all
    subscribers and marks the channel as done.
    """
    with _lock:
        channel = _channels.get(execution_id)
        if not channel:
            return

        channel.finished = True

        # Push sentinel to all subscribers
        for queue, loop in channel.subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except Exception:
                pass

    logger.debug(f"Log channel closed for execution {execution_id}")

    # Clean up after a short delay (let subscribers drain)
    def _cleanup():
        import time

        time.sleep(5)
        with _lock:
            _channels.pop(execution_id, None)

    t = threading.Thread(target=_cleanup, daemon=True)
    t.start()


# ── Subscriber API (called from async WebSocket handlers) ─────


async def subscribe(execution_id: int) -> Optional[asyncio.Queue]:
    """
    Subscribe to log lines for an execution. Returns an asyncio.Queue
    that will receive lines (str) and a None sentinel when done.

    Returns None if the execution doesn't have an active channel
    (already finished or doesn't exist).
    """
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue(maxsize=1000)

    with _lock:
        channel = _channels.get(execution_id)
        if not channel:
            return None

        # Send buffered lines first (catch-up)
        for line in channel.buffer:
            queue.put_nowait(line)

        # If already finished, push sentinel immediately
        if channel.finished:
            queue.put_nowait(None)
        else:
            # Register as subscriber
            channel.subscribers.append((queue, loop))

    return queue


def unsubscribe(execution_id: int, queue: asyncio.Queue) -> None:
    """Remove a subscriber queue from a channel."""
    with _lock:
        channel = _channels.get(execution_id)
        if not channel:
            return
        channel.subscribers = [
            (q, lock) for q, lock in channel.subscribers if q is not queue
        ]


# ── Query API ─────────────────────────────────────────────────


def get_active_channels() -> List[Dict]:
    """Return a list of currently active (streaming) execution channels."""
    with _lock:
        return [
            {
                "execution_id": ch.execution_id,
                "job_id": ch.job_id,
                "finished": ch.finished,
                "subscribers": len(ch.subscribers),
                "buffer_lines": len(ch.buffer),
            }
            for ch in _channels.values()
        ]


def get_channel_for_job(job_id: str) -> Optional[int]:
    """Return the execution_id of an active (not finished) channel for a job, if any."""
    with _lock:
        for ch in _channels.values():
            if ch.job_id == job_id and not ch.finished:
                return ch.execution_id
    return None

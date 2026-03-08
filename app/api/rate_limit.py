"""
Simple in-memory rate limiter for auth endpoints.

Uses a sliding window approach with per-IP tracking.
Not suitable for multi-process deployments without a shared store (e.g. Redis).
For single-process or single-container deployments, this is sufficient.
"""

import time
import threading
from collections import defaultdict
from typing import Tuple

from fastapi import HTTPException, Request, status
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Lock for thread-safe access to the rate limit store
_lock = threading.Lock()

# Store: { key: [timestamp1, timestamp2, ...] }
_requests: dict[str, list[float]] = defaultdict(list)


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _cleanup_window(key: str, window_seconds: float) -> None:
    """Remove timestamps outside the current window (must hold _lock)."""
    cutoff = time.monotonic() - window_seconds
    _requests[key] = [t for t in _requests[key] if t > cutoff]
    # Clean up empty keys to prevent unbounded memory growth
    if not _requests[key]:
        del _requests[key]


def _check_rate_limit(
    key: str, max_requests: int, window_seconds: float
) -> Tuple[bool, int]:
    """
    Check if a request is allowed under the rate limit.
    Returns (allowed, remaining_requests).
    """
    now = time.monotonic()
    with _lock:
        _cleanup_window(key, window_seconds)
        current_count = len(_requests.get(key, []))
        if current_count >= max_requests:
            return False, 0
        _requests.setdefault(key, []).append(now)
        return True, max_requests - current_count - 1


def rate_limit_auth(request: Request) -> None:
    """
    FastAPI dependency that enforces rate limiting on auth endpoints.
    Limits: 10 requests per 60 seconds per IP.
    """
    ip = _get_client_ip(request)
    key = f"auth:{ip}"
    allowed, remaining = _check_rate_limit(key, max_requests=10, window_seconds=60)
    if not allowed:
        logger.warning(f"Rate limit exceeded for {ip} on auth endpoint")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": "60"},
        )


def rate_limit_login(request: Request) -> None:
    """
    Stricter rate limit specifically for login attempts.
    Limits: 5 requests per 60 seconds per IP.
    """
    ip = _get_client_ip(request)
    key = f"login:{ip}"
    allowed, remaining = _check_rate_limit(key, max_requests=5, window_seconds=60)
    if not allowed:
        logger.warning(f"Login rate limit exceeded for {ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": "60"},
        )

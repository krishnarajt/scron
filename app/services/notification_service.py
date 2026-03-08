"""
Notification service — sends alerts via Telegram and/or Email.

Called by the scheduler after every job execution completes.
Respects user preferences (notify_on: failure_only | always | never).
"""

import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from app.common import constants
from app.db.database import SessionLocal
from app.db.models import User, NotificationSettings
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


def _get_user_notification_settings(user_id: int) -> Optional[dict]:
    """Load notification settings for a user. Returns None if not configured."""
    db = SessionLocal()
    try:
        settings = (
            db.query(NotificationSettings)
            .filter(NotificationSettings.user_id == user_id)
            .first()
        )
        if not settings:
            return None
        user = db.query(User).filter(User.id == user_id).first()
        return {
            "telegram_enabled": settings.telegram_enabled,
            "telegram_chat_id": settings.telegram_chat_id,
            "email_enabled": settings.email_enabled,
            "notify_on": settings.notify_on,
            "user_email": user.email if user else None,
        }
    finally:
        db.close()


def _format_message(
    job_name: str,
    status: str,
    duration: float,
    error_summary: str = None,
    execution_id: int = None,
) -> str:
    """Format a notification message."""
    emoji = (
        "\u2705"
        if status == "success"
        else "\u274c"
        if status == "failure"
        else "\u26a0\ufe0f"
    )
    lines = [
        f"{emoji} *Job: {job_name}*",
        f"Status: {status.upper()}",
        f"Duration: {duration:.1f}s" if duration is not None else "Duration: N/A",
    ]
    if execution_id:
        lines.append(f"Execution ID: {execution_id}")
    if error_summary and status == "failure":
        # Truncate for message readability
        short_err = error_summary[:300]
        lines.append(f"\nError:\n```\n{short_err}\n```")
    return "\n".join(lines)


def _send_telegram(chat_id: str, message: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not constants.TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram notification skipped: TELEGRAM_BOT_TOKEN not set")
        return False

    try:
        import urllib.request
        import urllib.parse
        import json

        url = f"https://api.telegram.org/bot{constants.TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }
        ).encode("utf-8")

        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                logger.error(f"Telegram API error: {result}")
                return False
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def _send_email(to_email: str, subject: str, body: str) -> bool:
    """Send an email via SMTP (Gmail)."""
    if not constants.SMTP_USER or not constants.SMTP_PASSWORD:
        logger.warning("Email notification skipped: SMTP_USER/SMTP_PASSWORD not set")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = constants.SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject

        # Plain text version (strip markdown)
        plain = body.replace("*", "").replace("```", "")
        msg.attach(MIMEText(plain, "plain"))

        # HTML version
        html_body = body.replace("\n", "<br>")
        html_body = html_body.replace("```", "<pre>").replace("</pre><br>", "</pre>")
        msg.attach(MIMEText(f"<html><body>{html_body}</body></html>", "html"))

        with smtplib.SMTP(constants.SMTP_HOST, constants.SMTP_PORT) as server:
            server.starttls()
            server.login(constants.SMTP_USER, constants.SMTP_PASSWORD)
            server.sendmail(constants.SMTP_FROM, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


def notify_execution_complete(
    user_id: int,
    job_name: str,
    status: str,
    duration: float = 0.0,
    error_summary: str = None,
    execution_id: int = None,
) -> None:
    """
    Send notification about a completed execution.
    Runs in a daemon thread to avoid blocking the scheduler.

    Called after every execution — checks user preferences to decide
    whether to actually send anything.
    """

    def _do_notify():
        settings = _get_user_notification_settings(user_id)
        if not settings:
            return

        notify_on = settings["notify_on"]

        # Check if we should notify based on preference
        if notify_on == "never":
            return
        if notify_on == "failure_only" and status != "failure":
            return
        # notify_on == "always" → always send

        message = _format_message(
            job_name, status, duration, error_summary, execution_id
        )

        # Send Telegram
        if settings["telegram_enabled"] and settings["telegram_chat_id"]:
            _send_telegram(settings["telegram_chat_id"], message)

        # Send Email
        if settings["email_enabled"] and settings["user_email"]:
            subject = f"sCron: {job_name} — {status.upper()}"
            _send_email(settings["user_email"], subject, message)

    # Fire and forget in a background thread
    t = threading.Thread(target=_do_notify, daemon=True)
    t.start()

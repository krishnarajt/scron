"""Application-wide constants loaded from environment variables.

Every other module should import needed values from here rather than
accessing :mod:`os.environ` directly.  This centralises configuration and
makes it easier to mock during tests.

The module also ensures ``.env`` files are loaded early via :func:`load_dotenv`.
"""

from __future__ import annotations

import os
from typing import List

from dotenv import load_dotenv

# Load environment variables from .env file (if present).  This happens
# on import so any module importing constants will have the vars available.
load_dotenv()

# ---------------------------------------------------------------------------
# Generic environment flags
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR: str = os.getenv("LOG_DIR", "logs")

# ---------------------------------------------------------------------------
# Server / FastAPI settings
# ---------------------------------------------------------------------------
PORT: int = int(os.getenv("PORT", "8000"))
RELOAD: bool = os.getenv("RELOAD", "false").lower() == "true"

# CORS origins are a comma-separated list.  ``*`` means allow all.
CORS_ORIGINS: List[str] = os.getenv("CORS_ORIGINS", "*").split(",")

# ---------------------------------------------------------------------------
# Database settings
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)
DB_SCHEMA: str = os.getenv("DB_SCHEMA", "public")

# ---------------------------------------------------------------------------
# Security / auth
# ---------------------------------------------------------------------------
SECRET_KEY: str = os.getenv("SECRET_KEY") or ""
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is not set!")

# ---------------------------------------------------------------------------
# Scheduler / job execution settings
# ---------------------------------------------------------------------------

# Directory where job scripts are materialised before execution.
# Each script is written to {JOBS_SCRIPTS_DIR}/{job_id}.(py|sh)
JOBS_SCRIPTS_DIR: str = os.getenv("JOBS_SCRIPTS_DIR", "/tmp/scron_scripts")

# Maximum number of jobs that can run concurrently
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))

# How many characters of stderr to capture in error_summary on failure
MAX_ERROR_SUMMARY_LENGTH: int = int(os.getenv("MAX_ERROR_SUMMARY_LENGTH", "500"))

# Encryption key derivation iterations (PBKDF2 for Fernet key from SECRET_KEY + user salt)
ENCRYPTION_KEY_ITERATIONS: int = int(os.getenv("ENCRYPTION_KEY_ITERATIONS", "100000"))

# Number of head/tail lines of combined stdout+stderr to capture per execution
LOG_HEAD_LINES: int = int(os.getenv("LOG_HEAD_LINES", "50"))
LOG_TAIL_LINES: int = int(os.getenv("LOG_TAIL_LINES", "50"))

# Default timeout for job execution (seconds). Can be overridden per-job.
DEFAULT_JOB_TIMEOUT: int = int(os.getenv("DEFAULT_JOB_TIMEOUT", "3600"))

# ---------------------------------------------------------------------------
# Telegram bot settings
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ---------------------------------------------------------------------------
# Email / SMTP settings (Gmail App Password recommended)
# ---------------------------------------------------------------------------
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM: str = os.getenv("SMTP_FROM", "") or SMTP_USER

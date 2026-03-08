from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Boolean,
    Float,
    Text,
    Index,
    JSON,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class User(Base):
    """User model - stores user info and authentication"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)

    # Per-user salt used for deriving encryption keys (env var encryption)
    # Generated once on signup, never changes
    salt = Column(String(64), nullable=False)

    # Profile
    display_name = Column(String(100), default="")
    email = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    jobs = relationship("Job", back_populates="owner", cascade="all, delete-orphan")
    notification_settings = relationship(
        "NotificationSettings", back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """Stores refresh tokens for JWT authentication"""

    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token = Column(String(500), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="refresh_tokens")


class Tag(Base):
    """User-defined tags for organising jobs."""

    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(100), nullable=False)
    color = Column(String(7), nullable=False, default="#6366f1")  # hex color

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    jobs = relationship("JobTag", back_populates="tag", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_tags_user_name", "user_id", "name", unique=True),)


class JobTag(Base):
    """Many-to-many association between jobs and tags."""

    __tablename__ = "job_tags"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    job = relationship("Job", back_populates="tags")
    tag = relationship("Tag", back_populates="jobs")

    __table_args__ = (Index("ix_job_tags_job_tag", "job_id", "tag_id", unique=True),)


class Job(Base):
    """
    Represents a scheduled cron job.
    Each job owns a script (stored as text in DB), a cron expression,
    and its own set of encrypted environment variables.
    """

    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")

    # The actual script content (python or bash) stored in DB as source of truth
    script_content = Column(Text, nullable=False)
    # "python" or "bash" — determines how the script is executed
    script_type = Column(String(20), nullable=False, default="python")

    # Cron expression string, e.g. "*/5 * * * *"
    cron_expression = Column(String(100), nullable=False)

    # Whether the scheduler should pick this job up
    is_active = Column(Boolean, default=True, nullable=False)

    # Per-job timeout in seconds (0 = use system default)
    timeout_seconds = Column(Integer, nullable=False, default=0)

    # DAG dependencies: list of job IDs that must succeed before this job runs.
    # Stored as JSON array of strings: ["uuid-1", "uuid-2"]
    depends_on = Column(JSON, nullable=False, default=list)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    owner = relationship("User", back_populates="jobs")
    env_vars = relationship(
        "JobEnvVar", back_populates="job", cascade="all, delete-orphan"
    )
    executions = relationship(
        "JobExecution",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="desc(JobExecution.started_at)",
    )
    script_versions = relationship(
        "JobScriptVersion",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="desc(JobScriptVersion.version)",
    )
    tags = relationship("JobTag", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_jobs_user_active", "user_id", "is_active"),)


class JobEnvVar(Base):
    """
    Encrypted environment variable for a specific job.
    The value is encrypted using a Fernet key derived from (SECRET_KEY + user.salt).
    """

    __tablename__ = "job_env_vars"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The variable name, stored in plaintext (e.g. "DATABASE_URL")
    var_key = Column(String(200), nullable=False)
    # The variable value, Fernet-encrypted then base64-encoded
    encrypted_value = Column(Text, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    job = relationship("Job", back_populates="env_vars")

    __table_args__ = (
        Index("ix_job_env_vars_job_key", "job_id", "var_key", unique=True),
    )


class JobScriptVersion(Base):
    """
    Immutable snapshot of a job's script at a point in time.
    A new row is created every time the script_content is changed via update.
    """

    __tablename__ = "job_script_versions"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Monotonically increasing version number per job (1, 2, 3, …)
    version = Column(Integer, nullable=False)
    # The script content at this version
    script_content = Column(Text, nullable=False)
    script_type = Column(String(20), nullable=False, default="python")
    # Optional human-readable label, e.g. "Added retry logic"
    change_summary = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    job = relationship("Job", back_populates="script_versions")

    __table_args__ = (
        Index("ix_job_script_versions_job_ver", "job_id", "version", unique=True),
    )


class JobExecution(Base):
    """
    Immutable log of every execution of a job.
    One row per run — never updated after the run completes, only inserted.
    """

    __tablename__ = "job_executions"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # When the execution started and ended (UTC, timezone-aware)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # How long it took in seconds (computed on completion)
    duration_seconds = Column(Float, nullable=True)

    # "running", "success", "failure", "cancelled"
    status = Column(String(20), nullable=False, default="running")

    # Subprocess exit code (0 = success, non-zero = failure, null = still running or killed)
    exit_code = Column(Integer, nullable=True)

    # Short error summary (NOT full logs — just last 500 chars of stderr on failure)
    error_summary = Column(Text, nullable=True)

    # Captured output: first 50 + last 50 lines of combined stdout+stderr
    log_output = Column(Text, nullable=True)

    # Script version that was executed (for replay)
    script_version_id = Column(Integer, nullable=True)

    # PID of the subprocess (for cancellation). Null when not running.
    pid = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    job = relationship("Job", back_populates="executions")

    __table_args__ = (
        Index("ix_job_executions_job_started", "job_id", "started_at"),
        Index("ix_job_executions_status", "status"),
    )


class NotificationSettings(Base):
    """
    Per-user notification preferences.
    Supports Telegram and Email channels.
    Users can enable/disable each channel and configure when to notify.
    """

    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Telegram settings
    telegram_enabled = Column(Boolean, default=False, nullable=False)
    telegram_chat_id = Column(String(100), nullable=True)

    # Email settings (uses user.email as destination)
    email_enabled = Column(Boolean, default=False, nullable=False)

    # When to notify: "failure_only", "always", "never"
    notify_on = Column(String(20), nullable=False, default="failure_only")

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", back_populates="notification_settings")


class JobTemplate(Base):
    """
    Pre-built script templates for common tasks.
    Some are seeded by default; users can also create their own.
    """

    __tablename__ = "job_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    category = Column(String(100), nullable=False, default="general")
    script_content = Column(Text, nullable=False)
    script_type = Column(String(20), nullable=False, default="python")
    # Suggested cron expression (user can override)
    default_cron = Column(String(100), nullable=False, default="0 * * * *")
    # null = system template (visible to all), non-null = user-created
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

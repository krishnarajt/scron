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

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    jobs = relationship("Job", back_populates="owner", cascade="all, delete-orphan")


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

    # "running", "success", "failure"
    status = Column(String(20), nullable=False, default="running")

    # Subprocess exit code (0 = success, non-zero = failure, null = still running or killed)
    exit_code = Column(Integer, nullable=True)

    # Short error summary (NOT full logs — just last 500 chars of stderr on failure)
    error_summary = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    job = relationship("Job", back_populates="executions")

    __table_args__ = (
        Index("ix_job_executions_job_started", "job_id", "started_at"),
        Index("ix_job_executions_status", "status"),
    )

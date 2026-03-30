"""
Test fixtures using a real SQLite in-memory database.

This gives us actual SQL execution, foreign key enforcement,
and ORM relationship resolution — far more reliable than mocking.
"""

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

# Ensure `app` package is importable when pytest is run from repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Required at import time by app.common.constants
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
# Use SQLite for tests so we don't need a running PostgreSQL
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
# SQLite doesn't support schemas — force public
os.environ["DB_SCHEMA"] = "public"

from app.db.database import Base, get_db  # noqa: E402
from app.db.models import (  # noqa: E402
    User,
    Job,
    JobScriptVersion,
    Tag,
)
from app.api.auth_routes import router as auth_router  # noqa: E402
from app.api.job_routes import router as jobs_router  # noqa: E402
from app.api.config_routes import router as config_router  # noqa: E402
from app.api.analytics_routes import router as analytics_router  # noqa: E402
from app.api.tag_routes import router as tag_router  # noqa: E402
from app.api.notification_routes import router as notification_router  # noqa: E402
from app.api.template_routes import router as template_router  # noqa: E402
from app.api.user_routes import router as user_router  # noqa: E402
from app.services.auth_service import get_password_hash, create_access_token  # noqa: E402

# Clean rate limit state before each test
from app.api.rate_limit import _requests, _lock  # noqa: E402


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limit store before each test to prevent cross-test leakage."""
    with _lock:
        _requests.clear()
    yield
    with _lock:
        _requests.clear()


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def db_engine():
    """Create a fresh SQLite in-memory engine for each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enable foreign key enforcement for SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # SQLite doesn't support schemas — remove the schema from
    # Base.metadata so all DDL and queries work without schema prefix
    Base.metadata.schema = None
    for table in Base.metadata.tables.values():
        table.schema = None

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine) -> Iterator[Session]:
    """Provide a transactional DB session that rolls back after each test."""
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# FastAPI app + test client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def app(db_session: Session) -> FastAPI:
    """Create a FastAPI app wired to the test database session."""
    test_app = FastAPI()
    test_app.include_router(auth_router, prefix="/api")
    test_app.include_router(jobs_router, prefix="/api")
    test_app.include_router(config_router, prefix="/api")
    test_app.include_router(analytics_router, prefix="/api")
    test_app.include_router(tag_router, prefix="/api")
    test_app.include_router(notification_router, prefix="/api")
    test_app.include_router(template_router, prefix="/api")
    test_app.include_router(user_router, prefix="/api")

    def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db
    return test_app


@pytest.fixture(scope="function")
def client(app: FastAPI) -> Iterator[TestClient]:
    """HTTP test client."""
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def test_user(db_session: Session) -> User:
    """Insert a test user and return it."""
    import secrets

    user = User(
        username="testuser",
        password_hash=get_password_hash("testpassword"),
        salt=secrets.token_hex(32),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def auth_headers(test_user: User) -> dict:
    """Return Authorization headers with a valid access token for test_user."""
    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_job(db_session: Session, test_user: User) -> Job:
    """Insert a test job and return it."""
    job = Job(
        user_id=test_user.id,
        name="Test Job",
        description="A test cron job",
        script_content="print('hello')",
        script_type="python",
        cron_expression="*/5 * * * *",
        is_active=True,
        timeout_seconds=0,
        depends_on=[],
    )
    db_session.add(job)
    db_session.flush()
    # Also add initial script version
    version = JobScriptVersion(
        job_id=job.id,
        version=1,
        script_content=job.script_content,
        script_type=job.script_type,
        change_summary="Initial version",
    )
    db_session.add(version)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture
def test_tag(db_session: Session, test_user: User) -> Tag:
    """Insert a test tag and return it."""
    tag = Tag(user_id=test_user.id, name="production", color="#ef4444")
    db_session.add(tag)
    db_session.commit()
    db_session.refresh(tag)
    return tag

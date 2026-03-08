from sqlalchemy import create_engine, MetaData
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.utils.logging_utils import get_logger

# central configuration values
from app.common import constants

logger = get_logger(__name__)

DATABASE_URL = constants.DATABASE_URL

# Read schema from env, default to public
DB_SCHEMA = constants.DB_SCHEMA

# SQLite requires check_same_thread=False for multi-threaded access (e.g. scheduler)
_connect_args = {}
_engine_kwargs = {"pool_pre_ping": True, "connect_args": _connect_args}

if DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
else:
    # Connection pool tuning for PostgreSQL
    _engine_kwargs.update(
        {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_recycle": 1800,  # recycle connections after 30 min
        }
    )

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base(metadata=MetaData(schema=DB_SCHEMA))


def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(use_alembic: bool = False):
    """
    Initialize the database.

    If use_alembic is True, runs Alembic migrations (alembic upgrade head).
    Otherwise, falls back to SQLAlchemy create_all() for quick dev setup.

    For production, always use Alembic:
        alembic upgrade head
    """
    # Step 1: Create schema in its own committed transaction
    with engine.connect() as conn:
        if DB_SCHEMA != "public":
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"'))
            conn.commit()  # <-- commit BEFORE create_all

    if use_alembic:
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrated via Alembic")
    else:
        # Step 2: Now create tables (schema already exists)
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created via create_all()")

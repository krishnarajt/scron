from sqlalchemy import create_engine, MetaData
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from app.utils.logging_utils import get_logger

# central configuration values
from app.common import constants

logger = get_logger(__name__)

DATABASE_URL = constants.DATABASE_URL

# Read schema from env, default to public
DB_SCHEMA = constants.DB_SCHEMA

# SQLite requires check_same_thread=False for multi-threaded access (e.g. scheduler)
_connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)

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


def init_db():
    # Step 1: Create schema in its own committed transaction
    with engine.connect() as conn:
        if DB_SCHEMA != "public":
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"'))
            conn.commit()  # <-- commit BEFORE create_all

    # Step 2: Now create tables (schema already exists)
    Base.metadata.create_all(bind=engine)

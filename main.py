from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone

# central configuration values and environment loading
from app.common import constants

from sqlalchemy import text
from app.db.database import get_db, init_db
from app.api.auth_routes import router as auth_router
from app.api.job_routes import router as jobs_router
from app.api.config_routes import router as config_router
from app.api.analytics_routes import router as analytics_router
from app.api.ws_routes import router as ws_router
from app.api.tag_routes import router as tag_router
from app.api.notification_routes import router as notification_router
from app.api.template_routes import router as template_router
from app.api.user_routes import router as user_router
from app.services.scheduler_service import (
    startup as scheduler_startup,
    shutdown as scheduler_shutdown,
)
from sqlalchemy.orm import Session

# Use the unified logging setup from logging_utils (no duplicate basicConfig)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events with improved error handling"""

    try:
        # Startup
        logger.info("=" * 60)
        logger.info("Starting scron Backend...")
        logger.info("=" * 60)

        # Initialize database
        try:
            init_db()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.critical(f"Failed to initialize database: {e}", exc_info=True)
            raise

        # Start the job scheduler (loads active jobs from DB)
        try:
            scheduler_startup()
            logger.info("Job scheduler started successfully")
        except Exception as e:
            logger.critical(f"Failed to start job scheduler: {e}", exc_info=True)
            raise

        logger.info("=" * 60)
        logger.info("scron backend is ready!")
        logger.info("=" * 60)

        yield

    finally:
        # Shutdown
        logger.info("=" * 60)
        logger.info("Shutting down scron Backend...")
        logger.info("=" * 60)

        # Stop the job scheduler gracefully (waits for running jobs to finish)
        try:
            scheduler_shutdown()
            logger.info("Job scheduler stopped")
        except Exception as e:
            logger.error(f"Error stopping job scheduler: {e}")

        logger.info("=" * 60)
        logger.info("scron Backend shutdown complete")
        logger.info("=" * 60)


# Create FastAPI app
app = FastAPI(
    title="scron Backend",
    description="Backend API for managing scheduled cron jobs",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
cors_origins = constants.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(f"CORS enabled for origins: {cors_origins}")

# Include routers
app.include_router(auth_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(ws_router, prefix="/api")
app.include_router(tag_router, prefix="/api")
app.include_router(notification_router, prefix="/api")
app.include_router(template_router, prefix="/api")
app.include_router(user_router, prefix="/api")

logger.info("API routers registered")


@app.get("/")
def root():
    """Root endpoint"""
    return {
        "name": "scron Backend",
        "description": "scron Backend API",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.get("/ready")
def readiness_check():
    """Readiness check endpoint for k8s"""
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    import uvicorn

    port = constants.PORT
    reload = constants.RELOAD

    logger.info(f"Starting server on port {port}, reload={reload}")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        log_level="info",
        reload_dirs=["app"],  # <-- only watch app/ folder, ignore .env and logs
    )

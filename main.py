from dotenv import load_dotenv
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from datetime import datetime
from app.db.database import init_db
from app.api.auth_routes import router as auth_router

load_dotenv()


# Configure logging
def setup_logging():
    """Configure application-wide logging"""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            # Console handler
            logging.StreamHandler(sys.stdout),
            # File handler - rotates daily
            logging.FileHandler(
                f"logs/app_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"
            ),
        ],
    )

    # Set specific log levels for noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured at {log_level} level")
    return logger


logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events with improved error handling"""
    notification_task = None

    try:
        # Startup
        logger.info("=" * 60)
        logger.info("Starting scron Backend...")
        logger.info("=" * 60)

        # Initialize database
        try:
            init_db()
            logger.info("✓ Database initialized successfully")
        except Exception as e:
            logger.critical(f"✗ Failed to initialize database: {e}", exc_info=True)
            raise

        logger.info("=" * 60)
        logger.info("scron Backend is ready!")
        logger.info("=" * 60)

        yield

    finally:
        # Shutdown
        logger.info("=" * 60)
        logger.info("Shutting down scron Backend...")
        logger.info("=" * 60)

        if notification_task:
            try:
                notification_task.cancel()
                await asyncio.wait_for(notification_task, timeout=5.0)
            except asyncio.CancelledError:
                logger.info("✓ Notification scheduler stopped")
            except asyncio.TimeoutError:
                logger.warning("⚠ Notification scheduler shutdown timed out")
            except Exception as e:
                logger.error(f"✗ Error stopping notification scheduler: {e}")

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
cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
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

logger.info("API routers registered")


@app.get("/")
def root():
    """Root endpoint"""
    return {
        "name": "scron Backend",
        "description": "scron Backend API",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/health")
def health_check():
    """Health check endpoint for k8s"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/ready")
def readiness_check():
    """Readiness check endpoint for k8s"""
    return {"status": "ready", "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "false").lower() == "true"

    logger.info(f"Starting server on port {port}, reload={reload}")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        log_level="info",
        reload_dirs=["app"],  # <-- only watch app/ folder, ignore .env and logs
    )

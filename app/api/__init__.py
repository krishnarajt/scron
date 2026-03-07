from app.api.auth_routes import router as auth_router
from app.api.job_routes import router as jobs_router
from app.api.config_routes import router as config_router

__all__ = ["auth_router", "jobs_router", "config_router"]

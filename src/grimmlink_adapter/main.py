"""Main application factory and lifespan entrypoint for GrimmLink Adapter."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from grimmlink_adapter import __version__
from grimmlink_adapter.api.router import api_router
from grimmlink_adapter.config import settings
from grimmlink_adapter.security.masking import mask_secret, setup_secure_logging
from grimmlink_adapter.state.migrations import apply_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for database initialization and cleanup."""
    # Enforce secret masking across all loggers
    setup_secure_logging(settings.LOG_LEVEL)
    logger.info("Initializing GrimmLink Adapter v%s", __version__)

    # Ensure SQLite directory exists and apply migrations
    db_path = settings.sqlite_db_path_resolved
    logger.info("SQLite database path: %s", db_path)
    try:
        applied = await apply_migrations()
        logger.info("Applied %d database migration(s)", applied)
    except Exception as exc:
        logger.critical("Failed to apply database migrations during startup: %s", exc)
        raise

    yield

    logger.info("Shutting down GrimmLink Adapter")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="GrimmLink Adapter",
        description="Standalone compatibility adapter bridging KOReader GrimmLink to Official Grimmory",
        version=__version__,
        lifespan=lifespan,
    )

    # Permissive CORS for KOReader device requests
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Safe global exception handler redacting secrets from error messages
    @application.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        sanitized_msg = mask_secret(str(exc))
        logger.error("Unhandled exception processing %s %s: %s", request.method, request.url.path, sanitized_msg)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error occurred.", "error": sanitized_msg},
        )

    # Healthcheck
    @application.get("/healthcheck", tags=["Health"])
    async def healthcheck() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
        }

    # Mount legacy GrimmLink compatibility router
    application.include_router(api_router)

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "grimmlink_adapter.main:app",
        host=settings.ADAPTER_HOST,
        port=settings.ADAPTER_PORT,
        reload=False,
    )

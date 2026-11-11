"""Main application factory and lifespan entrypoint for GrimmLink Adapter."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from grimmlink_adapter import __version__
from grimmlink_adapter.api.router import api_router, api_v2_router
from grimmlink_adapter.config import settings
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.runtime import start_runtime, stop_runtime
from grimmlink_adapter.performance import RequestTimingMiddleware
from grimmlink_adapter.security.masking import mask_secret, setup_secure_logging
from grimmlink_adapter.services.mdns import MDNSAdvertiser
from grimmlink_adapter.services.metadata_automation import metadata_automation
from grimmlink_adapter.state.linked_accounts import LinkedAccountStore
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
        await LinkedAccountStore.encrypt_existing()
        logger.info("Applied %d database migration(s)", applied)
    except Exception as exc:
        logger.critical("Failed to apply database migrations during startup: %s", exc)
        raise

    mdns = MDNSAdvertiser()
    await mdns.start()
    app.state.mdns_advertiser = mdns

    try:
        await start_runtime()
        await metadata_automation.start()
        yield
    finally:
        try:
            await metadata_automation.stop()
        finally:
            try:
                await stop_runtime()
            finally:
                await mdns.stop()
                app.state.mdns_advertiser = None

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
    application.add_middleware(RequestTimingMiddleware)

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
        """Local liveness probe; intentionally independent from Grimmory."""
        return {
            "status": "ok",
            "version": __version__,
        }

    @application.get("/readyz", tags=["Health"])
    async def readiness() -> JSONResponse:
        """Read-only readiness probe that also requires Official Grimmory."""
        try:
            async with OfficialGrimmoryClient() as client:
                await client.check_health()
        except Exception as exc:
            # Keep details and upstream response bodies out of this unauthenticated probe.
            logger.warning("Readiness check failed (%s)", type(exc).__name__)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not_ready", "upstream": "grimmory_unavailable", "version": __version__},
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready", "upstream": "ok", "version": __version__},
        )

    @application.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse(url="/api/grimmlink/v2/local/metadata", status_code=307)

    # Mount legacy GrimmLink compatibility router
    application.include_router(api_router)
    application.include_router(api_v2_router)

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

"""Central API router grouping all legacy GrimmLink compatibility endpoints."""

from fastapi import APIRouter

from grimmlink_adapter.api.auth import router as auth_router
from grimmlink_adapter.api.books import router as books_router
from grimmlink_adapter.api.capabilities import router as capabilities_router
from grimmlink_adapter.api.metadata import router as metadata_router
from grimmlink_adapter.api.progress import router as progress_router
from grimmlink_adapter.api.sessions import router as sessions_router
from grimmlink_adapter.api.shelves import router as shelves_router

API_V1_PREFIX = "/api/grimmlink/v1"

api_router = APIRouter(prefix=API_V1_PREFIX)

api_router.include_router(auth_router)
api_router.include_router(capabilities_router)
api_router.include_router(books_router)
api_router.include_router(shelves_router)
api_router.include_router(progress_router)
api_router.include_router(metadata_router)
api_router.include_router(sessions_router)

"""Central API router grouping all legacy GrimmLink compatibility endpoints."""

from fastapi import APIRouter, Depends

from grimmlink_adapter.api.auth import router as auth_router
from grimmlink_adapter.api.automation import router as automation_router
from grimmlink_adapter.api.books import router as books_router
from grimmlink_adapter.api.capabilities import router as capabilities_router
from grimmlink_adapter.api.metadata import router as metadata_router
from grimmlink_adapter.api.progress import router as progress_router
from grimmlink_adapter.api.sessions import router as sessions_router
from grimmlink_adapter.api.shelves import router as shelves_router
from grimmlink_adapter.api.v2 import router as v2_alias_router
from grimmlink_adapter.security.auth_extractor import get_adapter_session

API_V1_PREFIX = "/api/grimmlink/v1"
API_V2_PREFIX = "/api/grimmlink/v2"

api_router = APIRouter(prefix=API_V1_PREFIX)

api_router.include_router(auth_router)
api_router.include_router(capabilities_router)
api_router.include_router(books_router)
api_router.include_router(shelves_router)
api_router.include_router(progress_router)
api_router.include_router(metadata_router)
api_router.include_router(sessions_router)

api_v2_router = APIRouter(prefix=API_V2_PREFIX)
api_v2_router.include_router(v2_alias_router)
api_v2_router.include_router(automation_router)
api_v2_router.include_router(capabilities_router)
# Do not mount the complete legacy routers under v2. Their path/payload aliases
# include unadvertised operations such as Magic Shelves and legacy book status.
# V2's explicit routes above are the only book, shelf, and progress contract.
api_v2_router.include_router(metadata_router, dependencies=[Depends(get_adapter_session)])
api_v2_router.include_router(sessions_router, dependencies=[Depends(get_adapter_session)])

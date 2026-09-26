"""Authentication routes for legacy GrimmLink contract."""

from fastapi import APIRouter, Depends

from grimmlink_adapter.models.grimmlink import GrimmlinkAuthResponse
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.auth_service import AuthService

router = APIRouter(tags=["Auth"])
_auth_service = AuthService()


@router.get("/auth", response_model=GrimmlinkAuthResponse)
async def authorize(
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkAuthResponse:
    """Authorize current KOReader client."""
    return await _auth_service.authorize_client(creds)

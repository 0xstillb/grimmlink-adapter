"""Authentication bridge service translating GrimmLink MD5 credentials to Official Grimmory."""

import logging

import httpx
from fastapi import HTTPException, status

from grimmlink_adapter.models.grimmlink import GrimmlinkAuthResponse
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.security.auth_extractor import ClientCredentials

logger = logging.getLogger(__name__)


class AuthService:
    """Handles authentication for GrimmLink clients against Official Grimmory."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def authorize_client(self, creds: ClientCredentials) -> GrimmlinkAuthResponse:
        """Validate client credentials against Official Grimmory.

        Rejects requests if credentials fail verification or upstream is unavailable.
        Never silently falls back to successful authentication.
        """
        if creds.username and creds.md5_key:
            try:
                auth_data = await self.official_client.get_koreader_auth(creds.username, creds.md5_key)
                user_id = auth_data.get("userId") or auth_data.get("id", 1)
                sync_enabled = auth_data.get("syncEnabled", True)
                sync_with_web = auth_data.get("syncWithWebReader", True)
                return GrimmlinkAuthResponse(
                    status="ok",
                    username=creds.username,
                    userId=int(user_id),
                    syncEnabled=sync_enabled,
                    syncWithWebReader=sync_with_web,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    logger.warning("Upstream Official auth rejected credentials for user %s", creds.username)
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid username or MD5 authentication key.",
                    ) from exc
                logger.error("Upstream Official auth returned unexpected error: %s", exc.response.status_code)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Upstream Official Grimmory returned status {exc.response.status_code}.",
                ) from exc
            except httpx.RequestError as exc:
                logger.error("Upstream Official Grimmory unreachable: %s", type(exc).__name__)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Upstream Official Grimmory service is unreachable.",
                ) from exc

        if creds.bearer_token:
            try:
                user_data = await self.official_client.get_current_user(creds.bearer_token)
                user_id = user_data.get("id") or user_data.get("userId", 1)
                username = user_data.get("username", "bearer_user")
                return GrimmlinkAuthResponse(
                    status="ok",
                    username=str(username),
                    userId=int(user_id),
                    syncEnabled=True,
                    syncWithWebReader=True,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    logger.warning("Upstream Official rejected Bearer token")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired Bearer token.",
                    ) from exc
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Upstream Official Grimmory returned status {exc.response.status_code}.",
                ) from exc
            except httpx.RequestError as exc:
                logger.error("Upstream Official Grimmory unreachable: %s", type(exc).__name__)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Upstream Official Grimmory service is unreachable.",
                ) from exc

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials required (x-auth-user + x-auth-key or Bearer token).",
        )

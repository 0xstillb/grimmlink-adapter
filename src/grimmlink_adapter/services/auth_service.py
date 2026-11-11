"""Authentication bridge service translating GrimmLink MD5 credentials to Official Grimmory."""

import logging
from contextlib import suppress

import httpx
from fastapi import HTTPException, status

from grimmlink_adapter.models.grimmlink import GrimmlinkAuthResponse
from grimmlink_adapter.official import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialGrimmoryClient,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.performance import measure
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.state.linked_accounts import LinkedAccountStore

logger = logging.getLogger(__name__)


class AuthService:
    """Handles authentication for GrimmLink clients against Official Grimmory."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def authorize_client(self, creds: ClientCredentials) -> GrimmlinkAuthResponse:
        with measure("auth"):
            return await self._authorize_client(creds)

    async def _authorize_client(self, creds: ClientCredentials) -> GrimmlinkAuthResponse:
        """Validate client credentials against Official Grimmory.

        Rejects requests if credentials fail verification or upstream is unavailable.
        Never silently falls back to successful authentication.
        """
        if creds.username and creds.md5_key:
            try:
                auth_data = await self.official_client.get_koreader_auth(creds.username, creds.md5_key)
                auth_username = auth_data.get("username")
                if isinstance(auth_username, str) and auth_username != creds.username:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="KOReader credentials resolved to a different Grimmory username.",
                    )
                user_id = auth_data.get("userId") or auth_data.get("id")
                if isinstance(user_id, str) and user_id.isdigit():
                    user_id = int(user_id)
                linked = None
                from grimmlink_adapter.config import settings

                server = settings.GRIMMORY_BASE_URL.rstrip("/")
                linked = await LinkedAccountStore.get_by_credentials(
                    server, creds.username, creds.md5_key,
                )
                if user_id is None:
                    # Stock Grimmory authenticates the KOReader key but returns
                    # only the username. Reuse an explicit link, or automatically
                    # bind to the same username's already-authenticated Metadata
                    # account after verifying its JWT profile.
                    user_id = int(linked.user_id) if linked is not None else None
                    if user_id is None:
                        from grimmlink_adapter.services.metadata_automation import (
                            MetadataBlocked,
                            metadata_automation,
                        )

                        try:
                            user_id = await metadata_automation.link_reader_account(
                                creds.username, creds.md5_key,
                            )
                        except MetadataBlocked:
                            user_id = None
                elif linked is not None and str(linked.user_id) != str(user_id):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="KOReader and linked Grimmory account identities do not match.",
                    )
                elif linked is None and isinstance(user_id, int) and not isinstance(user_id, bool):
                    # Some Grimmory forks include a user ID in KOReader auth.
                    # If a background Metadata account is already bound to that
                    # same user, establish the Reader link on first use too.
                    from grimmlink_adapter.services.metadata_automation import (
                        MetadataBlocked,
                        metadata_automation,
                    )

                    with suppress(MetadataBlocked):
                        await metadata_automation.link_reader_account(
                            creds.username, creds.md5_key, expected_user_id=int(user_id),
                        )
                if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_501_NOT_IMPLEMENTED,
                        detail=(
                            "Grimmory accepted KOReader authentication but did not return a user ID. "
                            "Bind this same username in the Metadata account setup to enable automatic linking."
                        ),
                    )
                sync_enabled = auth_data.get("syncEnabled", True)
                sync_with_web = auth_data.get("syncWithWebReader", True)
                return GrimmlinkAuthResponse(
                    status="ok",
                    username=creds.username,
                    userId=user_id,
                    syncEnabled=sync_enabled,
                    syncWithWebReader=sync_with_web,
                )
            except (OfficialAuthError, OfficialPermissionError, httpx.HTTPStatusError) as exc:
                status_code = getattr(exc, "status_code", None) or (
                    exc.response.status_code if hasattr(exc, "response") else 401
                )
                if status_code in (401, 403):
                    logger.warning("Upstream Official auth rejected credentials for user %s", creds.username)
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid username or MD5 authentication key.",
                    ) from exc
                logger.error("Upstream Official auth returned unexpected error: %s", status_code)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Upstream Official Grimmory returned status {status_code}.",
                ) from exc
            except (OfficialTransportError, OfficialTimeoutError, OfficialBadResponseError, httpx.RequestError) as exc:
                logger.error("Upstream Official Grimmory unreachable: %s", type(exc).__name__)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Upstream Official Grimmory service is unreachable.",
                ) from exc

        if creds.bearer_token:
            try:
                user_data = await self.official_client.get_current_user(creds.bearer_token)
                user_id = user_data.get("id") or user_data.get("userId")
                username = user_data.get("username")
                if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
                    raise OfficialBadResponseError("Bearer profile response missing a valid user ID")
                if not isinstance(username, str) or not username.strip():
                    raise OfficialBadResponseError("Bearer profile response missing a username")
                return GrimmlinkAuthResponse(
                    status="ok",
                    username=username,
                    userId=user_id,
                    syncEnabled=True,
                    syncWithWebReader=True,
                )
            except (OfficialAuthError, OfficialPermissionError, httpx.HTTPStatusError) as exc:
                status_code = getattr(exc, "status_code", None) or (
                    exc.response.status_code if hasattr(exc, "response") else 401
                )
                if status_code in (401, 403):
                    logger.warning("Upstream Official rejected Bearer token")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired Bearer token.",
                    ) from exc
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Upstream Official Grimmory returned status {status_code}.",
                ) from exc
            except (OfficialTransportError, OfficialTimeoutError, OfficialBadResponseError, httpx.RequestError) as exc:
                logger.error("Upstream Official Grimmory unreachable: %s", type(exc).__name__)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Upstream Official Grimmory service is unreachable.",
                ) from exc

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials required (x-auth-user + x-auth-key or Bearer token).",
        )

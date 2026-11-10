"""Authentication credential extraction from incoming client requests."""

import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from grimmlink_adapter.state.adapter_sessions import AdapterSession, AdapterSessionStore


async def _official_session_credentials(token: str, session: AdapterSession) -> "ClientCredentials":
    from grimmlink_adapter.official.auth import parse_jwt_expiry
    from grimmlink_adapter.official.client import OfficialGrimmoryClient
    from grimmlink_adapter.official.exceptions import (
        OfficialAuthError,
        OfficialClientError,
        OfficialPermissionError,
    )
    from grimmlink_adapter.security.account_locks import credential_refresh_lock

    async with credential_refresh_lock:
        current = await AdapterSessionStore.get(token)
        if current is None or not current.official_access_token:
            raise HTTPException(status_code=401, detail="Adapter session is invalid or expired.")
        session = current
        try:
            async with OfficialGrimmoryClient() as client:
                client.set_jwt_credentials(
                    username=session.username,
                    access_token=session.official_access_token,
                    refresh_token=session.official_refresh_token,
                )
                access = session.official_access_token
                assert access is not None
                refresh = session.official_refresh_token
                expiry = parse_jwt_expiry(access)
                if expiry is not None and expiry < time.time() + 60:
                    if not refresh:
                        raise HTTPException(status_code=401, detail="Grimmory session expired. Sign in again.")
                    updated = await client.refresh_token()
                    access, refresh = updated.token, updated.refreshToken or refresh
                try:
                    profile = await client.get_current_user(access)
                except (OfficialAuthError, OfficialPermissionError):
                    if not refresh:
                        raise
                    updated = await client.refresh_token()
                    access, refresh = updated.token, updated.refreshToken or refresh
                    profile = await client.get_current_user(access)
                if str(profile.get("id", profile.get("userId"))) != str(session.user_id):
                    raise HTTPException(status_code=401, detail="Adapter session identity changed.")
                if access != session.official_access_token or refresh != session.official_refresh_token:
                    try:
                        await AdapterSessionStore.update_official(token, session, access, refresh)
                    except ValueError as exc:
                        raise HTTPException(status_code=401, detail="Adapter session is invalid or expired.") from exc
                return ClientCredentials(username=session.username, bearer_token=access)
        except (OfficialAuthError, OfficialPermissionError) as exc:
            raise HTTPException(status_code=401, detail="Grimmory session expired. Sign in again.") from exc
        except OfficialClientError as exc:
            raise HTTPException(status_code=502, detail="Grimmory authentication is unavailable.") from exc


@dataclass(frozen=True)
class ClientCredentials:
    """Extracted authentication credentials from KOReader GrimmLink request."""

    username: str | None = None
    md5_key: str | None = None
    bearer_token: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.bearer_token or (self.username and self.md5_key))

    def __repr__(self) -> str:
        # Guarantee no secrets in string representation
        return (
            f"ClientCredentials(username={self.username!r}, "
            f"md5_key=[REDACTED] if present, bearer_token=[REDACTED] if present)"
        )


async def get_client_credentials(
    x_auth_user: str | None = Header(None, alias="x-auth-user"),
    x_auth_key: str | None = Header(None, alias="x-auth-key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> ClientCredentials:
    """FastAPI dependency to extract auth credentials without logging them."""
    bearer_token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()

    if bearer_token:
        session = await AdapterSessionStore.get(bearer_token)
        if session is not None:
            if session.official_access_token:
                return await _official_session_credentials(bearer_token, session)
            session_creds = ClientCredentials(username=session.username, md5_key=session.md5_key)
            from grimmlink_adapter.services.request_auth import get_official_bearer

            official_bearer = await get_official_bearer(session_creds)
            if getattr(official_bearer, "user_id", None) != session.user_id:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Adapter session identity changed.")
            return ClientCredentials(
                username=session.username,
                md5_key=session.md5_key,
                bearer_token=str(official_bearer),
            )
    creds = ClientCredentials(username=x_auth_user, md5_key=x_auth_key, bearer_token=bearer_token)
    return creds


async def get_adapter_session(
    authorization: str | None = Header(None, alias="Authorization"),
) -> tuple[str, AdapterSession]:
    """Resolve a live Adapter bearer token for v2 session-only operations."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Adapter bearer token required.")
    token = authorization[7:].strip()
    session = await AdapterSessionStore.get(token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Adapter session is invalid or expired.")
    return token, session


async def require_client_credentials(
    x_auth_user: str | None = Header(None, alias="x-auth-user"),
    x_auth_key: str | None = Header(None, alias="x-auth-key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> ClientCredentials:
    """Dependency that raises 401 Unauthorized if no credentials are provided."""
    creds = await get_client_credentials(
        x_auth_user=x_auth_user,
        x_auth_key=x_auth_key,
        authorization=authorization,
    )
    if not creds.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required authentication credentials (x-auth-user/x-auth-key or Bearer token)",
        )
    return creds


async def require_v2_credentials(
    session: tuple[str, AdapterSession] = Depends(get_adapter_session),
) -> ClientCredentials:
    """Require an Adapter-issued session and resolve its linked Grimmory bearer."""
    token, adapter_session = session
    if adapter_session.official_access_token:
        return await _official_session_credentials(token, adapter_session)
    session_creds = ClientCredentials(
        username=adapter_session.username,
        md5_key=adapter_session.md5_key,
    )
    from grimmlink_adapter.services.request_auth import get_official_bearer

    official_bearer = await get_official_bearer(session_creds)
    if getattr(official_bearer, "user_id", None) != adapter_session.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Adapter session identity changed.")
    return ClientCredentials(
        username=adapter_session.username,
        md5_key=adapter_session.md5_key,
        bearer_token=str(official_bearer),
    )

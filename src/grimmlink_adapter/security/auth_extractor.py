"""Authentication credential extraction from incoming client requests."""

from dataclasses import dataclass

from fastapi import Header, HTTPException, status


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

    creds = ClientCredentials(
        username=x_auth_user,
        md5_key=x_auth_key,
        bearer_token=bearer_token,
    )
    return creds


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

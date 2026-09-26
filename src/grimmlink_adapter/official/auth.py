"""Authentication models, token management, and strict auth mode separation for Official Grimmory."""

import base64
import json
import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AuthMode(StrEnum):
    """Authentication mode strictly partitioned by upstream endpoint domain."""

    JWT = "JWT"
    KOREADER = "KOREADER"
    NONE = "NONE"


def parse_jwt_expiry(token: str) -> float | None:
    """Extract standard 'exp' POSIX timestamp from unverified JWT payload.

    Used proactively to avoid sending requests with known-expired access tokens.
    """
    if not token or not isinstance(token, str):
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        rem = len(payload_b64) % 4
        if rem:
            payload_b64 += "=" * (4 - rem)
        decoded = base64.urlsafe_b64decode(payload_b64)
        data = json.loads(decoded.decode("utf-8"))
        exp = data.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:
        pass
    return None


class JWTAuth(BaseModel):
    """Holds Official Grimmory JWT credentials and tokens.

    Invariants:
    - Secrets (password, access_token, refresh_token) are never displayed in repr/str.
    - Token expiration is tracked to allow proactive or reactive refresh.
    """

    model_config = ConfigDict(extra="ignore")

    username: str
    password: str | None = Field(default=None, repr=False)
    access_token: str | None = Field(default=None, repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    expires_at: float | None = None

    def is_expired(self, buffer_seconds: float = 30.0) -> bool:
        """Check whether the access token is expired or close to expiring."""
        if not self.access_token:
            return True
        if self.expires_at is None:
            return False
        return (time.time() + buffer_seconds) >= self.expires_at

    def update_tokens(
        self,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: float | None = None,
    ) -> None:
        """Update active access/refresh tokens and recalculate expiry."""
        self.access_token = access_token
        if refresh_token:
            self.refresh_token = refresh_token
        self.expires_at = expires_at if expires_at is not None else parse_jwt_expiry(access_token)

    def __repr__(self) -> str:
        tok_state = "[REDACTED]" if self.access_token else "None"
        ref_state = "[REDACTED]" if self.refresh_token else "None"
        pwd_state = "[REDACTED]" if self.password else "None"
        return (
            f"JWTAuth(username={self.username!r}, password={pwd_state}, "
            f"access_token={tok_state}, refresh_token={ref_state}, expires_at={self.expires_at})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class KOReaderAuth(BaseModel):
    """Holds Official Grimmory KOReader header credentials.

    Invariants:
    - md5_key is never displayed in repr/str.
    - Used strictly for /api/koreader/** endpoints.
    """

    model_config = ConfigDict(extra="ignore")

    username: str
    md5_key: str = Field(repr=False)

    def get_headers(self) -> dict[str, str]:
        """Return authentication headers required by Official KOReader endpoints."""
        return {
            "x-auth-user": self.username,
            "x-auth-key": self.md5_key,
        }

    def __repr__(self) -> str:
        return f"KOReaderAuth(username={self.username!r}, md5_key=[REDACTED])"

    def __str__(self) -> str:
        return self.__repr__()


PUBLIC_PATHS: frozenset[str] = frozenset({
    "/api/v1/healthcheck",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
})


def determine_auth_mode_for_path(path: str) -> AuthMode:
    """Determine the required authentication mode based on the URL path.

    Strict Separation:
    - /api/koreader/** -> KOREADER
    - /api/v1/**       -> JWT
    - Other            -> NONE
    """
    clean = "/" + path.lstrip("/")
    if clean.startswith("/api/koreader/"):
        return AuthMode.KOREADER
    if clean.startswith("/api/v1/"):
        return AuthMode.JWT
    return AuthMode.NONE


def validate_auth_separation(path: str, mode: AuthMode) -> None:
    """Enforce strict separation between JWT and KOReader authentication domains.

    Raises ValueError if an inappropriate auth mode is paired with a path.
    """
    clean = "/" + path.lstrip("/")
    if mode == AuthMode.NONE and clean not in PUBLIC_PATHS and (
        clean.startswith("/api/koreader/") or clean.startswith("/api/v1/")
    ):
        raise ValueError(f"Authentication mode NONE cannot be used for protected endpoint '{clean}'")
    if clean.startswith("/api/koreader/") and mode == AuthMode.JWT:
        raise ValueError(
            f"Strict auth separation violation: JWT authentication mode cannot be used for KOReader endpoint '{clean}'"
        )
    if clean.startswith("/api/v1/") and mode == AuthMode.KOREADER:
        raise ValueError(
            f"Strict auth separation violation: KOReader MD5 authentication cannot be used for general API endpoint '{clean}'"
        )

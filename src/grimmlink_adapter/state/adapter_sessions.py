"""Short-lived Adapter bearer sessions for v2 clients.

Session secrets, KOReader MD5 credentials, and management UI JWT credentials
are kept in process memory only. A restart revokes all sessions.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, repr=False)
class AdapterSession:
    username: str
    md5_key: str | None
    user_id: int
    expires_at: datetime
    official_access_token: str | None = None
    official_refresh_token: str | None = None

    def __repr__(self) -> str:
        return (
            f"AdapterSession(username={self.username!r}, credentials=[REDACTED], "
            f"user_id={self.user_id}, expires_at={self.expires_at.isoformat()!r})"
        )


class AdapterSessionStore:
    """In-memory session store keyed by a one-way hash of the bearer token."""

    _sessions: dict[str, AdapterSession] = {}
    _lock = asyncio.Lock()
    DEFAULT_TTL_SECONDS = 8 * 60 * 60

    @classmethod
    async def create(
        cls, username: str, md5_key: str, user_id: int,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> tuple[str, AdapterSession]:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        session = AdapterSession(username, md5_key, user_id, expires_at)
        async with cls._lock:
            cls._purge_expired()
            cls._sessions[hashlib.sha256(token.encode("utf-8")).hexdigest()] = session
        return token, session

    @classmethod
    async def create_official(
        cls, username: str, user_id: int, access_token: str, refresh_token: str | None,
    ) -> tuple[str, AdapterSession]:
        token = secrets.token_urlsafe(32)
        session = AdapterSession(
            username, None, user_id,
            datetime.now(UTC) + timedelta(seconds=cls.DEFAULT_TTL_SECONDS),
            access_token, refresh_token,
        )
        async with cls._lock:
            cls._purge_expired()
            cls._sessions[hashlib.sha256(token.encode("utf-8")).hexdigest()] = session
        return token, session

    @classmethod
    async def update_official(
        cls, token: str, current: AdapterSession, access_token: str, refresh_token: str | None,
    ) -> AdapterSession:
        from dataclasses import replace

        updated = replace(current, official_access_token=access_token, official_refresh_token=refresh_token)
        async with cls._lock:
            key = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if cls._sessions.get(key) != current or current.expires_at <= datetime.now(UTC):
                raise ValueError("Adapter session is invalid or expired")
            cls._sessions[key] = updated
        return updated

    @classmethod
    async def get(cls, token: str) -> AdapterSession | None:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with cls._lock:
            cls._purge_expired()
            return cls._sessions.get(key)

    @classmethod
    async def revoke(cls, token: str) -> bool:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with cls._lock:
            return cls._sessions.pop(key, None) is not None

    @classmethod
    def _purge_expired(cls) -> None:
        now = datetime.now(UTC)
        for key, session in list(cls._sessions.items()):
            if session.expires_at <= now:
                del cls._sessions[key]

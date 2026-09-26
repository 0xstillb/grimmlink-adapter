"""HTTP client for communicating with unmodified Official Grimmory."""

import logging
from typing import Any

import httpx

from grimmlink_adapter.config import settings
from grimmlink_adapter.official.endpoints import (
    OFFICIAL_AUTH_LOGIN,
    OFFICIAL_BOOK_BY_ID,
    OFFICIAL_BOOK_DOWNLOAD,
    OFFICIAL_KOREADER_AUTH,
    OFFICIAL_KOREADER_PROGRESS_HASH,
    OFFICIAL_MAGIC_SHELVES,
    OFFICIAL_SHELVES,
    OFFICIAL_USERS_ME,
)

logger = logging.getLogger(__name__)


class OfficialGrimmoryClient:
    """Async client interacting strictly with Official Grimmory REST APIs.

    Invariants:
    - Zero direct DB access.
    - Official Grimmory code remains unmodified.
    - Sensitive credentials and tokens are redacted before logging.
    - Mutations are disabled or stubbed in Session 00 (scaffold phase).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.GRIMMORY_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.OFFICIAL_CLIENT_TIMEOUT
        self._external_client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)

    async def get_koreader_auth(self, username: str, md5_key: str) -> dict[str, Any]:
        """Authenticate with Official KOReader endpoint using x-auth-user and x-auth-key."""
        headers = {
            "x-auth-user": username,
            "x-auth-key": md5_key,
        }
        async with self._get_client() as client:
            resp = await client.get(OFFICIAL_KOREADER_AUTH, headers=headers)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def login_jwt(self, username: str, password: str) -> dict[str, Any]:
        """Authenticate with Official JWT login endpoint."""
        payload = {"username": username, "password": password}
        async with self._get_client() as client:
            resp = await client.post(OFFICIAL_AUTH_LOGIN, json=payload)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def get_current_user(self, bearer_token: str) -> dict[str, Any]:
        """Fetch current authenticated user profile using Bearer JWT."""
        headers = {"Authorization": f"Bearer {bearer_token}"}
        async with self._get_client() as client:
            resp = await client.get(OFFICIAL_USERS_ME, headers=headers)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def get_koreader_progress(self, book_hash: str, username: str, md5_key: str) -> dict[str, Any]:
        """Fetch KOReader progress for a specific book hash."""
        url = OFFICIAL_KOREADER_PROGRESS_HASH.format(bookHash=book_hash)
        headers = {"x-auth-user": username, "x-auth-key": md5_key}
        async with self._get_client() as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def get_book_by_id(self, book_id: int, bearer_token: str) -> dict[str, Any]:
        """Fetch book metadata by Official book ID."""
        url = OFFICIAL_BOOK_BY_ID.format(bookId=book_id)
        headers = {"Authorization": f"Bearer {bearer_token}"}
        async with self._get_client() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def get_regular_shelves(self, bearer_token: str) -> list[dict[str, Any]]:
        """Fetch list of regular shelves."""
        headers = {"Authorization": f"Bearer {bearer_token}"}
        async with self._get_client() as client:
            resp = await client.get(OFFICIAL_SHELVES, headers=headers)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def get_magic_shelves(self, bearer_token: str, page: int = 0, size: int = 50) -> dict[str, Any]:
        """Fetch paginated list of magic shelves."""
        headers = {"Authorization": f"Bearer {bearer_token}"}
        params = {"page": page, "size": size}
        async with self._get_client() as client:
            resp = await client.get(OFFICIAL_MAGIC_SHELVES, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def download_book_stream(self, book_id: int, bearer_token: str) -> httpx.Response:
        """Stream book file download."""
        url = OFFICIAL_BOOK_DOWNLOAD.format(bookId=book_id)
        headers = {"Authorization": f"Bearer {bearer_token}"}
        client = self._get_client()
        req = client.build_request("GET", url, headers=headers)
        return await client.send(req, stream=True)

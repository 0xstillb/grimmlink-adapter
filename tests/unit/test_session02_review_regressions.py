"""Regressions for Session 02 review findings."""

import json
from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import HTTPException

from grimmlink_adapter.official import (
    OfficialBadResponseError,
    OfficialGrimmoryClient,
    OfficialTimeoutError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_mixed_case_auth_headers_cannot_cross_modes() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json={"userId": 7})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=transport)
    client.set_koreader_credentials("reader", "md5-secret")
    client.set_jwt_credentials("reader", access_token="jwt-secret")

    await client.request("GET", "/api/koreader/users/auth", headers={"AUTHORIZATION": "Bearer leak"})
    await client.request(
        "GET", "/api/v1/users/me", headers={"X-Auth-User": "leak", "X-AUTH-KEY": "leak"}
    )
    await client.request("GET", "/api/v1/healthcheck", headers={"AUTHORIZATION": "Bearer leak"})
    from grimmlink_adapter.official import AuthMode

    await client.request(
        "GET", "/api/v1/healthcheck", auth_mode=AuthMode.NONE,
        headers={"AUTHORIZATION": "Bearer leak", "X-AUTH-KEY": "leak"},
    )

    assert "authorization" not in seen[0]
    assert seen[0]["x-auth-key"] == "md5-secret"
    assert "x-auth-user" not in seen[1] and "x-auth-key" not in seen[1]
    assert seen[1]["authorization"] == "Bearer jwt-secret"
    assert "authorization" not in seen[2]
    assert "authorization" not in seen[3] and "x-auth-key" not in seen[3]


@pytest.mark.asyncio
async def test_switching_jwt_account_discards_previous_tokens() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization", "")))
        if request.url.path.endswith("/login"):
            assert json.loads(request.read()) == {"username": "bob", "password": "bob-password"}
            return httpx.Response(200, json={"token": "bob-token"})
        return httpx.Response(200, json={"id": 2, "username": "bob"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=transport)
    client.set_jwt_credentials(
        "alice", password="alice-password", access_token="alice-token", refresh_token="alice-refresh"
    )
    client.set_jwt_credentials("bob", password="bob-password")
    assert client.jwt_auth is not None
    assert client.jwt_auth.access_token is None
    assert client.jwt_auth.refresh_token is None
    await client.request("GET", "/api/v1/users/me")
    assert seen == [
        ("/api/v1/auth/login", ""),
        ("/api/v1/users/me", "Bearer bob-token"),
    ]


@pytest.mark.asyncio
async def test_explicit_login_switches_account_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "bob-token"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=transport)
    client.set_jwt_credentials("alice", access_token="alice-token", refresh_token="alice-refresh")
    await client.login("bob", "bob-password")
    assert client.jwt_auth is not None
    assert client.jwt_auth.username == "bob"
    assert client.jwt_auth.refresh_token is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["status", "timeout"])
async def test_post_mutation_is_never_retried(failure: str) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("late response")
        return httpx.Response(503, text="unavailable")

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=transport, max_retries=3, backoff_factor=0)
    client.set_jwt_credentials("reader", access_token="token")
    error = OfficialTimeoutError if failure == "timeout" else OfficialBadResponseError
    with pytest.raises(error):
        await client.request("POST", "/api/v1/reading-sessions", json={"bookId": 1})
    assert attempts == 1


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncGenerator[bytes, None]:
        yield b"unavailable"

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_streaming_server_error_is_typed_and_closed() -> None:
    body = TrackedStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, stream=body)

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=transport, max_retries=0)
    client.set_jwt_credentials("reader", access_token="token")
    with pytest.raises(OfficialBadResponseError) as exc:
        await client.request("GET", "/api/v1/books/1/download", stream=True)
    assert exc.value.status_code == 503
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["md5", "bearer"])
@pytest.mark.parametrize("upstream_status", [200, 403])
async def test_auth_service_rejects_missing_identity_and_forbidden(
    credential: str, upstream_status: int
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(upstream_status, json={})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    service = AuthService(OfficialGrimmoryClient(client=transport))
    creds = (
        ClientCredentials(username="reader", md5_key="md5")
        if credential == "md5"
        else ClientCredentials(bearer_token="token")
    )
    with pytest.raises(HTTPException) as exc:
        await service.authorize_client(creds)
    assert exc.value.status_code == (502 if upstream_status == 200 else 401)


@pytest.mark.asyncio
async def test_explicit_bearer_401_does_not_refresh_another_account() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(401, json={"message": "expired"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=transport)
    client.set_jwt_credentials("alice", access_token="alice-token", refresh_token="alice-refresh")
    from grimmlink_adapter.official import OfficialAuthError

    with pytest.raises(OfficialAuthError):
        await client.get_current_user("bob-token")
    assert calls == ["/api/v1/users/me"]


@pytest.mark.asyncio
async def test_download_stream_uses_typed_transport_error() -> None:
    body = TrackedStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, stream=body)

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=transport, max_retries=0)
    with pytest.raises(OfficialBadResponseError) as exc:
        await client.download_book_stream(1, "token")
    assert exc.value.status_code == 503
    assert body.closed


def test_public_request_rejects_none_mode_for_protected_path() -> None:
    from grimmlink_adapter.official import AuthMode, validate_auth_separation

    with pytest.raises(ValueError):
        validate_auth_separation("/api/v1/books", AuthMode.NONE)
    with pytest.raises(ValueError):
        validate_auth_separation("/api/koreader/users/auth", AuthMode.NONE)

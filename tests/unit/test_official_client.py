"""Unit tests for OfficialGrimmoryClient transport layer.

Validates:
- JWT login, refresh, expiry, one safe refresh on 401, loop prevention.
- KOReader routing with x-auth-user + MD5 x-auth-key only.
- Strict auth mode separation.
- Security: secrets masking in logs, exceptions, models, and header redaction.
- TLS verification defaults and explicit opt-in.
- Timeout and exponential backoff retry.
- Typed errors: Auth, Permission, Timeout, Transport, BadResponse.
- Read-only health canaries.
"""

import base64
import json
import logging
import time

import httpx
import pytest

from grimmlink_adapter.official import (
    AuthMode,
    JWTAuth,
    KOReaderAuth,
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialClientError,
    OfficialGrimmoryClient,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
    parse_jwt_expiry,
    redact_headers,
)
from grimmlink_adapter.security.masking import SecretMaskingFilter


def make_jwt(exp: float | int) -> str:
    """Helper to craft an unverified test JWT with a specific expiry claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode("utf-8").rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": "user1", "exp": exp}).encode("utf-8"))
        .decode("utf-8")
        .rstrip("=")
    )
    sig = "mock_sig_12345"
    return f"{header}.{payload}.{sig}"


# -----------------------------------------------------------------------------
# 1. JWT Login Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success() -> None:
    valid_jwt = make_jwt(time.time() + 3600)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/auth/login"
        body = json.loads(request.read())
        assert body["username"] == "admin"
        assert body["password"] == "super_secret_pw"
        return httpx.Response(
            200,
            json={
                "token": valid_jwt,
                "refreshToken": "refresh_token_abc",
                "userId": 42,
                "username": "admin",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    resp = await client.login("admin", "super_secret_pw")
    assert resp.token == valid_jwt
    assert resp.refreshToken == "refresh_token_abc"
    assert resp.userId == 42
    assert resp.username == "admin"

    assert client.jwt_auth is not None
    assert client.jwt_auth.access_token == valid_jwt
    assert client.jwt_auth.refresh_token == "refresh_token_abc"
    assert client.jwt_auth.is_expired() is False


@pytest.mark.asyncio
async def test_login_invalid_credentials_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Bad credentials"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    with pytest.raises(OfficialAuthError) as exc_info:
        await client.login("admin", "wrong_password")
    assert exc_info.value.status_code == 401
    assert "invalid username or password" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_login_missing_credentials() -> None:
    client = OfficialGrimmoryClient()
    with pytest.raises(OfficialAuthError) as exc_info:
        await client.login()
    assert "required" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_login_missing_token_in_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"userId": 1})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    with pytest.raises(OfficialBadResponseError) as exc_info:
        await client.login("admin", "pass")
    assert "missing required token field" in exc_info.value.message.lower()


# -----------------------------------------------------------------------------
# 2. JWT Refresh Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_success() -> None:
    new_jwt = make_jwt(time.time() + 7200)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/auth/refresh"
        body = json.loads(request.read())
        assert body["refreshToken"] == "valid_refresh_token"
        return httpx.Response(
            200,
            json={
                "token": new_jwt,
                "refreshToken": "new_refresh_token",
                "userId": 42,
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials(
        username="admin",
        access_token="old_expired_jwt",
        refresh_token="valid_refresh_token",
    )

    resp = await client.refresh_token()
    assert resp.token == new_jwt
    assert resp.refreshToken == "new_refresh_token"
    assert client.jwt_auth is not None
    assert client.jwt_auth.access_token == new_jwt
    assert client.jwt_auth.refresh_token == "new_refresh_token"


@pytest.mark.asyncio
async def test_refresh_expired_or_invalid_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Refresh token expired"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials(
        username="admin",
        access_token="expired_jwt",
        refresh_token="expired_refresh",
    )

    with pytest.raises(OfficialAuthError) as exc_info:
        await client.refresh_token()
    assert exc_info.value.status_code == 401
    assert "invalid or expired" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_refresh_no_refresh_token() -> None:
    client = OfficialGrimmoryClient()
    client.set_jwt_credentials(username="admin", access_token="jwt_without_refresh")
    with pytest.raises(OfficialAuthError) as exc_info:
        await client.refresh_token()
    assert "no refresh token available" in exc_info.value.message.lower()


# -----------------------------------------------------------------------------
# 3. 401 Retry-Once and Loop Prevention Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_401_triggers_one_refresh_and_succeeds() -> None:
    """A 401 on /api/v1/** must trigger a single refresh and retry the original request."""
    new_jwt = make_jwt(time.time() + 3600)
    users_me_calls = 0
    refresh_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal users_me_calls, refresh_calls
        if request.url.path == "/api/v1/users/me":
            users_me_calls += 1
            if users_me_calls == 1:
                assert request.headers["Authorization"] == "Bearer old_token"
                return httpx.Response(401, json={"message": "Token expired"})
            elif users_me_calls == 2:
                assert request.headers["Authorization"] == f"Bearer {new_jwt}"
                return httpx.Response(200, json={"id": 42, "username": "reader"})
        elif request.url.path == "/api/v1/auth/refresh":
            refresh_calls += 1
            return httpx.Response(200, json={"token": new_jwt, "refreshToken": "new_ref"})
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials(
        username="reader",
        access_token="old_token",
        refresh_token="valid_ref",
    )

    data = await client.request_json("GET", "/api/v1/users/me")
    assert data["id"] == 42
    assert users_me_calls == 2
    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_jwt_401_no_infinite_loop_on_persistent_401() -> None:
    """When a retried request still returns 401, stop immediately and do not loop."""
    new_jwt = make_jwt(time.time() + 3600)
    users_me_calls = 0
    refresh_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal users_me_calls, refresh_calls
        if request.url.path == "/api/v1/users/me":
            users_me_calls += 1
            return httpx.Response(401, json={"message": "Still unauthorized"})
        elif request.url.path == "/api/v1/auth/refresh":
            refresh_calls += 1
            return httpx.Response(200, json={"token": new_jwt, "refreshToken": "new_ref"})
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials(
        username="reader",
        access_token="old_token",
        refresh_token="valid_ref",
    )

    with pytest.raises(OfficialAuthError) as exc_info:
        await client.request_json("GET", "/api/v1/users/me")

    assert exc_info.value.status_code == 401
    assert "persisted after token refresh" in exc_info.value.message
    # Critical invariant: must not call refresh or users/me infinitely
    assert users_me_calls == 2
    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_jwt_401_refresh_fails_aborts_immediately() -> None:
    """If the token refresh call itself fails, abort immediately without retrying the original request."""
    users_me_calls = 0
    refresh_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal users_me_calls, refresh_calls
        if request.url.path == "/api/v1/users/me":
            users_me_calls += 1
            return httpx.Response(401, json={"message": "Unauthorized"})
        elif request.url.path == "/api/v1/auth/refresh":
            refresh_calls += 1
            return httpx.Response(401, json={"message": "Invalid refresh token"})
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials(
        username="reader",
        access_token="bad_token",
        refresh_token="bad_ref",
    )

    with pytest.raises(OfficialAuthError):
        await client.request_json("GET", "/api/v1/users/me")

    assert users_me_calls == 1
    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_jwt_proactive_refresh_when_expired() -> None:
    """When client knows the token is expired by exp claim, refresh proactively before calling."""
    expired_jwt = make_jwt(time.time() - 600)  # Expired 10 min ago
    fresh_jwt = make_jwt(time.time() + 3600)
    refresh_called = False
    endpoint_token_used = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_called, endpoint_token_used
        if request.url.path == "/api/v1/auth/refresh":
            refresh_called = True
            return httpx.Response(200, json={"token": fresh_jwt, "refreshToken": "ref2"})
        elif request.url.path == "/api/v1/users/me":
            endpoint_token_used = request.headers.get("Authorization")
            return httpx.Response(200, json={"id": 1, "username": "admin"})
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials(
        username="admin",
        access_token=expired_jwt,
        refresh_token="ref1",
    )

    assert client.jwt_auth is not None
    assert client.jwt_auth.is_expired() is True

    data = await client.request_json("GET", "/api/v1/users/me")
    assert data["id"] == 1
    assert refresh_called is True
    assert endpoint_token_used == f"Bearer {fresh_jwt}"


# -----------------------------------------------------------------------------
# 4. KOReader Routing & Auth Mode Separation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_koreader_routing_uses_md5_headers_only() -> None:
    """KOReader requests must contain x-auth-user and x-auth-key, never Authorization Bearer."""
    received_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_headers
        received_headers = dict(request.headers)
        return httpx.Response(200, json={"userId": 10, "syncEnabled": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_koreader_credentials("reader_user", "5f4dcc3b5aa765d61d8327deb882cf99")
    # Also set JWT to confirm it is NOT included in KOReader calls
    client.set_jwt_credentials("reader_user", access_token="acc_token_123")

    data = await client.request_json("GET", "/api/koreader/users/auth")
    assert data["userId"] == 10
    assert received_headers["x-auth-user"] == "reader_user"
    assert received_headers["x-auth-key"] == "5f4dcc3b5aa765d61d8327deb882cf99"
    assert "authorization" not in received_headers
    assert "Authorization" not in received_headers


@pytest.mark.asyncio
async def test_jwt_routing_uses_bearer_token_only() -> None:
    """General /api/v1/** requests must contain Authorization Bearer, never x-auth-key."""
    received_headers: dict[str, str] = {}
    valid_jwt = make_jwt(time.time() + 3600)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_headers
        received_headers = dict(request.headers)
        return httpx.Response(200, json={"id": 1, "username": "reader"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials("reader", access_token=valid_jwt)
    client.set_koreader_credentials("reader", "md5_hash_999")

    data = await client.request_json("GET", "/api/v1/users/me")
    assert data["id"] == 1
    assert received_headers["authorization"] == f"Bearer {valid_jwt}"
    assert "x-auth-user" not in received_headers
    assert "x-auth-key" not in received_headers


@pytest.mark.asyncio
async def test_koreader_missing_credentials_raises_auth_error() -> None:
    client = OfficialGrimmoryClient()
    with pytest.raises(OfficialAuthError) as exc_info:
        await client.request("GET", "/api/koreader/users/auth")
    assert "credentials" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_koreader_401_never_attempts_jwt_refresh() -> None:
    """A 401 on /api/koreader/** must fail immediately and NEVER call /api/v1/auth/refresh."""
    refresh_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_called
        if request.url.path == "/api/v1/auth/refresh":
            refresh_called = True
            return httpx.Response(200, json={"token": "t"})
        return httpx.Response(401, json={"message": "Invalid KOReader key"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_koreader_credentials("user1", "bad_md5")
    client.set_jwt_credentials("user1", access_token="jwt", refresh_token="ref")

    with pytest.raises(OfficialAuthError) as exc_info:
        await client.request("GET", "/api/koreader/users/auth")

    assert exc_info.value.status_code == 401
    assert refresh_called is False


def test_strict_auth_mode_separation_violations() -> None:
    """Explicitly attempting cross-domain auth modes raises ValueError."""
    from grimmlink_adapter.official.auth import validate_auth_separation

    with pytest.raises(ValueError) as exc1:
        validate_auth_separation("/api/koreader/users/auth", AuthMode.JWT)
    assert "cannot be used for KOReader endpoint" in str(exc1.value)

    with pytest.raises(ValueError) as exc2:
        validate_auth_separation("/api/v1/books", AuthMode.KOREADER)
    assert "cannot be used for general API endpoint" in str(exc2.value)


# -----------------------------------------------------------------------------
# 5. Security & Redaction Tests
# -----------------------------------------------------------------------------


def test_security_redact_headers() -> None:
    raw_headers = {
        "Authorization": "Bearer super_secret_token_12345",
        "x-auth-key": "098f6bcd4621d373cade4e832627b4f6",
        "Cookie": "session_id=secret_session_data",
        "Set-Cookie": "sid=xyz",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }
    redacted = redact_headers(raw_headers)
    assert redacted["Authorization"] == "Bearer [REDACTED]"
    assert redacted["x-auth-key"] == "[REDACTED]"
    assert redacted["Cookie"] == "[REDACTED]"
    assert redacted["Set-Cookie"] == "[REDACTED]"
    assert redacted["Content-Type"] == "application/json"
    assert redacted["Accept"] == "*/*"


def test_security_exception_and_model_repr_masking() -> None:
    """Exceptions and auth models must never reveal passwords, tokens, or MD5 keys."""
    jwt = JWTAuth(
        username="admin",
        password="plain_password",
        access_token="secret_access_token",
        refresh_token="secret_refresh_token",
    )
    rep_jwt = repr(jwt)
    assert "admin" in rep_jwt
    assert "plain_password" not in rep_jwt
    assert "secret_access_token" not in rep_jwt
    assert "secret_refresh_token" not in rep_jwt
    assert "[REDACTED]" in rep_jwt

    ko = KOReaderAuth(username="reader", md5_key="e10adc3949ba59abbe56e057f20f883e")
    rep_ko = repr(ko)
    assert "reader" in rep_ko
    assert "e10adc3949ba59abbe56e057f20f883e" not in rep_ko
    assert "[REDACTED]" in rep_ko

    err = OfficialClientError(
        "Failed query with token=secret_token_xyz and password=my_password",
        url="http://host/api?x-auth-key=5f4dcc3b5aa765d61d8327deb882cf99",
    )
    rep_err = repr(err)
    str_err = str(err)
    for s in (rep_err, str_err):
        assert "secret_token_xyz" not in s
        assert "my_password" not in s
        assert "5f4dcc3b5aa765d61d8327deb882cf99" not in s
        assert "[REDACTED]" in s


@pytest.mark.asyncio
async def test_security_no_secret_leak_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Logs must be scrubbed by SecretMaskingFilter during request execution."""
    filter_obj = SecretMaskingFilter()
    root = logging.getLogger()
    root.addFilter(filter_obj)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": make_jwt(time.time() + 3600)})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    with caplog.at_level(logging.DEBUG):
        await client.login("admin_user", "very_secret_cleartext_password")

    for record in caplog.records:
        filter_obj.filter(record)
        formatted = record.getMessage()
        assert "very_secret_cleartext_password" not in formatted


# -----------------------------------------------------------------------------
# 6. Timeout, Retry & Backoff Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_error_503_retries_and_succeeds() -> None:
    """Transient 503 returns succeed after backoff retries."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={"status": "ok"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(
        client=mock_client,
        max_retries=3,
        backoff_factor=0.001,  # Sub-millisecond for fast test
    )

    data = await client.request_json("GET", "/api/v1/healthcheck")
    assert data["status"] == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_transient_error_exhaustion_raises_bad_response_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(502, text="Bad Gateway")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(
        client=mock_client,
        max_retries=2,
        backoff_factor=0.001,
    )

    with pytest.raises(OfficialBadResponseError) as exc_info:
        await client.request_json("GET", "/api/v1/healthcheck")

    assert exc_info.value.status_code == 502
    assert attempts == 3  # Initial + 2 retries


@pytest.mark.asyncio
async def test_timeout_and_retry_exhaustion_raises_timeout_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Server took too long")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(
        client=mock_client,
        max_retries=2,
        backoff_factor=0.001,
    )

    with pytest.raises(OfficialTimeoutError) as exc_info:
        await client.request("GET", "/api/v1/healthcheck")

    assert attempts == 3
    assert "timed out" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_network_connect_error_exhaustion_raises_transport_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("Connection refused")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(
        client=mock_client,
        max_retries=2,
        backoff_factor=0.001,
    )

    with pytest.raises(OfficialTransportError) as exc_info:
        await client.request("GET", "/api/v1/healthcheck")

    assert attempts == 3
    assert "connection failed" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_non_transient_400_and_403_do_not_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(403, text="Forbidden")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(
        client=mock_client,
        max_retries=3,
        backoff_factor=0.001,
    )

    with pytest.raises(OfficialPermissionError) as exc_info:
        await client.request("GET", "/api/v1/healthcheck")

    assert exc_info.value.status_code == 403
    assert attempts == 1  # 403 must never retry!


# -----------------------------------------------------------------------------
# 7. Malformed Response Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>Not JSON</body></html>")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    with pytest.raises(OfficialBadResponseError) as exc_info:
        await client.check_health()

    assert "malformed json" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_unexpected_json_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected", "list"])

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    with pytest.raises(OfficialBadResponseError) as exc_info:
        await client.check_health()

    assert "expected json object" in exc_info.value.message.lower()


# -----------------------------------------------------------------------------
# 8. Read-Only Health Canaries Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_health_canary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/healthcheck"
        return httpx.Response(200, json={"status": "UP"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    result = await client.check_health()
    assert result["status"] == "UP"


@pytest.mark.asyncio
async def test_get_app_settings_accepts_explicit_bearer_without_jwt_auth() -> None:
    observed: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers.get("authorization")
        assert request.method == "GET"
        assert request.url.path == "/api/v1/settings"
        return httpx.Response(200, json={"metadataPersistenceSettings": {}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    result = await client.get_app_settings("explicit-token")

    assert result == {"metadataPersistenceSettings": {}}
    assert observed["authorization"] == "Bearer explicit-token"
    assert client.jwt_auth is None


@pytest.mark.asyncio
async def test_read_only_jwt_auth_canary() -> None:
    valid_jwt = make_jwt(time.time() + 3600)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/users/me"
        assert request.headers["Authorization"] == f"Bearer {valid_jwt}"
        return httpx.Response(200, json={"id": 1, "username": "admin"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)
    client.set_jwt_credentials("admin", access_token=valid_jwt)

    result = await client.canary_jwt_auth()
    assert result["username"] == "admin"
    assert result["id"] == 1


@pytest.mark.asyncio
async def test_read_only_koreader_auth_canary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/koreader/users/auth"
        assert request.headers["x-auth-user"] == "kouser"
        assert request.headers["x-auth-key"] == "komd5"
        return httpx.Response(200, json={"userId": 7, "syncEnabled": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    client = OfficialGrimmoryClient(client=mock_client)

    result = await client.canary_koreader_auth("kouser", "komd5")
    assert result["userId"] == 7
    assert result["syncEnabled"] is True


# -----------------------------------------------------------------------------
# 9. TLS Verification & Self-Signed Opt-In Tests
# -----------------------------------------------------------------------------


def test_tls_verify_defaults_and_opt_in(caplog: pytest.LogCaptureFixture) -> None:
    # 1. Default: TLS verify ON
    c1 = OfficialGrimmoryClient()
    assert c1.ssl_verify is True

    # 2. Custom CA bundle
    c2 = OfficialGrimmoryClient(ca_bundle="/certs/custom_ca.pem")
    assert c2.ssl_verify == "/certs/custom_ca.pem"

    # 3. Explicit self-signed opt-out (verify_ssl=False)
    with caplog.at_level(logging.WARNING):
        c3 = OfficialGrimmoryClient(verify_ssl=False)
        assert c3.ssl_verify is False
        assert "TLS verification is explicitly DISABLED" in caplog.text


# -----------------------------------------------------------------------------
# 10. Expiry & Context Manager Lifecycle Tests
# -----------------------------------------------------------------------------


def test_jwt_expiry_parsing() -> None:
    # Valid exp
    future = time.time() + 1000
    token = make_jwt(future)
    parsed = parse_jwt_expiry(token)
    assert parsed is not None
    assert abs(parsed - future) < 1.0

    # Invalid string
    assert parse_jwt_expiry("not_a_jwt") is None
    assert parse_jwt_expiry("") is None


@pytest.mark.asyncio
async def test_client_async_context_manager() -> None:
    async with OfficialGrimmoryClient(base_url="http://localhost:6060") as client:
        assert client.base_url == "http://localhost:6060"
        internal_c = client._get_client()
        assert not internal_c.is_closed
    assert internal_c.is_closed

"""HTTP client for communicating with unmodified Official Grimmory.

Invariants:
- This HTTP client never accesses Grimmory's database; Session 03's separate
  opt-in identity lookup uses a dedicated SELECT-only account.
- Official Grimmory code remains stock and unmodified.
- Strictly separate auth modes:
  * General `/api/v1/**`: JWT Bearer authentication only.
  * KOReader `/api/koreader/**`: x-auth-user + MD5 x-auth-key only.
- Absolute secret masking: passwords, JWT tokens, refresh tokens, and MD5 keys are NEVER logged.
- TLS verification enabled by default; explicit self-signed opt-in only.
- Exponential backoff and retry for transient network/server errors.
- One safe refresh on 401 for JWT requests with strict infinite-loop prevention.
- Read-only health canaries for server, JWT auth, and KOReader auth.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.official import OfficialBulkShelfAssignRequest, OfficialLoginResponse
from grimmlink_adapter.official.auth import (
    PUBLIC_PATHS,
    AuthMode,
    JWTAuth,
    KOReaderAuth,
    determine_auth_mode_for_path,
    parse_jwt_expiry,
    validate_auth_separation,
)
from grimmlink_adapter.official.endpoints import (
    OFFICIAL_APP_SETTINGS,
    OFFICIAL_AUTH_LOGIN,
    OFFICIAL_AUTH_REFRESH,
    OFFICIAL_BOOK_BY_ID,
    OFFICIAL_BOOK_DOWNLOAD,
    OFFICIAL_BOOK_METADATA,
    OFFICIAL_BOOK_SIDECAR_IMPORT,
    OFFICIAL_HEALTHCHECK,
    OFFICIAL_KOREADER_AUTH,
    OFFICIAL_KOREADER_PROGRESS_HASH,
    OFFICIAL_MAGIC_SHELF_BOOKS,
    OFFICIAL_MAGIC_SHELVES,
    OFFICIAL_SHELVES,
    OFFICIAL_SHELVES_ASSIGN,
    OFFICIAL_USERS_ME,
)
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialClientError,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.security.masking import mask_secret, redact_headers

logger = logging.getLogger(__name__)

# Transient HTTP status codes safe to retry
TRANSIENT_STATUS_CODES = frozenset({502, 503, 504})
RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class OfficialGrimmoryClient:
    """Async client interacting strictly with Official Grimmory REST APIs."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        connect_timeout: float | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        verify_ssl: bool | None = None,
        ca_bundle: str | None = None,
        jwt_auth: JWTAuth | None = None,
        koreader_auth: KOReaderAuth | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.GRIMMORY_BASE_URL).rstrip("/")
        self.max_retries = max_retries if max_retries is not None else settings.OFFICIAL_CLIENT_MAX_RETRIES
        self.backoff_factor = (
            backoff_factor if backoff_factor is not None else settings.OFFICIAL_CLIENT_BACKOFF_FACTOR
        )

        # Timeout configuration
        req_timeout = timeout if timeout is not None else settings.OFFICIAL_CLIENT_TIMEOUT
        conn_timeout = (
            connect_timeout if connect_timeout is not None else settings.OFFICIAL_CLIENT_CONNECT_TIMEOUT
        )
        if isinstance(req_timeout, httpx.Timeout):
            self.timeout_config = req_timeout
        else:
            self.timeout_config = httpx.Timeout(req_timeout, connect=conn_timeout)

        # TLS verification configuration: ON by default; explicit opt-in only
        self.ca_bundle = ca_bundle or settings.OFFICIAL_CLIENT_CA_BUNDLE
        resolved_verify = verify_ssl if verify_ssl is not None else settings.OFFICIAL_CLIENT_VERIFY_SSL
        if self.ca_bundle:
            self.ssl_verify: bool | str = self.ca_bundle
        else:
            self.ssl_verify = resolved_verify

        if self.ssl_verify is False:
            logger.warning(
                "TLS verification is explicitly DISABLED for Official Grimmory client. "
                "This is insecure and should only be used for self-signed certificates in development/testing."
            )

        # Auth states
        self.jwt_auth = jwt_auth
        self.koreader_auth = koreader_auth
        self._refresh_lock = asyncio.Lock()
        self._external_client = client
        self._internal_client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        if self._internal_client is None or self._internal_client.is_closed:
            self._internal_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_config,
                verify=self.ssl_verify,
            )
        return self._internal_client

    async def aclose(self) -> None:
        """Close internal HTTP client resources if owned."""
        if self._internal_client is not None and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None

    async def __aenter__(self) -> "OfficialGrimmoryClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    # -------------------------------------------------------------------------
    # Authentication Setup & Credentials Management
    # -------------------------------------------------------------------------

    def set_jwt_credentials(
        self,
        username: str,
        password: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: float | None = None,
    ) -> None:
        """Configure JWT credentials for general /api/v1/** routes."""
        resolved_exp = (
            expires_at
            if expires_at is not None
            else (parse_jwt_expiry(access_token) if access_token else None)
        )
        if self.jwt_auth is None or self.jwt_auth.username != username:
            self.jwt_auth = JWTAuth(
                username=username,
                password=password,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=resolved_exp,
            )
        else:
            self.jwt_auth.username = username
            if password is not None:
                self.jwt_auth.password = password
            if access_token is not None:
                self.jwt_auth.update_tokens(access_token, refresh_token, resolved_exp)

    def set_koreader_credentials(self, username: str, md5_key: str) -> None:
        """Configure KOReader credentials for /api/koreader/** routes."""
        self.koreader_auth = KOReaderAuth(username=username, md5_key=md5_key)

    # -------------------------------------------------------------------------
    # Token Lifecycle & Safe Refresh
    # -------------------------------------------------------------------------

    async def login(
        self, username: str | None = None, password: str | None = None
    ) -> OfficialLoginResponse:
        """Perform JWT login against POST /api/v1/auth/login.

        Never logs credentials. Updates internal JWTAuth tokens and expiry.
        """
        user = username if username is not None else (self.jwt_auth.username if self.jwt_auth else None)
        same_account = self.jwt_auth is not None and self.jwt_auth.username == user
        pwd = password if password is not None else (
            self.jwt_auth.password if same_account and self.jwt_auth is not None else None
        )
        if not user or not pwd:
            raise OfficialAuthError("Username and password are required for JWT login")

        payload = {"username": user, "password": pwd}
        try:
            resp = await self._send_request(
                method="POST",
                path=OFFICIAL_AUTH_LOGIN,
                auth_mode=AuthMode.NONE,
                json=payload,
                _is_401_retry=True,  # Login should never loop on 401
            )
        except OfficialAuthError as exc:
            if exc.status_code == 401:
                raise OfficialAuthError(
                    "Authentication failed: invalid username or password",
                    status_code=401,
                    method="POST",
                    url=OFFICIAL_AUTH_LOGIN,
                ) from exc
            raise

        if resp.status_code == 401:
            raise OfficialAuthError(
                "Authentication failed: invalid username or password",
                status_code=401,
                method="POST",
                url=OFFICIAL_AUTH_LOGIN,
            )

        data = self._parse_json(resp, "POST", OFFICIAL_AUTH_LOGIN)
        token = data.get("token") or data.get("accessToken")
        if not token:
            raise OfficialBadResponseError(
                "Login response missing required token field",
                status_code=resp.status_code,
                method="POST",
                url=OFFICIAL_AUTH_LOGIN,
                response_body=resp.text,
            )

        refresh_token = data.get("refreshToken")
        user_id = data.get("userId")
        uname = data.get("username", user)

        if self.jwt_auth is None or self.jwt_auth.username != user:
            self.jwt_auth = JWTAuth(
                username=user,
                password=pwd,
                access_token=token,
                refresh_token=refresh_token,
            )
        else:
            self.jwt_auth.password = pwd
            self.jwt_auth.update_tokens(token, refresh_token)

        return OfficialLoginResponse(
            token=token,
            refreshToken=refresh_token,
            userId=user_id,
            username=uname,
        )

    async def refresh_token(self) -> OfficialLoginResponse:
        """Refresh JWT access token using POST /api/v1/auth/refresh.

        Never logs refresh token. Updates internal JWTAuth tokens and expiry.
        """
        if not self.jwt_auth or not self.jwt_auth.refresh_token:
            raise OfficialAuthError("Cannot refresh token: no refresh token available")

        payload = {"refreshToken": self.jwt_auth.refresh_token}
        try:
            resp = await self._send_request(
                method="POST",
                path=OFFICIAL_AUTH_REFRESH,
                auth_mode=AuthMode.NONE,
                json=payload,
                _is_401_retry=True,  # Refresh itself should not trigger 401 refresh
            )
        except OfficialAuthError as exc:
            if exc.status_code in (401, 403):
                raise OfficialAuthError(
                    "Refresh token invalid or expired",
                    status_code=exc.status_code,
                    method="POST",
                    url=OFFICIAL_AUTH_REFRESH,
                    response_body=exc.response_body,
                ) from exc
            raise

        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Refresh token invalid or expired",
                status_code=resp.status_code,
                method="POST",
                url=OFFICIAL_AUTH_REFRESH,
                response_body=resp.text,
            )

        data = self._parse_json(resp, "POST", OFFICIAL_AUTH_REFRESH)
        token = data.get("token") or data.get("accessToken")
        if not token:
            raise OfficialBadResponseError(
                "Refresh response missing required token field",
                status_code=resp.status_code,
                method="POST",
                url=OFFICIAL_AUTH_REFRESH,
                response_body=resp.text,
            )

        new_refresh = data.get("refreshToken") or self.jwt_auth.refresh_token
        self.jwt_auth.update_tokens(token, new_refresh)

        return OfficialLoginResponse(
            token=token,
            refreshToken=new_refresh,
            userId=data.get("userId"),
            username=data.get("username", self.jwt_auth.username),
        )

    async def _ensure_valid_jwt(self) -> str:
        """Ensure an unexpired JWT access token is available, refreshing proactively if needed."""
        if self.jwt_auth is None:
            raise OfficialAuthError("JWT authentication credentials not configured")

        if self.jwt_auth.is_expired():
            async with self._refresh_lock:
                # Double-check inside lock to handle race conditions
                if self.jwt_auth.is_expired():
                    if self.jwt_auth.refresh_token:
                        logger.debug("Access token expired; refreshing proactively via refresh token")
                        await self.refresh_token()
                    elif self.jwt_auth.username and self.jwt_auth.password:
                        logger.debug("Access token expired; re-authenticating via credentials")
                        await self.login()
                    else:
                        raise OfficialAuthError(
                            "Access token is expired and no refresh token or password is available"
                        )

        if not self.jwt_auth.access_token:
            raise OfficialAuthError("No access token available")
        return self.jwt_auth.access_token

    # -------------------------------------------------------------------------
    # Core Transport: Request Dispatching, Redaction, Retry & Backoff
    # -------------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        auth_mode: AuthMode | None = None,
        json: Any = None,
        data: Any = None,
        params: Any = None,
        headers: dict[str, str] | None = None,
        files: Any = None,
        stream: bool = False,
        _is_401_retry: bool = False,
    ) -> httpx.Response:
        """Dispatch an HTTP request to Official Grimmory with strict auth separation and 401 retry-once.

        Invariants:
        - /api/koreader/** uses x-auth-user + MD5 key only.
        - /api/v1/** uses JWT Bearer only.
        - Redacts all sensitive headers before logging.
        - Performs exactly one safe token refresh on 401 for JWT routes; stops immediately on second 401.
        """
        resolved_mode = auth_mode if auth_mode is not None else determine_auth_mode_for_path(path)
        validate_auth_separation(path, resolved_mode)

        req_headers: dict[str, str] = dict(httpx.Headers(headers or {}))
        clean_path = "/" + path.lstrip("/")
        if clean_path in PUBLIC_PATHS:
            for name in ("authorization", "x-auth-user", "x-auth-key"):
                req_headers.pop(name, None)

        # Strict auth mode header injection and contamination prevention
        if resolved_mode == AuthMode.KOREADER:
            # Strip any accidental Bearer token
            req_headers.pop("authorization", None)

            if not self.koreader_auth:
                raise OfficialAuthError(
                    f"KOReader credentials (x-auth-user and x-auth-key) are required for '{path}'"
                )
            req_headers.update(self.koreader_auth.get_headers())

        elif resolved_mode == AuthMode.JWT:
            # Strip any accidental KOReader headers
            req_headers.pop("x-auth-user", None)
            req_headers.pop("x-auth-key", None)

            if clean_path not in PUBLIC_PATHS:
                access_token = await self._ensure_valid_jwt()
                req_headers["authorization"] = f"Bearer {access_token}"

        return await self._send_request(
            method=method,
            path=path,
            auth_mode=resolved_mode,
            json=json,
            data=data,
            params=params,
            headers=req_headers,
            files=files,
            stream=stream,
            _is_401_retry=_is_401_retry,
        )

    async def _send_request(
        self,
        method: str,
        path: str,
        *,
        auth_mode: AuthMode,
        json: Any = None,
        data: Any = None,
        params: Any = None,
        headers: dict[str, str] | None = None,
        files: Any = None,
        stream: bool = False,
        _is_401_retry: bool = False,
    ) -> httpx.Response:
        """Internal worker sending request with exponential backoff and typed error handling."""
        client = self._get_client()
        clean_path = "/" + path.lstrip("/")
        url = clean_path

        # Max attempts = 1 initial + max_retries
        total_attempts = 1 + max(0, self.max_retries) if method.upper() in RETRYABLE_METHODS else 1
        last_exception: Exception | None = None

        for attempt in range(1, total_attempts + 1):
            try:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "Sending %s %s (attempt %d/%d, headers=%s)",
                        method.upper(),
                        mask_secret(url),
                        attempt,
                        total_attempts,
                        redact_headers(headers),
                    )

                req = client.build_request(
                    method=method.upper(),
                    url=url,
                    json=json,
                    data=data,
                    params=params,
                    headers=headers,
                    files=files,
                )

                if stream:
                    resp = await client.send(req, stream=True)
                else:
                    resp = await client.send(req)

                # Error bodies must be consumed and streaming connections closed before
                # inspecting text, raising, or retrying.
                if stream and resp.status_code >= 400:
                    await resp.aread()
                    await resp.aclose()

                # Retry transient failures only for methods safe to replay.
                if resp.status_code in TRANSIENT_STATUS_CODES:
                    if attempt < total_attempts:
                        delay = self.backoff_factor * (2 ** (attempt - 1))
                        logger.warning(
                            "Transient %d from %s %s; retrying in %.2fs (attempt %d/%d)",
                            resp.status_code,
                            method.upper(),
                            mask_secret(url),
                            delay,
                            attempt,
                            total_attempts,
                        )
                        await resp.aclose()
                        await asyncio.sleep(delay)
                        continue
                    raise OfficialBadResponseError(
                        f"Upstream returned HTTP {resp.status_code} after {total_attempts} attempts",
                        status_code=resp.status_code,
                        method=method,
                        url=url,
                        response_body=resp.text,
                    )

                # Handle 403 Forbidden
                if resp.status_code == 403:
                    raise OfficialPermissionError(
                        f"Access forbidden (HTTP 403) for {method.upper()} {url}",
                        status_code=403,
                        method=method,
                        url=url,
                        response_body=resp.text,
                    )

                # Handle 401 Unauthorized
                if resp.status_code == 401:
                    # Explicit bearer helpers and KOReader endpoints never refresh
                    # an unrelated JWT stored on this client.
                    if auth_mode != AuthMode.JWT:
                        raise OfficialAuthError(
                            "Authentication failed (HTTP 401 Unauthorized)",
                            status_code=401,
                            method=method,
                            url=url,
                            response_body=resp.text,
                        )

                    # Public endpoints should not trigger refresh
                    if clean_path in PUBLIC_PATHS:
                        raise OfficialAuthError(
                            "Authentication rejected (HTTP 401 Unauthorized)",
                            status_code=401,
                            method=method,
                            url=url,
                            response_body=resp.text,
                        )

                    # JWT endpoint 401: One safe refresh
                    if _is_401_retry:
                        # Infinite refresh loop prevention: already retried once!
                        raise OfficialAuthError(
                            "Authentication failed: 401 Unauthorized persisted after token refresh",
                            status_code=401,
                            method=method,
                            url=url,
                            response_body=resp.text,
                        )

                    logger.info(
                        "Received 401 on JWT endpoint %s; performing one safe token refresh",
                        mask_secret(url),
                    )
                    async with self._refresh_lock:
                        try:
                            if self.jwt_auth and self.jwt_auth.refresh_token:
                                await self.refresh_token()
                            elif self.jwt_auth and self.jwt_auth.username and self.jwt_auth.password:
                                await self.login()
                            else:
                                raise OfficialAuthError(
                                    "Cannot refresh: no refresh token or credentials configured",
                                    status_code=401,
                                    method=method,
                                    url=url,
                                )
                        except OfficialClientError:
                            raise
                        except Exception as exc:
                            raise OfficialAuthError(
                                f"Failed to refresh token on 401: {exc}",
                                status_code=401,
                                method=method,
                                url=url,
                            ) from exc

                    # Update header with refreshed token and retry ONCE
                    new_token = self.jwt_auth.access_token if self.jwt_auth else None
                    new_headers = dict(httpx.Headers(headers or {}))
                    if new_token:
                        new_headers["authorization"] = f"Bearer {new_token}"

                    return await self._send_request(
                        method=method,
                        path=path,
                        auth_mode=auth_mode,
                        json=json,
                        data=data,
                        params=params,
                        headers=new_headers,
                        stream=stream,
                        _is_401_retry=True,
                    )

                # Return successful or other 2xx/3xx/4xx response
                return resp

            except httpx.TimeoutException as exc:
                last_exception = exc
                if attempt < total_attempts:
                    delay = self.backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "Request timeout on %s %s (%s); retrying in %.2fs (attempt %d/%d)",
                        method.upper(),
                        mask_secret(url),
                        type(exc).__name__,
                        delay,
                        attempt,
                        total_attempts,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise OfficialTimeoutError(
                    f"Request timed out after {total_attempts} attempts: {type(exc).__name__}",
                    method=method,
                    url=url,
                ) from exc

            except (httpx.NetworkError, httpx.ConnectError) as exc:
                last_exception = exc
                if attempt < total_attempts:
                    delay = self.backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "Network error on %s %s (%s); retrying in %.2fs (attempt %d/%d)",
                        method.upper(),
                        mask_secret(url),
                        type(exc).__name__,
                        delay,
                        attempt,
                        total_attempts,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise OfficialTransportError(
                    f"Transport connection failed after {total_attempts} attempts: {type(exc).__name__}",
                    method=method,
                    url=url,
                ) from exc

        if last_exception:
            raise OfficialTransportError(
                f"Request failed after {total_attempts} attempts: {last_exception}",
                method=method,
                url=url,
            ) from last_exception

        raise OfficialTransportError(f"Request failed without response after {total_attempts} attempts", method=method, url=url)

    def _parse_json(self, resp: httpx.Response, method: str, url: str) -> dict[str, Any]:
        """Safely parse JSON response body with typed bad-response errors."""
        try:
            parsed = resp.json()
            if not isinstance(parsed, dict):
                raise OfficialBadResponseError(
                    f"Expected JSON object response, got {type(parsed).__name__}",
                    status_code=resp.status_code,
                    method=method,
                    url=url,
                    response_body=resp.text,
                )
            return parsed
        except (json.JSONDecodeError, ValueError) as exc:
            raise OfficialBadResponseError(
                "Malformed JSON response from upstream",
                status_code=resp.status_code,
                method=method,
                url=url,
                response_body=resp.text,
            ) from exc

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        auth_mode: AuthMode | None = None,
        json: Any = None,
        data: Any = None,
        params: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Execute request and return parsed JSON, raising typed errors for failures."""
        resp = await self.request(
            method=method,
            path=path,
            auth_mode=auth_mode,
            json=json,
            data=data,
            params=params,
            headers=headers,
        )
        if resp.status_code >= 400:
            raise OfficialBadResponseError(
                f"Upstream returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                method=method,
                url=path,
                response_body=resp.text,
            )
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OfficialBadResponseError(
                "Malformed JSON response from upstream",
                status_code=resp.status_code,
                method=method,
                url=path,
                response_body=resp.text,
            ) from exc

    # -------------------------------------------------------------------------
    # Read-Only Health Canaries
    # -------------------------------------------------------------------------

    async def check_health(self) -> dict[str, Any]:
        """Probe Official Grimmory healthcheck endpoint (GET /api/v1/healthcheck).

        Read-only health canary: zero side-effects, zero mutations.
        """
        resp = await self.request("GET", OFFICIAL_HEALTHCHECK, auth_mode=AuthMode.NONE)
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Healthcheck returned non-200 status {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_HEALTHCHECK,
                response_body=resp.text,
            )
        return self._parse_json(resp, "GET", OFFICIAL_HEALTHCHECK)

    async def canary_jwt_auth(self) -> dict[str, Any]:
        """Probe Official Grimmory user profile using Bearer JWT (GET /api/v1/users/me).

        Read-only health canary: verifies JWT token validity without mutations.
        """
        resp = await self.request("GET", OFFICIAL_USERS_ME, auth_mode=AuthMode.JWT)
        if resp.status_code != 200:
            raise OfficialAuthError(
                f"JWT auth canary failed with HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_USERS_ME,
                response_body=resp.text,
            )
        return self._parse_json(resp, "GET", OFFICIAL_USERS_ME)

    async def canary_koreader_auth(
        self, username: str | None = None, md5_key: str | None = None
    ) -> dict[str, Any]:
        """Probe Official Grimmory KOReader auth endpoint (GET /api/koreader/users/auth).

        Read-only health canary: verifies x-auth-user + MD5 x-auth-key without mutations.
        """
        if username and md5_key:
            self.set_koreader_credentials(username, md5_key)

        resp = await self.request("GET", OFFICIAL_KOREADER_AUTH, auth_mode=AuthMode.KOREADER)
        if resp.status_code != 200:
            raise OfficialAuthError(
                f"KOReader auth canary failed with HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_KOREADER_AUTH,
                response_body=resp.text,
            )
        return self._parse_json(resp, "GET", OFFICIAL_KOREADER_AUTH)

    # -------------------------------------------------------------------------
    # Route Helper Methods (Preserving Contract & Scaffold Signatures)
    # -------------------------------------------------------------------------

    async def get_koreader_auth(self, username: str, md5_key: str) -> dict[str, Any]:
        """Authenticate with Official KOReader endpoint using x-auth-user and x-auth-key."""
        return await self.canary_koreader_auth(username, md5_key)

    async def login_jwt(self, username: str, password: str) -> dict[str, Any]:
        """Authenticate with Official JWT login endpoint."""
        res = await self.login(username, password)
        return res.model_dump()

    async def get_current_user(self, bearer_token: str) -> dict[str, Any]:
        """Fetch current authenticated user profile using Bearer JWT."""
        headers = {"Authorization": f"Bearer {bearer_token}"}
        resp = await self._send_request(
            method="GET",
            path=OFFICIAL_USERS_ME,
            auth_mode=AuthMode.NONE,
            headers=headers,
        )
        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Invalid or expired Bearer token",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_USERS_ME,
                response_body=resp.text,
            )
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Failed to fetch user profile: HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_USERS_ME,
                response_body=resp.text,
            )
        return self._parse_json(resp, "GET", OFFICIAL_USERS_ME)

    async def get_koreader_progress(
        self, book_hash: str, username: str, md5_key: str
    ) -> dict[str, Any]:
        """Fetch KOReader progress for a specific book hash."""
        url = OFFICIAL_KOREADER_PROGRESS_HASH.format(bookHash=book_hash)
        self.set_koreader_credentials(username, md5_key)
        resp = await self.request("GET", url, auth_mode=AuthMode.KOREADER)
        if resp.status_code == 404:
            return {}
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Failed to fetch progress: HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=url,
                response_body=resp.text,
            )
        return self._parse_json(resp, "GET", url)

    async def get_book_by_id(self, book_id: int, bearer_token: str) -> dict[str, Any]:
        """Fetch book metadata by Official book ID."""
        url = OFFICIAL_BOOK_BY_ID.format(bookId=book_id)
        headers = {"Authorization": f"Bearer {bearer_token}"}
        resp = await self._send_request(
            method="GET",
            path=url,
            auth_mode=AuthMode.NONE,
            headers=headers,
        )
        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Unauthorized access to book",
                status_code=resp.status_code,
                method="GET",
                url=url,
                response_body=resp.text,
            )
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Failed to fetch book: HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=url,
                response_body=resp.text,
            )
        return self._parse_json(resp, "GET", url)

    async def update_book_metadata(
        self,
        book_id: int,
        bearer_token: str,
        payload: dict[str, Any],
        *,
        merge_categories: bool = False,
        replace_mode: str = "REPLACE_WHEN_PROVIDED",
    ) -> dict[str, Any]:
        """Update metadata using Official's typed metadata endpoint."""
        url = OFFICIAL_BOOK_METADATA.format(bookId=book_id)
        headers = {"Authorization": f"Bearer {bearer_token}"}
        resp = await self._send_request(
            method="PUT", path=url, auth_mode=AuthMode.NONE, headers=headers,
            params={"mergeCategories": str(merge_categories).lower(), "replaceMode": replace_mode},
            json=payload,
        )
        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Unauthorized metadata update", status_code=resp.status_code,
                method="PUT", url=url, response_body=resp.text,
            )
        if resp.status_code >= 400:
            raise OfficialBadResponseError(
                f"Metadata update returned HTTP {resp.status_code}",
                status_code=resp.status_code, method="PUT", url=url, response_body=resp.text,
            )
        return self._parse_json(resp, "PUT", url) if resp.content else {}

    async def get_app_settings(self, bearer_token: str) -> dict[str, Any]:
        """Read Official persistence settings before any metadata write."""
        url = OFFICIAL_APP_SETTINGS
        headers = {"Authorization": f"Bearer {bearer_token}"}
        # This is an explicit caller token, like get_book_by_id; do not route it
        # through JWTAuth or refresh a different account stored on this client.
        resp = await self._send_request(
            method="GET", path=url, auth_mode=AuthMode.NONE, headers=headers,
        )
        if resp.status_code >= 400:
            raise OfficialBadResponseError(
                f"Application settings returned HTTP {resp.status_code}",
                status_code=resp.status_code, method="GET", url=url,
                response_body=resp.text,
            )
        result = self._parse_json(resp, "GET", url)
        if not isinstance(result, dict):
            raise OfficialBadResponseError(
                "Application settings response is not an object",
                method="GET", url=url,
            )
        return result

    async def import_book_sidecar(self, book_id: int, bearer_token: str) -> None:
        """Ask Official to import the adjacent sidecar for this exact book."""
        url = OFFICIAL_BOOK_SIDECAR_IMPORT.format(bookId=book_id)
        resp = await self._send_request(
            method="POST", path=url, auth_mode=AuthMode.NONE,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        if resp.status_code >= 400:
            raise OfficialBadResponseError(
                f"Sidecar import returned HTTP {resp.status_code}",
                status_code=resp.status_code, method="POST", url=url,
                response_body=resp.text,
            )

    async def get_regular_shelves(self, bearer_token: str) -> list[dict[str, Any]]:
        """Fetch list of regular shelves."""
        headers = {"Authorization": f"Bearer {bearer_token}"}
        resp = await self._send_request(
            method="GET",
            path=OFFICIAL_SHELVES,
            auth_mode=AuthMode.NONE,
            headers=headers,
        )
        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Unauthorized access to shelves",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_SHELVES,
                response_body=resp.text,
            )
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Failed to fetch regular shelves: HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_SHELVES,
                response_body=resp.text,
            )
        data = self._parse_json(resp, "GET", OFFICIAL_SHELVES)
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            return data
        raise OfficialBadResponseError(
            "Expected list of shelf objects",
            status_code=resp.status_code,
            method="GET",
            url=OFFICIAL_SHELVES,
            response_body=resp.text,
        )

    async def get_shelf_books(self, shelf_id: int, bearer_token: str) -> list[dict[str, Any]]:
        """Fetch the complete unpaginated regular shelf book list."""
        url = f"{OFFICIAL_SHELVES}/{shelf_id}/books"
        resp = await self._send_request(
            method="GET", path=url, auth_mode=AuthMode.NONE,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Unauthorized access to shelf books", status_code=resp.status_code,
                method="GET", url=url, response_body=resp.text,
            )
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Failed to fetch shelf books: HTTP {resp.status_code}",
                status_code=resp.status_code, method="GET", url=url, response_body=resp.text,
            )
        data = self._parse_json(resp, "GET", url)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise OfficialBadResponseError(
                "Expected list of shelf books", status_code=resp.status_code, method="GET", url=url,
            )
        return data

    async def get_magic_shelves(self, bearer_token: str) -> list[dict[str, Any]]:
        """Fetch the Official magic shelf summaries."""
        headers = {"Authorization": f"Bearer {bearer_token}"}
        resp = await self._send_request(
            method="GET",
            path=OFFICIAL_MAGIC_SHELVES,
            auth_mode=AuthMode.NONE,
            headers=headers,
        )
        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Unauthorized access to magic shelves",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_MAGIC_SHELVES,
                response_body=resp.text,
            )
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Failed to fetch magic shelves: HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=OFFICIAL_MAGIC_SHELVES,
                response_body=resp.text,
            )
        data = self._parse_json(resp, "GET", OFFICIAL_MAGIC_SHELVES)
        if not isinstance(data, list):
            raise OfficialBadResponseError(
                "Expected list of magic shelves",
                status_code=resp.status_code, method="GET", url=OFFICIAL_MAGIC_SHELVES,
            )
        return data

    async def get_magic_shelf_books(
        self, shelf_id: int, bearer_token: str, page: int = 0, size: int = 100,
    ) -> dict[str, Any]:
        """Fetch one page of books belonging to a rule-derived magic shelf."""
        url = OFFICIAL_MAGIC_SHELF_BOOKS.format(shelfId=shelf_id)
        resp = await self._send_request(
            method="GET", path=url, auth_mode=AuthMode.NONE,
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={"page": page, "size": size},
        )
        if resp.status_code in (401, 403):
            raise OfficialAuthError(
                "Unauthorized access to magic shelf books", status_code=resp.status_code,
                method="GET", url=url, response_body=resp.text,
            )
        if resp.status_code != 200:
            raise OfficialBadResponseError(
                f"Failed to fetch magic shelf books: HTTP {resp.status_code}",
                status_code=resp.status_code, method="GET", url=url, response_body=resp.text,
            )
        result = self._parse_json(resp, "GET", url)
        if not isinstance(result, dict):
            raise OfficialBadResponseError(
                "Expected paginated magic shelf response", status_code=resp.status_code,
                method="GET", url=url,
            )
        return result

    async def assign_shelves_to_books(
        self,
        book_ids: list[int],
        shelves_to_assign: list[int],
        shelves_to_unassign: list[int],
        bearer_token: str,
    ) -> Any:
        """Apply a regular shelf membership mutation through Official's bulk endpoint."""
        url = OFFICIAL_SHELVES_ASSIGN
        payload = OfficialBulkShelfAssignRequest(
            bookIds=book_ids,
            shelvesToAssign=shelves_to_assign,
            shelvesToUnassign=shelves_to_unassign,
        ).model_dump()
        resp = await self._send_request(
            method="POST", path=url, auth_mode=AuthMode.NONE,
            headers={"Authorization": f"Bearer {bearer_token}"}, json=payload,
        )
        if resp.status_code == 401:
            raise OfficialAuthError(
                "Unauthorized shelf mutation", status_code=401, method="POST", url=url,
                response_body=resp.text,
            )
        if resp.status_code == 403:
            raise OfficialPermissionError(
                "Shelf mutation forbidden", status_code=403, method="POST", url=url,
                response_body=resp.text,
            )
        if resp.status_code >= 400:
            raise OfficialBadResponseError(
                f"Shelf mutation returned HTTP {resp.status_code}", status_code=resp.status_code,
                method="POST", url=url, response_body=resp.text,
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OfficialBadResponseError(
                "Malformed shelf mutation response", status_code=resp.status_code,
                method="POST", url=url, response_body=resp.text,
            ) from exc

    async def download_book_stream(self, book_id: int, bearer_token: str) -> httpx.Response:
        """Stream book file download."""
        url = OFFICIAL_BOOK_DOWNLOAD.format(bookId=book_id)
        headers = {"Authorization": f"Bearer {bearer_token}"}
        resp = await self._send_request(
            method="GET",
            path=url,
            auth_mode=AuthMode.NONE,
            headers=headers,
            stream=True,
        )
        if resp.status_code != 200:
            await resp.aclose()
            raise OfficialBadResponseError(
                f"Book download returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                method="GET",
                url=url,
            )
        return resp

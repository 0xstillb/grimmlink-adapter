"""Typed exceptions for the Official Grimmory HTTP client and transport layer.

Security Invariant:
Exception messages, URLs, and representations must NEVER expose plaintext credentials,
passwords, JWT tokens, refresh tokens, or MD5 authentication keys.
"""

from grimmlink_adapter.security.masking import mask_secret


class OfficialClientError(Exception):
    """Base exception for all errors encountered when interacting with Official Grimmory."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        method: str | None = None,
        url: str | None = None,
        response_body: str | None = None,
    ) -> None:
        clean_msg = mask_secret(message)
        super().__init__(clean_msg)
        self.message = clean_msg
        self.status_code = status_code
        self.method = method.upper() if method else None
        self.url = mask_secret(url) if url else None
        self.response_body = mask_secret(response_body) if response_body else None

    def __repr__(self) -> str:
        parts = [f"message={self.message!r}"]
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        if self.method:
            parts.append(f"method={self.method!r}")
        if self.url:
            parts.append(f"url={self.url!r}")
        return f"{self.__class__.__name__}({', '.join(parts)})"

    def __str__(self) -> str:
        prefix = f"[{self.status_code}] " if self.status_code is not None else ""
        loc = f" for {self.method} {self.url}" if (self.method and self.url) else ""
        return f"{prefix}{self.message}{loc}"


class OfficialAuthError(OfficialClientError):
    """Raised when authentication fails (HTTP 401, expired tokens, or invalid credentials)."""


class OfficialPermissionError(OfficialClientError):
    """Raised when access is forbidden (HTTP 403, insufficient permissions)."""


class OfficialTimeoutError(OfficialClientError):
    """Raised when a request to Official Grimmory times out."""


class OfficialTransportError(OfficialClientError):
    """Raised when low-level network/transport connectivity fails (e.g., connection refused, DNS, TLS failure)."""


class OfficialBadResponseError(OfficialClientError):
    """Raised when upstream returns malformed JSON, unexpected 5xx server errors, or invalid DTO payloads."""

"""Logging filter and utilities to guarantee secrets are never logged.

Non-negotiable rule: Passwords, tokens, Bearer headers, and MD5 keys must never
leak into logs at any log level (DEBUG through CRITICAL).
"""

import logging
import re
from collections.abc import Mapping
from typing import Any

# Regular expressions targeting sensitive patterns in text/JSON
SENSITIVE_PATTERNS = [
    # Authorization header (Bearer / Basic)
    (re.compile(r"(Authorization\s*[:=]\s*(?:Bearer|Basic)\s+)[^\s,'\"]+", re.IGNORECASE), r"\1[REDACTED]"),
    # GrimmLink MD5 key header
    (re.compile(r"((?:x-auth-key|auth_key|x_auth_key)\s*[:=]\s*)[^\s,'\"]+", re.IGNORECASE), r"\1[REDACTED]"),
    # JSON password, token fields (snake_case and camelCase)
    (
        re.compile(
            r'("(?:password|access_token|accessToken|refresh_token|refreshToken|token|secret|key|md5_key|x-auth-key)"\s*:\s*")[^"]+(")',
            re.IGNORECASE,
        ),
        r'\1[REDACTED]\2',
    ),
    # Key-value pairs in query strings or strings (e.g. password=xyz)
    (
        re.compile(
            r"((?:password|access_token|accessToken|refresh_token|refreshToken|token|secret|key|x-auth-key|x_auth_key)\s*=\s*)[^\s&,'\"]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
    # 32-character hexadecimal MD5 auth tokens if preceded by key-like indicators
    (
        re.compile(r"((?:md5|auth_hash|key_hash)\s*[:=]\s*)[a-fA-F0-9]{32}", re.IGNORECASE),
        r"\1[REDACTED]",
    ),
]

SENSITIVE_HEADER_KEYS = frozenset({
    "authorization",
    "x-auth-key",
    "cookie",
    "set-cookie",
    "x-api-key",
    "proxy-authorization",
})


def redact_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Return a dictionary of headers with sensitive values masked."""
    if not headers:
        return {}
    redacted: dict[str, str] = {}
    for k, v in headers.items():
        k_lower = str(k).lower()
        val_str = str(v)
        if k_lower in SENSITIVE_HEADER_KEYS or any(
            term in k_lower for term in ("token", "secret", "auth-key", "password")
        ):
            if k_lower == "authorization" and val_str.lower().startswith("bearer "):
                redacted[str(k)] = "Bearer [REDACTED]"
            else:
                redacted[str(k)] = "[REDACTED]"
        else:
            redacted[str(k)] = val_str
    return redacted


def mask_secret(text: str) -> str:
    """Mask any sensitive credentials within a string."""
    if not text:
        return text
    result = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class SecretMaskingFilter(logging.Filter):
    """Logging filter that scrubs sensitive credentials from all log records.

    Formats message arguments first, then scrubs all sensitive credentials across
    the entire formatted string, and empties record.args to eliminate any leak
    of raw argument tuples or dicts to stderr or subsequent handlers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            formatted = record.getMessage()
        except Exception:
            # Fallback in case argument formatting fails
            formatted = f"{record.msg} args={record.args}"

        record.msg = mask_secret(formatted)
        record.args = ()
        return True


def setup_secure_logging(level_name: str = "INFO") -> None:
    """Configure root logger with the SecretMaskingFilter attached to all handlers."""
    numeric_level = getattr(logging, level_name.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Attach masking filter to root
    masking_filter = SecretMaskingFilter()
    root_logger.addFilter(masking_filter)

    # Attach to existing handlers
    for handler in root_logger.handlers:
        handler.addFilter(masking_filter)

    # Ensure at least one stdout handler exists
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(masking_filter)
        root_logger.addHandler(handler)

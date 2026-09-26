"""Logging filter and utilities to guarantee secrets are never logged.

Non-negotiable rule: Passwords, tokens, Bearer headers, and MD5 keys must never
leak into logs at any log level (DEBUG through CRITICAL).
"""

import logging
import re

# Regular expressions targeting sensitive patterns in text/JSON
SENSITIVE_PATTERNS = [
    # Authorization header (Bearer / Basic)
    (re.compile(r"(Authorization\s*[:=]\s*(?:Bearer|Basic)\s+)[^\s,'\"]+", re.IGNORECASE), r"\1[REDACTED]"),
    # GrimmLink MD5 key header
    (re.compile(r"((?:x-auth-key|auth_key|x_auth_key)\s*[:=]\s*)[^\s,'\"]+", re.IGNORECASE), r"\1[REDACTED]"),
    # JSON password, token fields
    (
        re.compile(r'("(?:password|access_token|refresh_token|token|secret|key)"\s*:\s*")[^"]+(")', re.IGNORECASE),
        r'\1[REDACTED]\2',
    ),
    # Key-value pairs in query strings or strings (e.g. password=xyz)
    (
        re.compile(r"((?:password|access_token|refresh_token|x-auth-key)\s*=\s*)[^\s&,'\"]+", re.IGNORECASE),
        r"\1[REDACTED]",
    ),
    # 32-character hexadecimal MD5 auth tokens if preceded by key-like indicators
    (
        re.compile(r"((?:md5|auth_hash|key_hash)\s*[:=]\s*)[a-fA-F0-9]{32}", re.IGNORECASE),
        r"\1[REDACTED]",
    ),
]


def mask_secret(text: str) -> str:
    """Mask any sensitive credentials within a string."""
    if not text:
        return text
    result = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class SecretMaskingFilter(logging.Filter):
    """Logging filter that scrubs sensitive credentials from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_secret(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (mask_secret(str(v)) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    mask_secret(str(arg)) if isinstance(arg, str) else arg
                    for arg in record.args
                )
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

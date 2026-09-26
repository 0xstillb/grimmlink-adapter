"""Unit tests verifying the critical security invariant: secrets are never logged."""

import logging

from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.security.masking import SecretMaskingFilter, mask_secret


def test_mask_secret_bearer_token() -> None:
    raw = "Request header Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    masked = mask_secret(raw)
    assert "Bearer [REDACTED]" in masked
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in masked


def test_mask_secret_x_auth_key() -> None:
    raw = "Client sent header x-auth-key: 5f4dcc3b5aa765d61d8327deb882cf99"
    masked = mask_secret(raw)
    assert "x-auth-key: [REDACTED]" in masked
    assert "5f4dcc3b5aa765d61d8327deb882cf99" not in masked


def test_mask_secret_json_body() -> None:
    raw = '{"username": "admin", "password": "supersecretpassword123", "token": "abcde"}'
    masked = mask_secret(raw)
    assert '"password": "[REDACTED]"' in masked
    assert '"token": "[REDACTED]"' in masked
    assert "supersecretpassword123" not in masked
    assert "abcde" not in masked
    assert '"username": "admin"' in masked


def test_client_credentials_repr_redaction() -> None:
    creds = ClientCredentials(
        username="reader_user",
        md5_key="e10adc3949ba59abbe56e057f20f883e",
        bearer_token="secret_jwt_token_value",
    )
    rep = repr(creds)
    assert "reader_user" in rep
    assert "e10adc3949ba59abbe56e057f20f883e" not in rep
    assert "secret_jwt_token_value" not in rep
    assert "[REDACTED]" in rep


def test_logging_filter_scrubs_records() -> None:
    filter_obj = SecretMaskingFilter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Login failed with Authorization: Bearer secret_token_12345",
        args=(),
        exc_info=None,
    )
    filter_obj.filter(record)
    assert "secret_token_12345" not in record.msg
    assert "Bearer [REDACTED]" in record.msg

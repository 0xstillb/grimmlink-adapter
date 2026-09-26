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


def test_logging_filter_scrubs_records_without_args() -> None:
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
    assert record.args == ()


def test_logging_filter_scrubs_records_with_positional_args() -> None:
    """Issue 3 fix verification: %s format strings must not break or leak raw args."""
    filter_obj = SecretMaskingFilter()

    # Case 1: msg contains Authorization header prefix, token passed in args
    record1 = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Request headers: Authorization: Bearer %s",
        args=("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token_payload",),
        exc_info=None,
    )
    assert filter_obj.filter(record1) is True
    assert "token_payload" not in record1.msg
    assert "Bearer [REDACTED]" in record1.msg
    # Ensure args is cleared so stderr output handler never receives raw token
    assert record1.args == ()

    # Case 2: msg contains key header prefix, MD5 passed in args
    record2 = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="User %s supplied x-auth-key: %s",
        args=("john_doe", "098f6bcd4621d373cade4e832627b4f6"),
        exc_info=None,
    )
    assert filter_obj.filter(record2) is True
    assert "john_doe" in record2.msg
    assert "098f6bcd4621d373cade4e832627b4f6" not in record2.msg
    assert "x-auth-key: [REDACTED]" in record2.msg
    assert record2.args == ()


def test_logging_filter_scrubs_records_with_dict_args() -> None:
    """Issue 3 fix verification: %(key)s format strings must evaluate and scrub."""
    filter_obj = SecretMaskingFilter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="User %(user)s sent password=%(pwd)s",
        args={"user": "admin", "pwd": "unmasked_secret_password"},
        exc_info=None,
    )
    assert filter_obj.filter(record) is True
    assert "admin" in record.msg
    assert "unmasked_secret_password" not in record.msg
    assert "password=[REDACTED]" in record.msg
    assert record.args == ()

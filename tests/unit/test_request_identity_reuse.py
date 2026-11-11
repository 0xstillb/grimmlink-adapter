"""A verified session identity is reusable only within its current request."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from grimmlink_adapter.api.v2 import _issue_session
from grimmlink_adapter.models.grimmlink import GrimmlinkAuthResponse
from grimmlink_adapter.security.auth_extractor import (
    ClientCredentials,
    _official_session_credentials,
)
from grimmlink_adapter.services.linked_auth import LinkedAuthUnavailable
from grimmlink_adapter.services.request_auth import VerifiedBearer, get_official_bearer
from grimmlink_adapter.state.adapter_sessions import AdapterSessionStore


@pytest.mark.asyncio
async def test_verified_session_profile_is_checked_once_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    token, session = await AdapterSessionStore.create_official("reader", 42, "access-token", None)
    profile = AsyncMock(return_value={"id": 42, "username": "reader"})
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_current_user", profile
    )
    try:
        creds = await _official_session_credentials(token, session)
        bearer = await get_official_bearer(creds)
        assert isinstance(bearer, VerifiedBearer)
        assert bearer.user_id == 42
        profile.assert_awaited_once_with("access-token")
    finally:
        await AdapterSessionStore.revoke(token)


@pytest.mark.asyncio
async def test_mismatched_session_profile_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    token, session = await AdapterSessionStore.create_official("reader", 42, "access-token", None)
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_current_user",
        AsyncMock(return_value={"id": 99, "username": "reader"}),
    )
    try:
        with pytest.raises(HTTPException) as exc:
            await _official_session_credentials(token, session)
        assert exc.value.status_code == 401
    finally:
        await AdapterSessionStore.revoke(token)


@pytest.mark.asyncio
async def test_raw_client_token_still_requires_upstream_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    authorize = AsyncMock(side_effect=HTTPException(status_code=401, detail="Unauthorized"))
    monkeypatch.setattr(
        "grimmlink_adapter.services.auth_service.AuthService.authorize_client", authorize
    )
    with pytest.raises(HTTPException) as exc:
        await get_official_bearer(ClientCredentials(bearer_token="unverified"))
    assert exc.value.status_code == 401
    authorize.assert_awaited_once()


@pytest.mark.asyncio
async def test_reader_login_verifies_credentials_once_and_checks_linked_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock(return_value=GrimmlinkAuthResponse(username="reader", userId=42))
    linked = AsyncMock(return_value="verified-upstream-token")
    monkeypatch.setattr("grimmlink_adapter.services.auth_service.AuthService.authorize_client", authorize)
    monkeypatch.setattr("grimmlink_adapter.services.request_auth.get_linked_bearer", linked)
    result = await _issue_session("reader", "reader-key")
    try:
        assert result.username == "reader"
        assert result.userId == 42
        authorize.assert_awaited_once()
        assert linked.await_args.args[1:] == ("42", "reader", "reader-key")
        session = await AdapterSessionStore.get(result.accessToken)
        assert session is not None and session.user_id == 42
    finally:
        await AdapterSessionStore.revoke(result.accessToken)


@pytest.mark.asyncio
async def test_reader_login_rejects_bad_credentials_before_link_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock(side_effect=HTTPException(status_code=401, detail="Invalid credentials"))
    linked = AsyncMock()
    monkeypatch.setattr("grimmlink_adapter.services.auth_service.AuthService.authorize_client", authorize)
    monkeypatch.setattr("grimmlink_adapter.services.request_auth.get_linked_bearer", linked)
    with pytest.raises(HTTPException) as exc:
        await _issue_session("reader", "wrong-key")
    assert exc.value.status_code == 401
    authorize.assert_awaited_once()
    linked.assert_not_awaited()


@pytest.mark.asyncio
async def test_reader_login_rejects_unmatched_linked_account(monkeypatch: pytest.MonkeyPatch) -> None:
    authorize = AsyncMock(return_value=GrimmlinkAuthResponse(username="reader", userId=42))
    linked = AsyncMock(side_effect=LinkedAuthUnavailable("Linked account identity mismatch"))
    monkeypatch.setattr("grimmlink_adapter.services.auth_service.AuthService.authorize_client", authorize)
    monkeypatch.setattr("grimmlink_adapter.services.request_auth.get_linked_bearer", linked)
    with pytest.raises(HTTPException) as exc:
        await _issue_session("reader", "reader-key")
    assert exc.value.status_code == 501
    authorize.assert_awaited_once()
    linked.assert_awaited_once()

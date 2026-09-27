"""Regression checks for explicitly linked same-user JWT credentials."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from grimmlink_adapter.config import settings
from grimmlink_adapter.link_account import link
from grimmlink_adapter.models.official import OfficialLoginResponse
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialAuthError
from grimmlink_adapter.services.linked_auth import LinkedAuthUnavailable, get_linked_bearer
from grimmlink_adapter.state.database import get_connection
from grimmlink_adapter.state.linked_accounts import (
    LinkedAccount,
    LinkedAccountStore,
    digest_md5_key,
)

SERVER = settings.GRIMMORY_BASE_URL


@pytest.mark.asyncio
async def test_local_link_checks_both_auth_modes_before_saving() -> None:
    async with get_connection() as conn:
        await conn.execute("DELETE FROM linked_accounts")
        await conn.commit()
    with (
        patch.object(OfficialGrimmoryClient, "get_koreader_auth", new_callable=AsyncMock,
                     return_value={"username": "reader"}) as koreader,
        patch.object(OfficialGrimmoryClient, "login", new_callable=AsyncMock,
                     return_value=OfficialLoginResponse(
                         token="access", refreshToken="refresh", userId=17, username="reader",
                     )),
        patch.object(OfficialGrimmoryClient, "get_current_user", new_callable=AsyncMock,
                     return_value={"id": 17, "username": "reader"}),
    ):
        assert await link("reader", "password", SERVER) == "17"
    koreader.assert_awaited_once()
    assert koreader.await_args.args[0] == "reader"
    assert koreader.await_args.args[1] == "5f4dcc3b5aa765d61d8327deb882cf99"
    account = await LinkedAccountStore.get(SERVER, "17")
    assert account is not None
    assert account.matches("reader", "5f4dcc3b5aa765d61d8327deb882cf99")
    assert account.access_token == "access"


@pytest.mark.asyncio
async def test_local_link_rejects_different_upstream_user() -> None:
    with (
        patch.object(OfficialGrimmoryClient, "get_koreader_auth", new_callable=AsyncMock,
                     return_value={"username": "other-reader"}),
        patch.object(OfficialGrimmoryClient, "login", new_callable=AsyncMock,
                     return_value=OfficialLoginResponse(token="access", userId=17)),
        patch.object(OfficialGrimmoryClient, "get_current_user", new_callable=AsyncMock,
                     return_value={"id": 17, "username": "reader"}),
    ):
        with pytest.raises(ValueError, match="identities do not match"):
            await link("reader", "password", SERVER)
    assert await LinkedAccountStore.get(SERVER, "18") is None


@pytest.mark.asyncio
async def test_runtime_rejects_profile_identity_change() -> None:
    await LinkedAccountStore.put(LinkedAccount(
        server=SERVER, user_id="20", username="reader",
        md5_key_digest=digest_md5_key("md5-key"),
        access_token="access", refresh_token="refresh",
    ))
    with patch.object(OfficialGrimmoryClient, "get_current_user", new_callable=AsyncMock,
                      return_value={"id": 21, "username": "reader"}):
        with pytest.raises(LinkedAuthUnavailable, match="no longer matches"):
            await get_linked_bearer(SERVER, "20", "reader", "md5-key")


@pytest.mark.asyncio
async def test_runtime_refreshes_expired_link_and_persists_token() -> None:
    await LinkedAccountStore.put(LinkedAccount(
        server=SERVER, user_id="22", username="reader",
        md5_key_digest=digest_md5_key("md5-key"),
        access_token="expired-access", refresh_token="old-refresh",
    ))
    with (
        patch.object(OfficialGrimmoryClient, "get_current_user", new_callable=AsyncMock,
                     side_effect=[OfficialAuthError("expired"), {"id": 22, "username": "reader"}]),
        patch.object(OfficialGrimmoryClient, "refresh_token", new_callable=AsyncMock,
                     return_value=OfficialLoginResponse(
                         token="fresh-access", refreshToken="new-refresh",
                     )) as refresh,
    ):
        token = await get_linked_bearer(SERVER, "22", "reader", "md5-key")
    assert token == "fresh-access"
    refresh.assert_awaited_once()
    account = await LinkedAccountStore.get(SERVER, "22")
    assert account is not None
    assert account.access_token == "fresh-access"
    assert account.refresh_token == "new-refresh"

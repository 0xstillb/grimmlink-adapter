"""Use an explicitly linked account's JWT for that same KOReader user."""

from __future__ import annotations

from grimmlink_adapter.official.auth import parse_jwt_expiry
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialAuthError, OfficialPermissionError
from grimmlink_adapter.state.linked_accounts import LinkedAccount, LinkedAccountStore


class LinkedAuthUnavailable(Exception):
    """No valid same-user JWT has been linked locally."""


async def get_linked_bearer(
    server: str, user_id: str, username: str, md5_key: str,
) -> str:
    """Return a verified same-user token, refreshing it when needed."""
    account = await LinkedAccountStore.get(server, user_id)
    if account is None or not account.matches(username, md5_key):
        raise LinkedAuthUnavailable("Link this Grimmory account to use MD5-only book lookup")

    async with OfficialGrimmoryClient(base_url=server) as client:
        client.set_jwt_credentials(
            username=account.username,
            access_token=account.access_token,
            refresh_token=account.refresh_token,
        )
        token = account.access_token
        expiry = parse_jwt_expiry(token)
        if expiry is not None and client.jwt_auth is not None and client.jwt_auth.is_expired():
            token = await _refresh(client, account)
        try:
            profile = await client.get_current_user(token)
        except (OfficialAuthError, OfficialPermissionError):
            token = await _refresh(client, account)
            profile = await client.get_current_user(token)
        profile_id = profile.get("id") or profile.get("userId")
        if str(profile_id) != user_id or profile.get("username") != username:
            raise LinkedAuthUnavailable("Linked account identity no longer matches KOReader user")
        return token


async def _refresh(client: OfficialGrimmoryClient, account: LinkedAccount) -> str:
    if not account.refresh_token:
        raise LinkedAuthUnavailable("Linked Grimmory session expired; link the account again")
    try:
        refreshed = await client.refresh_token()
    except OfficialAuthError as exc:
        raise LinkedAuthUnavailable("Linked Grimmory session expired; link the account again") from exc
    await LinkedAccountStore.put(LinkedAccount(
        server=account.server,
        user_id=account.user_id,
        username=account.username,
        md5_key_digest=account.md5_key_digest,
        access_token=refreshed.token,
        refresh_token=refreshed.refreshToken or account.refresh_token,
    ))
    return refreshed.token

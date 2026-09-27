"""One-time local account link; prompts for the user's Grimmory password."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib

from grimmlink_adapter.config import settings
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.state.linked_accounts import (
    LinkedAccount,
    LinkedAccountStore,
    digest_md5_key,
)
from grimmlink_adapter.state.migrations import apply_migrations


async def link(username: str, password: str, server: str) -> str:
    """Verify both Official auth modes identify one user before saving tokens."""
    server = server.rstrip("/")
    md5_key = hashlib.md5(password.encode("utf-8")).hexdigest()
    async with OfficialGrimmoryClient(base_url=server) as client:
        koreader = await client.get_koreader_auth(username, md5_key)
        login = await client.login(username, password)
        profile = await client.get_current_user(login.token)
    koreader_id = koreader.get("userId") or koreader.get("id")
    profile_id = profile.get("id") or profile.get("userId")
    if (
        isinstance(koreader_id, bool) or not isinstance(koreader_id, int)
        or isinstance(profile_id, bool) or not isinstance(profile_id, int)
        or koreader_id <= 0 or koreader_id != profile_id
        or profile.get("username") != username
    ):
        raise ValueError("Official KOReader and JWT identities do not match")
    await apply_migrations()
    await LinkedAccountStore.put(LinkedAccount(
        server=server.rstrip("/"),
        user_id=str(profile_id),
        username=username,
        md5_key_digest=digest_md5_key(md5_key),
        access_token=login.token,
        refresh_token=login.refreshToken,
    ))
    return str(profile_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Link a Grimmory user for MD5-only book lookup")
    parser.add_argument("username")
    parser.add_argument("--server", default=settings.GRIMMORY_BASE_URL)
    args = parser.parse_args()
    user_id = asyncio.run(link(args.username, getpass.getpass("Grimmory password: "), args.server))
    print(f"Linked Grimmory user {user_id}")


if __name__ == "__main__":
    main()

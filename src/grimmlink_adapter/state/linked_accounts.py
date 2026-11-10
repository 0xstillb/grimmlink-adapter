"""Adapter-owned credentials explicitly linked to a KOReader account."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from grimmlink_adapter.security.metadata_vault import metadata_cipher
from grimmlink_adapter.state.database import get_connection


def digest_md5_key(md5_key: str) -> str:
    """Avoid storing the KOReader authentication header verbatim."""
    return hashlib.sha256(md5_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LinkedAccount:
    server: str
    user_id: str
    username: str
    md5_key_digest: str
    access_token: str
    refresh_token: str | None

    def matches(self, username: str, md5_key: str) -> bool:
        return self.username == username and hmac.compare_digest(
            self.md5_key_digest, digest_md5_key(md5_key)
        )


class LinkedAccountStore:
    @staticmethod
    def decode(row: dict[str, Any]) -> LinkedAccount:
        for field in ("access_token", "refresh_token"):
            value = row[field]
            if value and value.startswith("encrypted:v1:"):
                row[field] = metadata_cipher().decrypt(value[len("encrypted:v1:"):].encode()).decode()
        return LinkedAccount(**row)

    @staticmethod
    async def encrypt_existing() -> None:
        async with get_connection() as conn:
            async with conn.execute("SELECT server,user_id,username,md5_key_digest,access_token,refresh_token FROM linked_accounts") as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            if not row["access_token"].startswith("encrypted:v1:"):
                await LinkedAccountStore.put(LinkedAccountStore.decode(dict(row)))

    @staticmethod
    async def get(server: str, user_id: str) -> LinkedAccount | None:
        async with get_connection() as conn, conn.execute(
            """SELECT server, user_id, username, md5_key_digest, access_token, refresh_token
               FROM linked_accounts WHERE server = ? AND user_id = ?""",
            (server, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return LinkedAccountStore.decode(dict(row)) if row else None

    @staticmethod
    async def get_by_credentials(
        server: str, username: str, md5_key: str,
    ) -> LinkedAccount | None:
        """Find a linked account without storing or exposing the raw MD5 key."""
        async with get_connection() as conn, conn.execute(
            """SELECT server, user_id, username, md5_key_digest, access_token, refresh_token
               FROM linked_accounts WHERE server = ? AND username = ?""",
            (server, username),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            account = LinkedAccountStore.decode(dict(row))
            if account.matches(username, md5_key):
                return account
        return None

    @staticmethod
    async def put(account: LinkedAccount) -> None:
        cipher = metadata_cipher()
        access = "encrypted:v1:" + cipher.encrypt(account.access_token.encode()).decode()
        refresh = "encrypted:v1:" + cipher.encrypt(account.refresh_token.encode()).decode() if account.refresh_token else None
        async with get_connection() as conn:
            await conn.execute(
                """INSERT INTO linked_accounts
                   (server, user_id, username, md5_key_digest, access_token, refresh_token)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(server, user_id) DO UPDATE SET
                     username = excluded.username,
                     md5_key_digest = excluded.md5_key_digest,
                     access_token = excluded.access_token,
                     refresh_token = excluded.refresh_token,
                     updated_at = CURRENT_TIMESTAMP""",
                (account.server, account.user_id, account.username, account.md5_key_digest,
                 access, refresh),
            )
            await conn.commit()

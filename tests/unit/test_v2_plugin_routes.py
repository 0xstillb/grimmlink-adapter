import pytest
from fastapi import HTTPException

from grimmlink_adapter.api import v2
from grimmlink_adapter.official.identity_lookup import (
    GrimmoryIdentityCandidate,
    GrimmoryIdentityLookup,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials


@pytest.mark.asyncio
async def test_primary_hash_requires_matching_book_and_file(monkeypatch):
    async def bearer(_creds):
        return "official-token"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_book_by_id(self, book_id, token):
            assert (book_id, token) == (12, "official-token")
            return {"id": 12, "primaryFile": {"id": 34}}

    class Lookup:
        enabled = True

        async def lookup_file_id(self, file_id):
            assert file_id == 34
            return [GrimmoryIdentityCandidate(12, 34, "current-md5", "initial-md5")]

    monkeypatch.setattr(v2, "get_official_bearer", bearer)
    monkeypatch.setattr(v2, "OfficialGrimmoryClient", Client)
    monkeypatch.setattr(v2, "GrimmoryIdentityLookup", Lookup)
    result = await v2.get_primary_file_hash(12, ClientCredentials(bearer_token="adapter-token"))
    assert result == {"bookId": 12, "bookFileId": 34, "bookHash": "current-md5"}

    async def wrong_book(_self, _file_id):
        return [GrimmoryIdentityCandidate(13, 34, "wrong-md5", "wrong-md5")]

    monkeypatch.setattr(Lookup, "lookup_file_id", wrong_book)
    with pytest.raises(HTTPException) as exc:
        await v2.get_primary_file_hash(12, ClientCredentials(bearer_token="adapter-token"))
    assert exc.value.status_code == 409

    class WrongClient(Client):
        async def get_book_by_id(self, _book_id, _token):
            return {"id": 99, "primaryFile": {"id": 34}}

    monkeypatch.setattr(v2, "OfficialGrimmoryClient", WrongClient)
    with pytest.raises(HTTPException) as wrong_response:
        await v2.get_primary_file_hash(12, ClientCredentials(bearer_token="adapter-token"))
    assert wrong_response.value.status_code == 502


@pytest.mark.asyncio
async def test_file_id_lookup_uses_exact_read_only_query(monkeypatch):
    lookup = GrimmoryIdentityLookup()
    queried = {}

    async def query(sql, value):
        queried["sql"] = sql
        queried["value"] = value
        return []

    monkeypatch.setattr(lookup, "_query", query)
    await lookup.lookup_file_id(34)
    assert "bf.id = %s" in queried["sql"]
    assert "bf.current_hash = %s" not in queried["sql"]
    assert queried["value"] == 34

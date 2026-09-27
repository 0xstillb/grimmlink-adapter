"""Session 04 read-only shelf and Official download coverage."""

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from grimmlink_adapter.api import books as books_api
from grimmlink_adapter.models.grimmlink import GrimmlinkBookSummary
from grimmlink_adapter.official.exceptions import OfficialPermissionError, OfficialTimeoutError
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services import request_auth as request_auth_module
from grimmlink_adapter.services import shelf_service as shelf_module
from grimmlink_adapter.services.request_auth import get_official_bearer
from grimmlink_adapter.services.shelf_service import ShelfService


def official_book(book_id: int, file_id: int | None = 10) -> dict[str, Any]:
    book: dict[str, Any] = {
        "id": book_id,
        "title": f"Book {book_id}",
        "metadata": {"authors": ["Ada Author"], "seriesName": "Series", "seriesNumber": 2},
    }
    if file_id is not None:
        book["primaryFile"] = {
            "id": file_id,
            "bookId": book_id,
            "fileName": f"{book_id}.epub",
            "extension": "epub",
            "fileSizeKb": 12,
        }
    return book


def app_book(book_id: int, file_id: int = 10) -> dict[str, Any]:
    return {
        "id": book_id,
        "title": f"Book {book_id}",
        "authors": ["Ada Author"],
        "seriesName": "Series",
        "seriesNumber": 2,
        "primaryFileId": file_id,
        "primaryFileType": "EPUB",
        "primaryFileName": f"{book_id}.epub",
        "fileSizeKb": 12,
    }


@pytest.fixture(autouse=True)
def bearer_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(_: ClientCredentials) -> str:
        return "verified-bearer"

    monkeypatch.setattr(shelf_module, "get_official_bearer", resolve)


@pytest.mark.asyncio
async def test_regular_empty_and_nonempty_shelves_normalize_optional_primary_file() -> None:
    client = AsyncMock()
    client.get_shelf_books.side_effect = [[], [official_book(1), official_book(2, None)]]
    service = ShelfService(client)

    assert await service.list_shelf_books("regular", 1, ClientCredentials(bearer_token="t")) == []
    books = await service.list_shelf_books("regular", 2, ClientCredentials(bearer_token="t"))

    assert books == [
        GrimmlinkBookSummary(
            bookId=1, bookFileId=10, title="Book 1", author="Ada Author", fileName="1.epub",
            originalFileName="1.epub", extension="epub", fileFormat="EPUB", fileSizeKb=12,
            fileSize=12 * 1024, seriesName="Series", seriesNumber=2,
        ),
        GrimmlinkBookSummary(bookId=2, title="Book 2", author="Ada Author", seriesName="Series", seriesNumber=2),
    ]


@pytest.mark.asyncio
async def test_shelf_pagination_uses_book_id_cursor_and_legacy_limit() -> None:
    client = AsyncMock()
    client.get_shelf_books.return_value = [official_book(book_id) for book_id in range(1, 106)]
    service = ShelfService(client)
    creds = ClientCredentials(bearer_token="t")

    assert len(await service.list_shelf_books("regular", 1, creds)) == 100
    assert [book.bookId for book in await service.list_shelf_books(
        "regular", 1, creds, limit=2, cursor="50",
    )] == [51, 52]
    assert [book.bookId for book in await service.list_shelf_books(
        "regular", 1, creds, limit=2, offset=2, cursor="50",
    )] == [3, 4]
    assert len(await service.list_shelf_books("regular", 1, creds, limit=200)) == 100
    assert len(await service.list_shelf_books("regular", 1, creds, limit=0)) == 100


@pytest.mark.asyncio
async def test_shelf_extension_comes_from_filename_and_format_from_file_type() -> None:
    client = AsyncMock()
    regular = official_book(1)
    regular["primaryFile"]["extension"] = "pdf"
    regular["primaryFile"]["bookType"] = "EPUB"
    client.get_shelf_books.return_value = [regular]
    client.get_magic_shelf_books.return_value = {
        "content": [{**app_book(2), "primaryFileName": "2.PDF"}],
        "page": 0, "size": 50, "totalElements": 1, "totalPages": 1,
        "hasNext": False, "hasPrevious": False,
    }
    service = ShelfService(client)
    creds = ClientCredentials(bearer_token="t")

    regular_books = await service.list_shelf_books("regular", 1, creds)
    magic_books = await service.list_shelf_books("magic", 2, creds)

    assert (regular_books[0].extension, regular_books[0].fileFormat) == ("epub", "EPUB")
    assert (magic_books[0].extension, magic_books[0].fileFormat) == ("pdf", "EPUB")


@pytest.mark.asyncio
async def test_magic_shelves_loop_all_pages_without_duplicate_books() -> None:
    client = AsyncMock()
    first_page = [app_book(book_id) for book_id in range(1, 51)]
    pages = [
        {
            "content": first_page, "page": 0, "size": 50,
            "totalElements": 51, "totalPages": 2, "hasNext": True, "hasPrevious": False,
        },
        {
            "content": [app_book(51)], "page": 1, "size": 50,
            "totalElements": 51, "totalPages": 2, "hasNext": False, "hasPrevious": True,
        },
    ]
    client.get_magic_shelf_books.side_effect = pages + pages
    service = ShelfService(client)

    books = await service.list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert [book.bookId for book in books] == list(range(1, 52))
    assert books[0].bookFileId == 10
    assert books[0].fileName == "1.epub"
    assert books[0].extension == "epub"
    assert books[0].fileFormat == "EPUB"
    assert books[0].fileSizeKb == 12
    assert books[0].author == "Ada Author"
    assert books[0].seriesName == "Series"
    assert [call.args[2] for call in client.get_magic_shelf_books.call_args_list] == [0, 1, 0, 1]


@pytest.mark.asyncio
async def test_magic_same_total_membership_drift_fails_closed() -> None:
    client = AsyncMock()
    first_page = [app_book(book_id) for book_id in range(1, 51)]
    changed_page = [app_book(0), *[app_book(book_id) for book_id in range(2, 51)]]
    last_page = {
        "content": [app_book(51)], "page": 1, "size": 50,
        "totalElements": 51, "totalPages": 2, "hasNext": False, "hasPrevious": True,
    }
    client.get_magic_shelf_books.side_effect = [
        {
            "content": first_page, "page": 0, "size": 50,
            "totalElements": 51, "totalPages": 2, "hasNext": True, "hasPrevious": False,
        },
        last_page,
        {
            "content": changed_page, "page": 0, "size": 50,
            "totalElements": 51, "totalPages": 2, "hasNext": True, "hasPrevious": False,
        },
        last_page,
    ]

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502
    assert [call.args[2] for call in client.get_magic_shelf_books.call_args_list] == [0, 1, 0, 1]


@pytest.mark.asyncio
async def test_magic_primary_file_drift_fails_closed() -> None:
    client = AsyncMock()
    client.get_magic_shelf_books.side_effect = [
        {
            "content": [app_book(1, file_id=10)], "page": 0, "size": 50,
            "totalElements": 1, "totalPages": 1, "hasNext": False, "hasPrevious": False,
        },
        {
            "content": [app_book(1, file_id=11)], "page": 0, "size": 50,
            "totalElements": 1, "totalPages": 1, "hasNext": False, "hasPrevious": False,
        },
    ]

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_magic_duplicate_book_ids_fail_closed() -> None:
    client = AsyncMock()
    first_page = [app_book(book_id) for book_id in range(1, 51)]
    client.get_magic_shelf_books.side_effect = [
        {
            "content": first_page, "page": 0, "size": 50,
            "totalElements": 52, "totalPages": 2, "hasNext": True, "hasPrevious": False,
        },
        {
            "content": [app_book(50), app_book(51)], "page": 1, "size": 50,
            "totalElements": 52, "totalPages": 2, "hasNext": False, "hasPrevious": True,
        },
    ]

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_magic_empty_snapshot_without_reliable_metadata_fails_closed() -> None:
    client = AsyncMock()
    client.get_magic_shelf_books.return_value = {
        "content": [], "page": 0, "size": 50,
        "totalElements": 0, "totalPages": 0, "hasNext": False, "hasPrevious": False,
    }

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_magic_page_failure_fails_whole_snapshot() -> None:
    client = AsyncMock()
    first_page = [app_book(book_id) for book_id in range(1, 51)]
    client.get_magic_shelf_books.side_effect = [
        {
            "content": first_page, "page": 0, "size": 50,
            "totalElements": 51, "totalPages": 2, "hasNext": True, "hasPrevious": False,
        },
        OfficialTimeoutError("page timeout"),
    ]

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502
    assert "incomplete" in str(caught.value.detail)


@pytest.mark.asyncio
async def test_magic_short_nonterminal_page_fails_closed() -> None:
    client = AsyncMock()
    client.get_magic_shelf_books.return_value = {
        "content": [app_book(1)], "page": 0, "size": 50,
        "totalElements": 51, "totalPages": 2, "hasNext": True, "hasPrevious": False,
    }

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_magic_inconsistent_page_metadata_fails_closed() -> None:
    client = AsyncMock()
    client.get_magic_shelf_books.return_value = {
        "content": [app_book(1)], "page": 1, "size": 50,
        "totalElements": 1, "totalPages": 1, "hasNext": False, "hasPrevious": True,
    }

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books("magic", 9, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_same_id_regular_and_magic_shelves_remain_distinct() -> None:
    client = AsyncMock()
    client.get_regular_shelves.return_value = [{"id": 5, "name": "Manual", "bookCount": 1}]
    client.get_magic_shelves.return_value = [{"id": 5, "name": "Rule", "rule": "read"}]

    result = await ShelfService(client).list_shelves(creds=ClientCredentials(bearer_token="t"))

    assert [(item.id, item.type) for item in result] == [(5, "regular"), (5, "magic")]
    assert result[1].description == "read"


@pytest.mark.asyncio
async def test_regular_series_uses_official_metadata_fields() -> None:
    client = AsyncMock()
    client.get_shelf_books.return_value = [{
        "id": 1,
        "title": "Book 1",
        "metadata": {"authors": ["Ada Author"], "seriesName": "Series", "seriesNumber": 2},
    }]

    books = await ShelfService(client).list_shelf_books(
        "regular", 1, ClientCredentials(bearer_token="t"),
    )

    assert books[0].seriesName == "Series"
    assert books[0].seriesNumber == 2


@pytest.mark.asyncio
async def test_shelf_forbidden_is_preserved_as_403() -> None:
    client = AsyncMock()
    client.get_shelf_books.side_effect = OfficialPermissionError("forbidden", status_code=403)

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).list_shelf_books(
            "regular", 1, ClientCredentials(bearer_token="t"),
        )

    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_download_stream_preserves_binary_and_disposition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(books_api, "get_official_bearer", AsyncMock(return_value="verified"))
    upstream = httpx.Response(200, content=b"PK\x03\x04book", headers={"content-type": "application/epub+zip"})
    client = AsyncMock()
    client.get_book_by_id.return_value = official_book(1)
    client.download_book_stream.return_value = upstream
    monkeypatch.setattr(books_api, "OfficialGrimmoryClient", lambda: client)

    response = await books_api.download_book(1, ClientCredentials(bearer_token="t"))
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert body == b"PK\x03\x04book"
    assert "1.epub" in response.headers["content-disposition"]
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_timeout_and_error_document_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(books_api, "get_official_bearer", AsyncMock(return_value="verified"))
    client = AsyncMock()
    client.get_book_by_id.return_value = official_book(1)
    client.download_book_stream.side_effect = OfficialTimeoutError("timeout")
    monkeypatch.setattr(books_api, "OfficialGrimmoryClient", lambda: client)

    with pytest.raises(HTTPException) as timeout:
        await books_api.download_book(1, ClientCredentials(bearer_token="t"))
    assert timeout.value.status_code == 502

    client.download_book_stream.side_effect = None
    client.download_book_stream.return_value = httpx.Response(
        200, content=b'{"error":"not a book"}', headers={"content-type": "application/json"},
    )
    with pytest.raises(HTTPException) as bad_content:
        await books_api.download_book(1, ClientCredentials(bearer_token="t"))
    assert bad_content.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_error", [httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_download_first_chunk_error_closes_stream_and_client(
    monkeypatch: pytest.MonkeyPatch,
    stream_error: type[httpx.RequestError],
) -> None:
    monkeypatch.setattr(books_api, "get_official_bearer", AsyncMock(return_value="verified"))

    class FailingResponse:
        headers = {"content-type": "application/epub+zip"}

        def __init__(self) -> None:
            self.closed = False

        def aiter_bytes(self) -> Any:
            async def fail() -> Any:
                raise stream_error("late response")
                yield b""

            return fail()

        async def aclose(self) -> None:
            self.closed = True

    upstream = FailingResponse()
    client = AsyncMock()
    client.get_book_by_id.return_value = official_book(1)
    client.download_book_stream.return_value = upstream
    monkeypatch.setattr(books_api, "OfficialGrimmoryClient", lambda: client)

    with pytest.raises(HTTPException) as caught:
        await books_api.download_book(1, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502
    assert upstream.closed
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_bearer_and_md5_credentials_must_resolve_to_same_user(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAuth:
        async def authorize_client(self, creds: ClientCredentials) -> Any:
            user_id = 1 if creds.bearer_token else 2
            return type("Identity", (), {"userId": user_id})()

    monkeypatch.setattr(request_auth_module, "AuthService", FakeAuth)

    with pytest.raises(HTTPException) as caught:
        await get_official_bearer(ClientCredentials(
            bearer_token="jwt", username="reader", md5_key="md5",
        ))

    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_request_auth_closes_owned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    owned_client = AsyncMock()

    class FakeAuth:
        def __init__(self) -> None:
            self.official_client = owned_client

        async def authorize_client(self, creds: ClientCredentials) -> Any:
            return type("Identity", (), {"userId": 1})()

    monkeypatch.setattr(request_auth_module, "AuthService", FakeAuth)

    assert await get_official_bearer(ClientCredentials(bearer_token="jwt")) == "jwt"
    owned_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_requires_official_primary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(books_api, "get_official_bearer", AsyncMock(return_value="verified"))
    client = AsyncMock()
    client.get_book_by_id.return_value = official_book(1, None)
    monkeypatch.setattr(books_api, "OfficialGrimmoryClient", lambda: client)

    with pytest.raises(HTTPException) as caught:
        await books_api.download_book(1, ClientCredentials(bearer_token="t"))

    assert caught.value.status_code == 502
    client.download_book_stream.assert_not_awaited()

"""Session 03A API/fallback, lock, dry-run, and idempotency tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialAuthError, OfficialTimeoutError
from grimmlink_adapter.official.identity_lookup import GrimmoryIdentityCandidate
from grimmlink_adapter.opf.parser import grimmory_file_fingerprint
from grimmlink_adapter.services.opf_ingestion import (
    OPFAuthorizationError,
    OPFFieldLockError,
    OPFIngestionService,
)

OPF = """<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Title</dc:title><dc:creator>Author</dc:creator></metadata><manifest /></package>"""


class MemoryStateStore:
    records: dict[str, SimpleNamespace] = {}

    @classmethod
    async def get(cls, key: str) -> SimpleNamespace | None:
        return cls.records.get(key)

    @classmethod
    async def put(cls, **values: object) -> None:
        values["last_success_at"] = "now" if values["success"] else None
        values["last_result_status"] = values["result_status"]
        cls.records[str(values["source_opf_path"])] = SimpleNamespace(**values)


def make_source(tmp_path: Path) -> tuple[Path, Path]:
    book = tmp_path / "book.epub"
    book.write_bytes(b"immutable ebook bytes")
    opf = tmp_path / "metadata.opf"
    opf.write_text(OPF, encoding="utf-8")
    return book, opf


def make_client() -> AsyncMock:
    client = AsyncMock(spec=OfficialGrimmoryClient)
    client.update_book_metadata.return_value = {}
    client.get_app_settings.return_value = {
        "metadataPersistenceSettings": {
            "moveFilesToLibraryPattern": False,
            "saveToOriginalFile": {
                fmt: {"enabled": False} for fmt in ("epub", "pdf", "cbx", "audiobook")
            },
        },
    }
    return client


def make_service(client: AsyncMock, book: Path) -> OPFIngestionService:
    fingerprint = grimmory_file_fingerprint(book)
    client.get_book_by_id.return_value = {
        "id": 534,
        "primaryFile": {"id": 534, "fileName": book.name},
        "alternativeFormats": [],
        "metadata": {"allMetadataLocked": False},
    }
    identity_lookup = AsyncMock()
    identity_lookup.lookup_current_hash.return_value = [
        GrimmoryIdentityCandidate(
            book_id=534, book_file_id=534,
            current_hash=fingerprint, initial_hash=fingerprint,
        ),
    ]
    identity_lookup.lookup_initial_hash.return_value = []
    return OPFIngestionService(
        official_client=client, state_store=MemoryStateStore,
        identity_lookup=identity_lookup,
    )


@pytest.mark.asyncio
async def test_api_success_does_not_write_sidecars_and_preserves_book(tmp_path: Path) -> None:
    book, opf = make_source(tmp_path)
    client = make_client()
    service = make_service(client, book)

    result = await service.ingest(
        book_path=book, opf_path=opf, book_id=534, bearer_token="bearer",
    )

    assert result.status == "success"
    assert result.primary_action == "official_api"
    assert not (tmp_path / "book.metadata.json").exists()
    assert not (tmp_path / "book.cover.jpg").exists()
    assert book.read_bytes() == b"immutable ebook bytes"
    client.update_book_metadata.assert_awaited_once()
    call = client.update_book_metadata.await_args
    assert call is not None
    assert call.args[0] == 534
    assert call.args[2]["metadata"]["bookId"] == 534
    assert call.args[2]["metadata"]["title"] == "Title"


@pytest.mark.asyncio
async def test_timeout_uses_object_form_sidecar_fallback(tmp_path: Path) -> None:
    book, opf = make_source(tmp_path)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xffcover")
    opf.write_text(OPF.replace("<manifest />", '<manifest><item id="cover" href="cover.jpg" properties="cover-image" /></manifest>'), encoding="utf-8")
    client = make_client()
    client.update_book_metadata.side_effect = OfficialTimeoutError("timeout")
    service = make_service(client, book)

    result = await service.ingest(
        book_path=book, opf_path=opf, book_id=534, bearer_token="bearer",
    )

    assert result.fallback_reason == "metadata_api_unavailable"
    payload = json.loads((tmp_path / "book.metadata.json").read_text(encoding="utf-8"))
    assert payload["cover"] == {"source": "external", "path": "book.cover.jpg"}
    assert (tmp_path / "book.cover.jpg").read_bytes() == b"\xff\xd8\xffcover"
    assert book.read_bytes() == b"immutable ebook bytes"


@pytest.mark.asyncio
async def test_auth_failure_never_falls_back(tmp_path: Path) -> None:
    book, opf = make_source(tmp_path)
    client = make_client()
    client.update_book_metadata.side_effect = OfficialAuthError("unauthorized", status_code=401)
    service = make_service(client, book)

    with pytest.raises(OPFAuthorizationError):
        await service.ingest(book_path=book, opf_path=opf, book_id=534, bearer_token="bearer")
    assert not (tmp_path / "book.metadata.json").exists()


@pytest.mark.asyncio
async def test_dry_run_does_not_call_api_or_write_files(tmp_path: Path) -> None:
    book, opf = make_source(tmp_path)
    client = make_client()
    service = make_service(client, book)

    result = await service.ingest(
        book_path=book, opf_path=opf, book_id=534, bearer_token="bearer", dry_run=True,
    )

    assert result.status == "dry_run"
    assert result.preview is not None and result.preview.would_modify_book_file is False
    client.update_book_metadata.assert_not_awaited()
    assert not (tmp_path / "book.metadata.json").exists()


@pytest.mark.asyncio
async def test_unchanged_metadata_is_deduplicated(tmp_path: Path) -> None:
    book, opf = make_source(tmp_path)
    client = make_client()
    service = make_service(client, book)
    first = await service.ingest(book_path=book, opf_path=opf, book_id=534, bearer_token="bearer")
    second = await service.ingest(book_path=book, opf_path=opf, book_id=534, bearer_token="bearer")

    assert first.status == "success"
    assert second.status == "success"
    client.update_book_metadata.assert_awaited_once()


@pytest.mark.asyncio
async def test_locked_fields_stop_without_fallback(tmp_path: Path) -> None:
    book, opf = make_source(tmp_path)
    service = make_service(make_client(), book)
    with pytest.raises(OPFFieldLockError):
        await service.ingest(
            book_path=book, opf_path=opf, book_id=534,
            bearer_token="bearer", locked_fields={"title"},
        )
    assert not (tmp_path / "book.metadata.json").exists()

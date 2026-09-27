"""Integration coverage for OPF ingestion against adapter-local SQLite state."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.identity_lookup import GrimmoryIdentityCandidate
from grimmlink_adapter.opf.parser import grimmory_file_fingerprint
from grimmlink_adapter.services.opf_ingestion import OPFIngestionService
from grimmlink_adapter.state.opf_ingestion import OPFStateStore


@pytest.mark.asyncio
async def test_sidecar_only_ingestion_persists_dedupe_state(tmp_path: Path) -> None:
    book = tmp_path / "book.epub"
    book.write_bytes(b"immutable")
    opf = tmp_path / "metadata.opf"
    opf.write_text(
        '<package xmlns="http://www.idpf.org/2007/opf"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Title</dc:title>'
        '</metadata></package>',
        encoding="utf-8",
    )

    client = AsyncMock(spec=OfficialGrimmoryClient)
    client.get_book_by_id.return_value = {
        "id": 534,
        "primaryFile": {"id": 534, "fileName": book.name},
        "alternativeFormats": [],
        "metadata": {"allMetadataLocked": False},
    }
    client.get_app_settings.return_value = {
        "metadataPersistenceSettings": {
            "moveFilesToLibraryPattern": False,
            "saveToOriginalFile": {
                fmt: {"enabled": False} for fmt in ("epub", "pdf", "cbx", "audiobook")
            },
        },
    }
    identity_lookup = AsyncMock()
    fingerprint = grimmory_file_fingerprint(book)
    identity_lookup.lookup_current_hash.return_value = [
        GrimmoryIdentityCandidate(
            book_id=534, book_file_id=534,
            current_hash=fingerprint, initial_hash=fingerprint,
        ),
    ]
    identity_lookup.lookup_initial_hash.return_value = []
    service = OPFIngestionService(official_client=client, identity_lookup=identity_lookup)
    result = await service.ingest(
        book_path=book, opf_path=opf, book_id=534, bearer_token="test-bearer", mode="sidecar_only",
    )

    assert result.status == "success"
    sidecar = json.loads((tmp_path / "book.metadata.json").read_text(encoding="utf-8"))
    assert sidecar["metadata"]["title"] == "Title"
    state = await OPFStateStore.get(str(opf.resolve()))
    assert state is not None and state.book_id == 534

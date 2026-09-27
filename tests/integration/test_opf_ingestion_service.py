"""Integration coverage for OPF ingestion against adapter-local SQLite state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    service = OPFIngestionService()
    result = await service.ingest(
        book_path=book, opf_path=opf, book_id=534, mode="sidecar_only",
    )

    assert result.status == "success"
    sidecar = json.loads((tmp_path / "book.metadata.json").read_text(encoding="utf-8"))
    assert sidecar["metadata"]["title"] == "Title"
    state = await OPFStateStore.get(str(opf.resolve()))
    assert state is not None and state.book_id == 534

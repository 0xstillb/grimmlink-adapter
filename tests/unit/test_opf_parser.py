"""OPF discovery, normalization, containment, and sidecar contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimmlink_adapter.models.opf_metadata import CanonicalMetadata
from grimmlink_adapter.opf.parser import (
    AmbiguousOPFError,
    UnsafePathError,
    discover_opf,
    parse_opf,
)
from grimmlink_adapter.opf.sidecar import sidecar_payload, write_sidecars

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>  The Title  </dc:title>
    <dc:creator>Author One</dc:creator><dc:creator>Author Two</dc:creator>
    <dc:publisher>Publisher</dc:publisher><dc:date>2026-09-27</dc:date>
    <dc:language>th</dc:language><dc:subject>Fiction</dc:subject>
    <dc:identifier id="isbn">978-1-2345-6789-7</dc:identifier>
    <meta name="calibre:series" content="Series Name" />
    <meta name="calibre:series_index" content="1" />
    <meta name="cover" content="cover-image" />
  </metadata>
  <manifest><item id="cover-image" href="cover.jpg" media-type="image/jpeg" /></manifest>
</package>"""


def test_parse_normalizes_metadata_and_cover(tmp_path: Path) -> None:
    opf = tmp_path / "metadata.opf"
    (tmp_path / "cover.jpg").write_bytes(b"\xff\xd8\xffcover")
    opf.write_text(OPF, encoding="utf-8")

    document = parse_opf(opf, root=tmp_path)

    assert document.metadata.title == "The Title"
    assert document.metadata.authors == ["Author One", "Author Two"]
    assert document.metadata.series is not None
    assert document.metadata.series.number == 1
    assert document.metadata.isbn13 == "9781234567897"
    assert document.cover_path == (tmp_path / "cover.jpg").resolve()


def test_discovery_is_deterministic_and_rejects_ambiguity(tmp_path: Path) -> None:
    book = tmp_path / "book.epub"
    book.write_bytes(b"book")
    (tmp_path / "book.opf").write_text(OPF, encoding="utf-8")
    assert discover_opf(book, root=tmp_path).name == "book.opf"

    (tmp_path / "metadata.opf").write_text(OPF, encoding="utf-8")
    with pytest.raises(AmbiguousOPFError):
        discover_opf(book, root=tmp_path)


def test_discovery_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.opf"
    outside.write_text(OPF, encoding="utf-8")
    with pytest.raises(UnsafePathError):
        parse_opf(outside, root=tmp_path)


def test_sidecar_uses_object_cover_and_never_legacy_string(tmp_path: Path) -> None:
    metadata = CanonicalMetadata(title="Title", authors=["Author"])
    payload = sidecar_payload(metadata, "book.cover.jpg")
    assert payload["cover"] == {"source": "external", "path": "book.cover.jpg"}
    assert not isinstance(payload["cover"], str)

    book = tmp_path / "book.epub"
    book.write_bytes(b"original")
    cover = tmp_path / "source.jpg"
    cover.write_bytes(b"\xff\xd8\xffcover")
    metadata_path, cover_path = write_sidecars(book, metadata, cover)
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["cover"]["path"] == "book.cover.jpg"
    assert cover_path is not None and cover_path.read_bytes() == b"\xff\xd8\xffcover"
    assert book.read_bytes() == b"original"

import json

import pytest

from grimmlink_adapter.opf.sidecar import InvalidMetadataSidecarError, parse_metadata_sidecar


def _write_sidecar(path, cover_path: str) -> None:
    path.write_text(
        json.dumps({"metadata": {"title": "Blade Play Vol. 2"}, "cover": {"path": cover_path}}),
        encoding="utf-8",
    )


def test_missing_optional_cover_does_not_invalidate_metadata(tmp_path):
    sidecar = tmp_path / "book.metadata.json"
    _write_sidecar(sidecar, "missing.cover.jpg")

    parsed = parse_metadata_sidecar(sidecar, tmp_path)

    assert parsed.metadata.title == "Blade Play Vol. 2"
    assert parsed.cover_path is None


def test_existing_cover_is_retained(tmp_path):
    sidecar = tmp_path / "book.metadata.json"
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"jpeg")
    _write_sidecar(sidecar, cover.name)

    parsed = parse_metadata_sidecar(sidecar, tmp_path)

    assert parsed.cover_path == cover


def test_cover_path_outside_root_is_rejected(tmp_path):
    sidecar = tmp_path / "book.metadata.json"
    _write_sidecar(sidecar, "../outside.jpg")

    with pytest.raises(InvalidMetadataSidecarError):
        parse_metadata_sidecar(sidecar, tmp_path)

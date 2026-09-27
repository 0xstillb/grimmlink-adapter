"""Official-compatible sidecar generation with deterministic object schemas."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimmlink_adapter.models.opf_metadata import CanonicalMetadata


class SidecarConflictError(Exception):
    """An existing sidecar is not owned by GrimmLink Adapter."""


def sidecar_paths(book_path: Path) -> tuple[Path, Path]:
    return book_path.with_suffix(".metadata.json"), book_path.with_suffix(".cover.jpg")


def sidecar_payload(metadata: CanonicalMetadata, cover_name: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "generatedBy": "grimmlink-adapter",
        "metadata": metadata.api_payload(),
    }
    if cover_name:
        payload["cover"] = {"source": "external", "path": cover_name}
    return payload


def write_sidecars(
    book_path: Path, metadata: CanonicalMetadata, cover_path: Path | None,
) -> tuple[Path, Path | None]:
    metadata_path, cover_target = sidecar_paths(book_path)
    existing: dict[str, Any] | None = None
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SidecarConflictError("Existing metadata sidecar is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("generatedBy") != "grimmlink-adapter":
            raise SidecarConflictError("Refusing to overwrite a sidecar owned by another tool")
        existing = payload
    if cover_target.exists() and (
        not existing
        or not isinstance(existing.get("cover"), dict)
        or existing["cover"].get("path") != cover_target.name
    ):
        raise SidecarConflictError("Refusing to overwrite an unowned cover sidecar")

    cover_name = cover_target.name if cover_path else None
    cover_temp: str | None = None
    metadata_temp: str | None = None
    try:
        if cover_path:
            with cover_path.open("rb") as source:
                if source.read(3) != b"\xff\xd8\xff":
                    raise ValueError("Only JPEG OPF covers can be written as .cover.jpg")
            with tempfile.NamedTemporaryFile(
                dir=cover_target.parent, prefix=f".{cover_target.name}.", suffix=".tmp", delete=False,
            ) as temp_cover:
                cover_temp = temp_cover.name
            shutil.copyfile(cover_path, cover_temp)
            os.replace(cover_temp, cover_target)
            cover_temp = None

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=metadata_path.parent,
            prefix=f".{metadata_path.name}.", suffix=".tmp", delete=False,
        ) as temp_metadata:
            metadata_temp = temp_metadata.name
            temp_metadata.write(
                json.dumps(sidecar_payload(metadata, cover_name), ensure_ascii=False, indent=2) + "\n",
            )
            temp_metadata.flush()
            os.fsync(temp_metadata.fileno())
        os.replace(metadata_temp, metadata_path)
        metadata_temp = None
    finally:
        for temporary in (cover_temp, metadata_temp):
            if temporary:
                with suppress(FileNotFoundError):
                    os.unlink(temporary)
    if cover_path:
        return metadata_path, cover_target
    return metadata_path, None

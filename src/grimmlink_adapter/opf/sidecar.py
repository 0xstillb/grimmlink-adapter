"""Official-compatible sidecar generation with deterministic object schemas."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimmlink_adapter.models.opf_metadata import CanonicalMetadata


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
    cover_name = cover_target.name if cover_path else None
    metadata_path.write_text(
        json.dumps(sidecar_payload(metadata, cover_name), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if cover_path:
        shutil.copyfile(cover_path, cover_target)
        return metadata_path, cover_target
    return metadata_path, None

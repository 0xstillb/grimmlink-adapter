"""Shared OPF/sidecar source discovery and conservative field planning."""

import hashlib
import json
import os
from itertools import islice
from pathlib import Path
from typing import Any

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.opf_metadata import OPFDocument
from grimmlink_adapter.opf.parser import parse_opf
from grimmlink_adapter.opf.sidecar import InvalidMetadataSidecarError, parse_metadata_sidecar

BOOK_SUFFIXES = {".epub", ".pdf", ".cbz", ".cbr", ".djvu", ".fb2", ".mobi", ".azw3"}


def file_stamp(stat: os.stat_result) -> list[int]:
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def read_source(path: Path) -> tuple[OPFDocument, str] | None:
    """Parse and fingerprint the same bounded read; ignore generated sidecars."""
    before = path.stat()
    with path.open("rb") as stream:
        data = stream.read(settings.LOCAL_METADATA_MAX_SOURCE_BYTES + 1)
    after = path.stat()
    if len(data) > settings.LOCAL_METADATA_MAX_SOURCE_BYTES:
        raise ValueError("metadata_source_too_large")
    if file_stamp(before) != file_stamp(after) or len(data) != before.st_size:
        raise ValueError("source_changed_during_read")
    if path.name.lower().endswith(".metadata.json"):
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise InvalidMetadataSidecarError("Metadata sidecar must contain an object")
        if payload.get("generatedBy") == "grimmlink-adapter":
            return None
        document = parse_metadata_sidecar(path, path.parent, payload=payload)
    else:
        document = parse_opf(path, path.parent, data=data)
    return document, hashlib.sha256(data).hexdigest()


def source_candidates(book: Path) -> list[Path]:
    files = [book.with_suffix(".opf"), book.with_suffix(".OPF"), book.with_suffix(".metadata.json"), book.with_name(book.name + ".metadata.json")]
    generic = book.parent / "metadata.opf"
    if generic.is_file():
        targets = islice(
            (p for p in book.parent.iterdir() if p.suffix.lower() in BOOK_SUFFIXES and p.is_file()),
            2,
        )
        if len(list(targets)) == 1:
            files.append(generic)
    found: list[Path] = []
    for file in files:
        if not file.exists():
            continue
        if file.is_symlink() or file.resolve().parent != book.parent.resolve():
            raise ValueError("source_path_escape")
        if file not in found:
            found.append(file)
    return found


def equivalent(field: str, left: Any, right: Any) -> bool:
    if field == "categories" and isinstance(left, list) and isinstance(right, list):
        return set(left) == set(right)
    return bool(left == right)


def field_plan(source: dict[str, Any], current: dict[str, Any], baseline: dict[str, Any], fields: list[str], mode: str) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    conflicts: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    for field in fields:
        value = source.get(field)
        if value is None or value == "" or value == []:
            continue
        now = current.get(field)
        if equivalent(field, now, value):
            continue
        if current.get("allMetadataLocked") is True or current.get(field + "Locked") is True:
            skipped[field] = "locked"
        elif mode == "backfill_missing" and now not in (None, "", []):
            skipped[field] = "not_empty"
        elif field in baseline and not equivalent(field, now, baseline[field]) and mode != "backfill_source":
            conflicts[field] = {"grimmory": now, "source": value, "baseline": baseline[field]}
        else:
            changes[field] = value
    return {"changes": changes, "conflicts": conflicts, "skipped": skipped}

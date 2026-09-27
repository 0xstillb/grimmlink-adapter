"""Safe OPF discovery and normalization for Session 03A."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from grimmlink_adapter.models.opf_metadata import CanonicalMetadata, OPFDocument, SeriesMetadata


class OPFError(Exception):
    """Base parser/discovery error."""


class MalformedOPFError(OPFError):
    """The OPF is not valid XML or lacks required publication metadata."""


class UnsafePathError(OPFError):
    """A discovered OPF or cover escapes the permitted source root."""


class AmbiguousOPFError(OPFError):
    """More than one deterministic OPF candidate exists."""


def _contained(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafePathError(f"Path escapes source root: {path}") from exc
    return resolved


def discover_opf(source: Path, root: Path | None = None) -> Path:
    """Find one deterministic Calibre/adjacent OPF without title matching."""
    source = source.resolve(strict=False)
    source_root = (root or (source if source.is_dir() else source.parent)).resolve(strict=False)
    _contained(source, source_root)
    if source.is_file() and source.suffix.lower() == ".opf":
        return _contained(source, source_root)

    directory = source if source.is_dir() else source.parent
    stem = source.stem if source.is_file() else None
    candidates: list[Path] = []
    if stem:
        candidates.extend(sorted(directory.glob(f"{stem}.opf")))
        candidates.extend(sorted(directory.glob(f"{stem}.OPF")))
    metadata = directory / "metadata.opf"
    if metadata.exists():
        candidates.append(metadata)
    if not candidates and source.is_dir():
        candidates = sorted(directory.glob("*.opf")) + sorted(directory.glob("*.OPF"))
    unique = list(dict.fromkeys(_contained(candidate, source_root) for candidate in candidates))
    if len(unique) != 1:
        if not unique:
            raise OPFError(f"No OPF found beside {source}")
        raise AmbiguousOPFError(f"Multiple OPF candidates found beside {source}")
    return unique[0]


def parse_opf(opf_path: Path, root: Path | None = None) -> OPFDocument:
    """Parse Dublin Core and common Calibre metadata into canonical fields."""
    opf_path = _contained(opf_path, (root or opf_path.parent).resolve())
    try:
        tree = ET.parse(opf_path)
        root_element = tree.getroot()
    except (ET.ParseError, OSError) as exc:
        raise MalformedOPFError(f"Unable to parse OPF: {opf_path.name}") from exc

    metadata_element = next((element for element in root_element.iter() if _local(element.tag) == "metadata"), None)
    if metadata_element is None:
        raise MalformedOPFError("OPF metadata element is missing")

    values: dict[str, list[str]] = {}
    for element in metadata_element:
        key = _local(element.tag)
        text = _text(element)
        if text:
            values.setdefault(key, []).append(text)

    title = _first(values, "title")
    if not title:
        raise MalformedOPFError("OPF title is missing")
    subtitle = _meta_value(metadata_element, "title-type", "subtitle")
    authors = values.get("creator", [])
    publisher = _first(values, "publisher")
    published_date = _first(values, "date")
    description = _first(values, "description")
    language = _first(values, "language")
    categories = values.get("subject", [])
    isbn10, isbn13 = _extract_isbns(metadata_element)

    series_name = _meta_named(metadata_element, {"calibre:series", "series"})
    collection = _meta_property(metadata_element, "belongs-to-collection")
    if not series_name and collection:
        series_name = collection
    series_number = _meta_named(metadata_element, {"calibre:series_index", "group-position"})
    series_total = _meta_named(metadata_element, {"calibre:series_total"})
    series = (
        SeriesMetadata(name=series_name, number=cast(Any, series_number), total=cast(Any, series_total))
        if series_name else None
    )

    metadata = CanonicalMetadata(
        title=title, subtitle=subtitle, authors=authors, publisher=publisher,
        publishedDate=published_date, description=description, language=language,
        categories=categories, isbn10=isbn10, isbn13=isbn13, series=series,
    )
    cover_path = _find_cover(root_element, metadata_element, opf_path)
    return OPFDocument(
        source_path=opf_path,
        metadata=metadata,
        cover_path=cover_path,
        parsed_at=datetime.now(UTC),
    )


def file_fingerprint(path: Path) -> str:
    """Hash only the source artifact used for dedupe/safety checks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def grimmory_file_fingerprint(path: Path) -> str:
    """Match Grimmory FileFingerprint.generateHash partial-MD5 semantics."""
    digest = hashlib.md5()
    size = path.stat().st_size
    offset = 256
    with path.open("rb") as handle:
        while offset < size:
            handle.seek(offset)
            sample = handle.read(1024)
            if not sample:
                break
            digest.update(sample)
            offset *= 4
    return digest.hexdigest()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(element: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def _first(values: dict[str, list[str]], key: str) -> str | None:
    return values.get(key, [None])[0]


def _meta_value(parent: ET.Element, name: str, value: str) -> str | None:
    for element in parent:
        if _local(element.tag) == "meta" and element.attrib.get("name") == name and element.attrib.get("content") == value:
            return _text(element) or element.attrib.get("content")
        if element.attrib.get("property") == name and element.attrib.get("content") == value:
            return _text(element) or value
    return None


def _meta_named(parent: ET.Element, names: set[str]) -> str | None:
    for element in parent:
        if _local(element.tag) != "meta":
            continue
        key = element.attrib.get("name") or element.attrib.get("property")
        if key in names:
            return element.attrib.get("content") or _text(element) or None
    return None


def _meta_property(parent: ET.Element, name: str) -> str | None:
    for element in parent:
        if element.attrib.get("property") == name:
            return _text(element) or None
    return None


def _extract_isbns(parent: ET.Element) -> tuple[str | None, str | None]:
    isbn10 = isbn13 = None
    for element in parent:
        if _local(element.tag) != "identifier":
            continue
        value = re.sub(r"[^0-9Xx]", "", _text(element)).upper()
        scheme = " ".join(str(v).upper() for k, v in element.attrib.items() if _local(k) in {"scheme", "id"})
        if "ISBN" in scheme or len(value) in {10, 13}:
            if len(value) == 10 and isbn10 is None:
                isbn10 = value
            elif len(value) == 13 and isbn13 is None:
                isbn13 = value
    return isbn10, isbn13


def _find_cover(document: ET.Element, metadata: ET.Element, opf_path: Path) -> Path | None:
    cover_id = None
    for element in metadata:
        if _local(element.tag) == "meta" and element.attrib.get("name") == "cover":
            cover_id = element.attrib.get("content")
    for element in document.iter():
        if _local(element.tag) != "item":
            continue
        properties = set((element.attrib.get("properties") or "").split())
        if element.attrib.get("id") == cover_id or "cover-image" in properties:
            href = element.attrib.get("href")
            if href:
                return _contained((opf_path.parent / href).resolve(), opf_path.parent)
    for name in ("cover.jpg", "cover.jpeg", "cover.png"):
        candidate = opf_path.parent / name
        if candidate.exists():
            return _contained(candidate, opf_path.parent)
    return None

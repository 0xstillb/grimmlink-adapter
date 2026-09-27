"""Canonical metadata models used by Session 03A OPF ingestion."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SeriesMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    number: int | float | None = None
    total: int | float | None = None

    @field_validator("number", "total", mode="before")
    @classmethod
    def normalize_number(cls, value: Any) -> int | float | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError("series number must be numeric")
        number = float(value)
        return int(number) if number.is_integer() else number


class CanonicalMetadata(BaseModel):
    """Loss-minimized, API/sidecar-neutral metadata representation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    publisher: str | None = None
    published_date: str | None = Field(default=None, alias="publishedDate")
    description: str | None = None
    language: str | None = None
    categories: list[str] = Field(default_factory=list)
    isbn10: str | None = None
    isbn13: str | None = None
    series: SeriesMetadata | None = None

    def api_payload(self) -> dict[str, Any]:
        """Return the canonical field map used by API and sidecar adapters."""
        return self.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)

    def official_payload(self, book_id: int) -> dict[str, Any]:
        """Return Grimmory's ``MetadataUpdateWrapper`` request shape."""
        metadata = self.api_payload()
        series = metadata.pop("series", None)
        if series:
            metadata["seriesName"] = series["name"]
            if series.get("number") is not None:
                metadata["seriesNumber"] = series["number"]
            if series.get("total") is not None:
                metadata["seriesTotal"] = series["total"]
        metadata["bookId"] = book_id
        return {"metadata": metadata}


class OPFDocument(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    source_path: Path
    metadata: CanonicalMetadata
    cover_path: Path | None = None
    parsed_at: datetime


class IngestionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    opf_source: Path
    book_target: Path
    book_id: int
    fields_to_update: list[str]
    fields_skipped: list[str] = Field(default_factory=list)
    locked_fields: list[str] = Field(default_factory=list)
    cover_source: Path | None = None
    primary_action: str
    fallback_action: str
    would_modify_book_file: bool = False


class IngestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status: str
    book_id: int
    metadata_hash: str
    primary_action: str
    fallback_reason: str | None = None
    sidecar_metadata_path: Path | None = None
    sidecar_cover_path: Path | None = None
    preview: IngestionPreview | None = None

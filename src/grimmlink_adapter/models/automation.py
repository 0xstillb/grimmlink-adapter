"""Validated per-library metadata policies, never arbitrary filesystem roots."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

METADATA_FIELDS = (
    "title", "subtitle", "authors", "publisher", "publishedDate", "description",
    "language", "categories", "isbn10", "isbn13", "seriesName", "seriesNumber", "seriesTotal",
)


class LibraryMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    libraryPathId: int = Field(gt=0)
    rootIndex: int = Field(ge=0)
    rootPath: str | None = None  # Filled by the server to detect reordered roots.
    localSubpath: str | None = Field(default=None, max_length=1024)
    # A small file already present on the NAS; never created by the Adapter.
    marker: str = Field(min_length=1, max_length=512)

    @field_validator("localSubpath")
    @classmethod
    def valid_subpath(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.replace("\\", "/").split("/")
        if not value.strip() or value.startswith(("/", "\\")) or ":" in parts[0] or ".." in parts:
            raise ValueError("Choose a folder within the configured NAS root; use . for the root itself")
        return value.replace("\\", "/")


class LibraryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autoEnabled: bool = False
    intervalSeconds: int = Field(default=300, ge=60, le=86400)
    settleSeconds: int = Field(default=30, ge=10, le=3600)
    sourcePreference: Literal["review", "opf", "json"] = "opf"
    fields: list[str] = Field(default_factory=lambda: list(METADATA_FIELDS))
    mappings: list[LibraryMapping] = Field(min_length=1, max_length=32)

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)) or set(value) - set(METADATA_FIELDS):
            raise ValueError("Choose unique supported metadata fields")
        return value

    @field_validator("mappings")
    @classmethod
    def unique_paths(cls, value: list[LibraryMapping]) -> list[LibraryMapping]:
        if len({item.libraryPathId for item in value}) != len(value):
            raise ValueError("Map each library path only once")
        return value


class MetadataRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    libraryIds: list[int] = Field(min_length=1, max_length=100)
    mode: Literal["check", "preview", "backfill_missing", "backfill_source", "retry"] = "check"


class ResolveMetadataConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[str] = Field(min_length=1)
    choice: Literal["grimmory", "source"]
    revision: str

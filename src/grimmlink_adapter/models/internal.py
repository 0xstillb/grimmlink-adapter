"""Internal normalized data models used across adapter services and state."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NormalizedProgress(BaseModel):
    """Deprecated compatibility model; use :class:`ProgressSnapshot`.

    The old ``percentage`` field was ambiguous.  New code must use the
    explicitly unit-labelled fields below.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    book_id: int
    book_hash: str | None = None
    book_file_id: int | None = None
    format: str  # 'EPUB', 'PDF', etc.
    native_location: str | None = None  # CFI, XPointer, or page number
    display_percent: float | None = Field(default=None, validation_alias="percentage")  # 0.0 to 100.0
    official_fraction: float | None = None  # 0.0 to 1.0
    current_page: int | None = None
    total_pages: int | None = None
    device_id: str | None = None
    device_name: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @property
    def percentage(self) -> float | None:
        """Backward-compatible read-only alias for legacy callers."""
        return self.display_percent


class ProgressSnapshot(BaseModel):
    """Canonical progress representation with explicit percentage units.

    ``display_percent`` is the GrimmLink/UI value (0..100), while
    ``official_fraction`` is the value used by Official's KOReader API (0..1).
    Keeping both names explicit prevents an Official fraction from leaking into
    the legacy wire contract as a percentage.
    """

    model_config = ConfigDict(extra="ignore")

    book_hash: str
    book_id: int | None = None
    book_file_id: int | None = None
    format: str | None = None
    native_location: str | None = None
    current_page: int | None = None
    total_pages: int | None = None
    display_percent: float | None = None
    official_fraction: float | None = None
    device: str | None = None
    device_id: str | None = None
    timestamp_epoch: int | None = None
    updated_at: datetime | None = None
    source: str | None = None
    expected_updated_at: int | None = None
    force: bool = False

    @property
    def is_reflowable(self) -> bool:
        """Whether the snapshot uses a native reflowable location."""
        return (self.format or "").upper() in {"EPUB", "MOBI", "AZW3", "FB2", "HTML"}

    @property
    def is_fixed_page(self) -> bool:
        """Whether the snapshot uses fixed-page semantics."""
        return (self.format or "").upper() in {"PDF", "CBZ", "CBR", "DJVU"}


class OutboxAction(BaseModel):
    """An action queued in the outbox to be pushed to Official Grimmory."""

    model_config = ConfigDict(extra="ignore")

    id: str
    action_type: str  # 'PROGRESS_SYNC', 'SHELF_UNASSIGN', 'SESSION_RECORD', 'METADATA_PUSH'
    payload: dict[str, Any]
    idempotency_key: str
    status: str = "PENDING"
    retry_count: int = 0
    max_retries: int = 5
    last_error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CachedToken(BaseModel):
    """Cached Official JWT mapped to a username and MD5 password hash."""

    model_config = ConfigDict(extra="ignore")

    username: str
    password_hash: str
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BookHashEntry(BaseModel):
    """Cached hash-to-book mapping."""

    model_config = ConfigDict(extra="ignore")

    book_hash: str
    book_id: int
    book_file_id: int | None = None
    format: str | None = None
    file_size: int | None = None
    filename: str | None = None
    title: str | None = None
    cached_at: datetime = Field(default_factory=datetime.utcnow)


class ManagedFileRecord(BaseModel):
    """Local file marker used by the conservative shelf cleanup guard."""

    model_config = ConfigDict(extra="ignore")

    owner_key: str = "default"
    book_id: int
    book_file_id: int = 0
    tracked_path: str
    downloaded_by_grimmlink: bool = False
    provider_reference_count: int = 0
    expected_size: int | None = None
    cached_at: datetime = Field(default_factory=datetime.utcnow)


class NormalizedRating(BaseModel):
    """Normalized personal rating mapped between GrimmLink and Official Grimmory."""

    model_config = ConfigDict(extra="ignore")

    rating_value: float | None = None
    source_scale: int = 10
    official_rating: int | None = None
    is_reset: bool = False
    review: str | None = None
    dedupe_key: str | None = None
    updated_at: str | None = None


class NormalizedBookmark(BaseModel):
    """Normalized bookmark representation for Official CRUD and local mapping."""

    model_config = ConfigDict(extra="ignore")

    local_id: str
    remote_id: int | None = None
    book_id: int
    title: str | None = None
    notes: str | None = None
    chapter: str | None = None
    page_number: int | None = None
    cfi: str | None = None
    pos0: str | None = None
    pos1: str | None = None
    color: str | None = None
    deleted: bool = False
    dedupe_key: str | None = None


class NormalizedAnnotation(BaseModel):
    """Normalized annotation payload with explicit unsupported field preservation."""

    model_config = ConfigDict(extra="ignore")

    local_id: str
    remote_id: int | None = None
    book_id: int
    type: str | None = "highlight"
    text: str | None = None
    note: str | None = None
    color: str | None = None
    drawer: str | None = None
    style: str | None = None
    chapter: str | None = None
    page_number: int | None = None
    cfi: str | None = None
    pos0: str | None = None
    pos1: str | None = None
    deleted: bool = False
    dedupe_key: str | None = None
    unsupported_fields: dict[str, Any] = Field(default_factory=dict)


class MetadataDedupeRecord(BaseModel):
    """Record stored in SQLite applied-history for deduplication and cursor state."""

    model_config = ConfigDict(extra="ignore")

    owner_key: str
    book_id: int
    item_type: str  # 'rating', 'bookmark', 'annotation'
    dedupe_key: str
    content_hash: str
    device: str | None = None
    device_id: str | None = None
    source_scale: int | None = None
    source_value: float | None = None
    official_value: int | None = None
    official_id: int | None = None
    payload_json: str | None = None
    is_deleted: bool = False
    synced_at: datetime = Field(default_factory=datetime.utcnow)


class ReadingSessionRecord(BaseModel):
    """Auxiliary record for reading session idempotency and reconciliation state."""

    model_config = ConfigDict(extra="ignore")

    idempotency_key: str
    server: str
    user_id: str
    book_id: int
    book_hash: str | None = None
    book_type: str | None = None
    start_time: str
    end_time: str
    duration_seconds: int
    device: str | None = None
    device_id: str | None = None
    current_page: int | None = None
    total_pages: int | None = None
    start_progress: float | None = None
    end_progress: float | None = None
    start_page: int | None = None
    end_page: int | None = None
    start_location: str | None = None
    end_location: str | None = None
    official_session_id: int | None = None
    status: str = "PENDING"  # 'PENDING', 'POSTING', 'COMMITTED', 'REJECTED'
    retry_count: int = 0
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

"""Internal normalized data models used across adapter services and state."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NormalizedProgress(BaseModel):
    """Normalized cross-format reading progress."""

    model_config = ConfigDict(extra="ignore")

    book_id: int
    book_hash: str | None = None
    book_file_id: int | None = None
    format: str  # 'EPUB', 'PDF', etc.
    native_location: str | None = None  # CFI, XPointer, or page number
    percentage: float = 0.0  # 0.0 to 100.0
    current_page: int | None = None
    total_pages: int | None = None
    device_id: str | None = None
    device_name: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


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

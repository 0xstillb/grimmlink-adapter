"""Pydantic models representing the legacy GrimmLink wire contract (/api/grimmlink/v1/**)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GrimmlinkAuthResponse(BaseModel):
    """Response from GET /api/grimmlink/v1/auth."""

    model_config = ConfigDict(extra="ignore")

    status: str = "ok"
    username: str
    userId: int
    syncEnabled: bool | None = None
    syncWithWebReader: bool | None = None


class GrimmlinkCapabilitiesResponse(BaseModel):
    """Response from GET /api/grimmlink/v1/capabilities."""

    model_config = ConfigDict(extra="ignore")

    apiVersion: str = "v1"
    webUiProgress: bool = True
    progressSync: bool = True
    pdfBridge: bool = False
    readingSessions: bool = True
    metadataSync: bool = True
    shelves: bool = True


class GrimmlinkBookSummary(BaseModel):
    """Flat book summary DTO returned in shelf listings and lookups."""

    model_config = ConfigDict(extra="ignore")

    bookId: int
    bookFileId: int | None = None
    title: str | None = None
    author: str | None = None
    fileName: str | None = None
    originalFileName: str | None = None
    extension: str | None = None
    fileFormat: str | None = None
    fileSizeKb: int | None = None
    fileSize: int | None = None
    bookHash: str | None = None
    seriesName: str | None = None
    seriesNumber: float | None = None


class GrimmlinkShelfSummary(BaseModel):
    """Shelf summary DTO returned by GET /api/grimmlink/v1/shelves."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    type: str  # 'regular' or 'magic'
    visibility: str | None = "PRIVATE"
    bookCount: int | None = 0
    description: str | None = None


class GrimmlinkShelfRemovalResponse(BaseModel):
    """Response from POST /api/grimmlink/v1/shelves/{shelfId}/books/{bookId}/remove."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    message: str | None = None
    shelfId: int
    bookId: int
    shelfType: str = "regular"


class GrimmlinkLocationPayload(BaseModel):
    """Location information within a book (CFI, XPointer, or page)."""

    model_config = ConfigDict(extra="ignore")

    cfi: str | None = None
    xpointer: str | None = None
    page: int | None = None
    total_pages: int | None = None
    percentage: float | None = None


class GrimmlinkRatingPayload(BaseModel):
    """Personal rating item payload."""

    model_config = ConfigDict(extra="ignore")

    rating: float
    review: str | None = None


class GrimmlinkBookmarkPayload(BaseModel):
    """Bookmark item payload."""

    model_config = ConfigDict(extra="ignore")

    bookmark_id: str | None = None
    title: str | None = None
    location: GrimmlinkLocationPayload | None = None
    notes: str | None = None
    deleted: bool | None = False


class GrimmlinkAnnotationPayload(BaseModel):
    """Annotation item payload."""

    model_config = ConfigDict(extra="ignore")

    annotation_id: str | None = None
    text: str | None = None
    notes: str | None = None
    location: GrimmlinkLocationPayload | None = None
    color: str | None = None
    deleted: bool | None = False


class GrimmlinkMetadataSyncRequest(BaseModel):
    """Request payload for POST /api/grimmlink/v1/syncs/metadata."""

    model_config = ConfigDict(extra="ignore")

    bookId: int | None = None
    bookHash: str | None = None
    bookFileId: int | None = None
    deviceId: str | None = None
    device: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


class GrimmlinkItemResult(BaseModel):
    """Individual item result in batch operations."""

    model_config = ConfigDict(extra="ignore")

    itemId: str | None = None
    status: str  # 'SUCCESS', 'SKIPPED', 'DUPLICATE', 'FAILED'
    error: str | None = None


class GrimmlinkMetadataSyncResponse(BaseModel):
    """Response from metadata sync."""

    model_config = ConfigDict(extra="ignore")

    status: str = "ok"
    appliedCount: int = 0
    skippedCount: int = 0
    results: list[GrimmlinkItemResult] = Field(default_factory=list)


class GrimmlinkMetadataBatchResponse(BaseModel):
    """Response from POST /api/grimmlink/v1/syncs/metadata/batch."""

    model_config = ConfigDict(extra="ignore")

    status: str = "ok"
    totalItems: int = 0
    successCount: int = 0
    failureCount: int = 0
    results: list[GrimmlinkItemResult] = Field(default_factory=list)


class GrimmlinkMetadataPullItem(BaseModel):
    """Item returned in metadata pull query."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str  # 'RATING', 'BOOKMARK', 'ANNOTATION'
    deviceId: str | None = None
    contentHash: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    updatedAt: str | None = None


class GrimmlinkMetadataPullResponse(BaseModel):
    """Response from GET /api/grimmlink/v1/syncs/metadata."""

    model_config = ConfigDict(extra="ignore")

    items: list[GrimmlinkMetadataPullItem] = Field(default_factory=list)
    nextCursor: str | None = None
    hasMore: bool = False


class GrimmlinkReadingSessionItemRequest(BaseModel):
    """Single reading session request."""

    model_config = ConfigDict(extra="ignore")

    bookId: int
    bookHash: str | None = None
    startTime: str
    endTime: str
    startProgress: float | None = None
    endProgress: float | None = None
    startPage: int | None = None
    endPage: int | None = None
    deviceId: str | None = None
    device: str | None = None


class GrimmlinkReadingSessionBatchRequest(BaseModel):
    """Batch reading session request."""

    model_config = ConfigDict(extra="ignore")

    sessions: list[GrimmlinkReadingSessionItemRequest] = Field(default_factory=list)


class GrimmlinkReadingSessionBatchResponse(BaseModel):
    """Batch reading session response."""

    model_config = ConfigDict(extra="ignore")

    totalReceived: int = 0
    acceptedCount: int = 0
    duplicateCount: int = 0
    results: list[GrimmlinkItemResult] = Field(default_factory=list)


class GrimmlinkReadStatusRequest(BaseModel):
    """Request payload for updating book read status."""

    model_config = ConfigDict(extra="ignore")

    status: str | None = None


class KoreaderProgressPayload(BaseModel):
    """KOReader progress payload representation."""

    model_config = ConfigDict(extra="ignore")

    document: str | None = None
    progress: str | None = None
    percentage: float | None = None
    device: str | None = None
    device_id: str | None = None
    timestamp: int | None = None
    book_hash: str | None = None
    book_id: int | None = None
    book_file_id: int | None = None
    format: str | None = None
    current_page: int | None = None
    total_pages: int | None = None

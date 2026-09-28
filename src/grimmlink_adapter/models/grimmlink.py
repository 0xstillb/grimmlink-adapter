"""Pydantic models representing the legacy GrimmLink wire contract (/api/grimmlink/v1/**).

Frozen in Session 01 as an immutable specification of the fork and KOReader client
wire contract.
"""

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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
    visibility: str | None = "personal"
    bookCount: int | None = 0
    description: str | None = None


class GrimmlinkShelfRemovalResponse(BaseModel):
    """Response from POST /api/grimmlink/v1/shelves/{shelfId}/books/{bookId}/remove."""

    model_config = ConfigDict(extra="ignore")

    shelfId: int
    bookId: int
    shelfType: str = "regular"
    removed: bool = True
    status: str = "removed"
    message: str | None = None


class GrimmlinkReadStatusRequest(BaseModel):
    """Request payload for updating book read status."""

    model_config = ConfigDict(extra="ignore")

    status: str | None = None


class GrimmlinkReadStatusResponse(BaseModel):
    """Response payload after updating book read status."""

    model_config = ConfigDict(extra="ignore")

    bookId: int
    status: str
    updated: bool = True


class GrimmlinkReadStatusesResponse(BaseModel):
    """Response payload for supported read statuses list."""

    model_config = ConfigDict(extra="ignore")

    statuses: list[str] = Field(default_factory=list)


class GrimmlinkLocationPayload(BaseModel):
    """Location information within a book (CFI, XPointer, pos0/pos1, or page)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    pos0: str | None = None
    pos1: str | None = None
    pageno: int | None = None
    cfi: str | None = None
    raw: str | None = None
    xpointer: str | None = None
    page: int | None = None
    total_pages: int | None = None
    percentage: float | None = None


class GrimmlinkRatingPayload(BaseModel):
    """Personal rating item payload."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    dedupeKey: str | None = None
    value: float | int | None = None
    rating: float | int | None = None
    scale: int | None = 10
    source: str | None = "koreader"
    updatedAt: str | None = None
    review: str | None = None
    reset: bool = False
    deleted: bool = False


class GrimmlinkBookmarkPayload(BaseModel):
    """Bookmark item payload."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    dedupeKey: str | None = None
    bookmark_id: str | None = None
    title: str | None = None
    notes: str | None = None
    chapter: str | None = None
    page: int | None = None
    location: GrimmlinkLocationPayload | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    deleted: bool | None = False


class GrimmlinkAnnotationPayload(BaseModel):
    """Annotation item payload."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    dedupeKey: str | None = None
    annotation_id: str | None = None
    type: str | None = None
    text: str | None = None
    note: str | None = None
    color: str | None = None
    drawer: str | None = None
    style: str | None = None
    chapter: str | None = None
    page: int | None = None
    location: GrimmlinkLocationPayload | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    deleted: bool | None = False


class GrimmlinkItemResult(BaseModel):
    """Individual item result in batch operations."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str | None = None
    dedupeKey: str | None = None
    itemId: str | None = None
    status: str = "SUCCESS"  # 'SUCCESS', 'SKIPPED', 'DUPLICATE', 'FAILED', 'created', 'duplicate', 'error'
    id: str | None = None
    reason: str | None = None
    error: str | None = None


class GrimmlinkMetadataSyncResults(BaseModel):
    """Grouped result object within metadata sync response."""

    model_config = ConfigDict(extra="ignore")

    rating: GrimmlinkItemResult | None = None
    annotations: list[GrimmlinkItemResult] = Field(default_factory=list)
    bookmarks: list[GrimmlinkItemResult] = Field(default_factory=list)


class GrimmlinkMetadataSyncRequest(BaseModel):
    """Request payload for POST /api/grimmlink/v1/syncs/metadata."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schemaVersion: int | None = 1
    syncMode: str | None = "incremental"
    bookId: int | None = None
    bookHash: str | None = None
    bookFileId: int | None = None
    fileFormat: str | None = "EPUB"
    device: str | None = None
    deviceId: str | None = Field(default=None, validation_alias=AliasChoices("deviceId", "device_id"))
    timestamp: str | None = None
    since: str | None = None
    cursor: str | None = None
    limit: int | None = None
    type: str | None = None
    rating: GrimmlinkRatingPayload | None = None
    annotations: list[GrimmlinkAnnotationPayload] = Field(default_factory=list)
    bookmarks: list[GrimmlinkBookmarkPayload] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)


class GrimmlinkMetadataSyncResponse(BaseModel):
    """Response from metadata sync."""

    model_config = ConfigDict(extra="ignore")

    bookId: int | None = None
    ok: bool = True
    status: str = "ok"
    appliedCount: int = 0
    skippedCount: int = 0
    results: GrimmlinkMetadataSyncResults | list[GrimmlinkItemResult] | None = None


class GrimmlinkMetadataPullItem(BaseModel):
    """Item returned in metadata pull query."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    type: str  # 'rating', 'bookmark', 'annotation'
    bookId: int | None = None
    bookFileId: int | None = None
    dedupeKey: str | None = None
    contentHash: str | None = None
    payload: dict[str, Any] | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    payloadJson: str | None = None
    clientUpdatedAt: str | None = None
    syncedAt: str | None = None
    updatedAt: str | None = None
    device: str | None = None
    deviceId: str | None = Field(default=None, validation_alias=AliasChoices("deviceId", "device_id"))


class GrimmlinkMetadataPullResponse(BaseModel):
    """Response from GET /api/grimmlink/v1/syncs/metadata."""

    model_config = ConfigDict(extra="ignore")

    bookId: int | None = None
    bookFileId: int | None = None
    ok: bool = True
    since: str | None = None
    nextCursor: str | None = None
    limit: int | None = None
    items: list[GrimmlinkMetadataPullItem] = Field(default_factory=list)
    hasMore: bool = False


class GrimmlinkMetadataBatchResponse(BaseModel):
    """Response from POST /api/grimmlink/v1/syncs/metadata/batch."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    push: GrimmlinkMetadataSyncResponse | None = None
    pull: GrimmlinkMetadataPullResponse | None = None
    status: str = "ok"
    totalItems: int = 0
    successCount: int = 0
    failureCount: int = 0
    results: list[GrimmlinkItemResult] = Field(default_factory=list)


class GrimmlinkReadingSessionItemRequest(BaseModel):
    """Single reading session item inside batch or array."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    startTime: str
    endTime: str
    durationSeconds: int
    durationFormatted: str | None = None
    startProgress: float | None = None
    endProgress: float | None = None
    progressDelta: float | None = None
    startLocation: str | None = None
    endLocation: str | None = None
    startPage: int | None = None
    endPage: int | None = None


class GrimmlinkReadingSessionSingleRequest(BaseModel):
    """Single reading session request payload (POST /api/grimmlink/v1/reading-sessions)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    bookId: int
    bookType: str | None = "EPUB"
    bookHash: str | None = None
    device: str | None = None
    deviceId: str | None = Field(default=None, validation_alias=AliasChoices("deviceId", "device_id"))
    startTime: str
    endTime: str
    durationSeconds: int
    durationFormatted: str | None = None
    startProgress: float | None = None
    endProgress: float | None = None
    progressDelta: float | None = None
    startLocation: str | None = None
    endLocation: str | None = None
    currentPage: int | None = None
    totalPages: int | None = None
    startPage: int | None = None
    endPage: int | None = None



class GrimmlinkReadingSessionResponse(BaseModel):
    """Individual reading session item returned by GET /api/grimmlink/v1/reading-sessions."""

    model_config = ConfigDict(extra="ignore")

    id: int
    bookId: int
    bookTitle: str | None = None
    bookType: str | None = None
    startTime: str
    endTime: str
    durationSeconds: int
    startProgress: float | None = None
    endProgress: float | None = None
    progressDelta: float | None = None
    startLocation: str | None = None
    endLocation: str | None = None
    createdAt: str | None = None


class GrimmlinkReadingSessionBatchRequest(BaseModel):
    """Batch reading session request."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    bookId: int
    bookHash: str | None = None
    bookType: str | None = "EPUB"
    device: str | None = None
    deviceId: str | None = Field(default=None, validation_alias=AliasChoices("deviceId", "device_id"))
    sessions: list[GrimmlinkReadingSessionItemRequest] = Field(min_length=1, max_length=500)


class GrimmlinkReadingSessionResultItem(BaseModel):
    """Individual result item in batch reading session response."""

    model_config = ConfigDict(extra="ignore")

    index: int | None = None
    sessionId: int | None = None
    status: str  # 'created', 'duplicate', 'pending', 'rejected', 'error'
    message: str | None = None
    startTime: str | None = None
    endTime: str | None = None


class GrimmlinkReadingSessionBatchResponse(BaseModel):
    """Batch reading session response."""

    model_config = ConfigDict(extra="ignore")

    totalRequested: int
    successCount: int
    results: list[GrimmlinkReadingSessionResultItem] = Field(default_factory=list)


class KoreaderProgressPayload(BaseModel):
    """KOReader progress payload representation."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    document: str | None = None
    progress: str | None = None
    percentage: float | None = None
    device: str | None = None
    device_id: str | None = Field(default=None, validation_alias=AliasChoices("device_id", "deviceId"))
    timestamp: int | None = None
    bookHash: str | None = Field(default=None, validation_alias=AliasChoices("bookHash", "book_hash"))
    bookId: int | None = Field(default=None, validation_alias=AliasChoices("bookId", "book_id"))
    bookFileId: int | None = Field(default=None, validation_alias=AliasChoices("bookFileId", "book_file_id"))
    format: str | None = None
    fileFormat: str | None = None
    currentPage: int | None = Field(default=None, validation_alias=AliasChoices("currentPage", "current_page"))
    totalPages: int | None = Field(default=None, validation_alias=AliasChoices("totalPages", "total_pages"))
    location: str | None = None
    updatedAt: str | None = None
    updated: bool | None = None
    conflictDetected: bool | None = None
    conversionStatus: str | None = None
    message: str | None = None
    expectedUpdatedAt: int | None = None
    force: bool | None = None
    rawKoreaderLocation: str | None = None
    rawKoreaderProgress: str | None = None
    source: str | None = None
    currentHash: str | None = None
    initialHash: str | None = None


class GrimmlinkAuthErrorResponse(BaseModel):
    """Authentication filter error response shape."""

    model_config = ConfigDict(extra="ignore")

    status: str = "error"
    message: str


class GrimmlinkErrorResponse(BaseModel):
    """Spring GlobalExceptionHandler error response shape."""

    model_config = ConfigDict(extra="ignore")

    status: int
    message: str
    timestamp: str | None = None
    details: list[str] | None = None

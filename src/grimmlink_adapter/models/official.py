"""Pydantic models representing Official Grimmory API schemas."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OfficialLoginRequest(BaseModel):
    """Payload for POST /api/v1/auth/login."""

    model_config = ConfigDict(extra="ignore")

    username: str
    password: str


class OfficialLoginResponse(BaseModel):
    """Response from POST /api/v1/auth/login."""

    model_config = ConfigDict(extra="ignore")

    token: str
    refreshToken: str | None = None
    userId: int | None = None
    username: str | None = None


class OfficialRefreshTokenRequest(BaseModel):
    """Payload for POST /api/v1/auth/refresh."""

    model_config = ConfigDict(extra="ignore")

    refreshToken: str


class OfficialBookFileDTO(BaseModel):
    """BookFile DTO returned by the pinned Grimmory fork."""

    model_config = ConfigDict(extra="ignore")

    id: int
    bookId: int | None = None
    fileName: str | None = None
    filePath: str | None = None
    fileSubPath: str | None = None
    isBook: bool | None = None
    folderBased: bool | None = None
    bookType: str | None = None
    archiveType: str | None = None
    fileSizeKb: int | None = None
    extension: str | None = None
    description: str | None = None
    addedOn: str | None = None


class OfficialBookDTO(BaseModel):
    """Book DTO returned by the pinned Grimmory fork's by-hash route."""

    model_config = ConfigDict(extra="ignore")

    id: int
    libraryId: int | None = None
    libraryName: str | None = None
    primaryFile: OfficialBookFileDTO | None = None
    title: str | None = None
    lastReadTime: str | None = None
    addedOn: str | None = None
    metadata: dict[str, Any] | None = None
    metadataMatchScore: float | None = None
    pdfProgress: dict[str, Any] | None = None
    epubProgress: dict[str, Any] | None = None
    cbxProgress: dict[str, Any] | None = None
    audiobookProgress: dict[str, Any] | None = None
    koreaderProgress: dict[str, Any] | None = None
    koboProgress: dict[str, Any] | None = None
    personalRating: int | None = None
    shelves: list[dict[str, Any]] | None = None
    readStatus: str | None = None
    dateFinished: str | None = None
    libraryPath: dict[str, Any] | None = None
    alternativeFormats: list[OfficialBookFileDTO] | None = None
    supplementaryFiles: list[OfficialBookFileDTO] | None = None
    isPhysical: bool | None = None


class OfficialAppBookSummaryDTO(BaseModel):
    """Book summary returned by Official's paginated app shelf endpoint."""

    model_config = ConfigDict(extra="ignore")

    id: int
    title: str | None = None
    authors: list[str] | None = None
    seriesName: str | None = None
    seriesNumber: float | None = None
    primaryFileId: int | None = None
    primaryFileType: str | None = None
    primaryFileName: str | None = None
    fileSizeKb: int | None = None


class OfficialShelfDTO(BaseModel):
    """Official Grimmory regular shelf representation."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    description: str | None = None
    visibility: str | None = "PRIVATE"
    bookCount: int | None = 0


class OfficialMagicShelfDTO(BaseModel):
    """Official Grimmory magic shelf representation."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    description: str | None = None
    rule: str | None = None
    bookCount: int | None = 0


class OfficialBulkShelfAssignRequest(BaseModel):
    """Payload for bulk shelf assignments."""

    model_config = ConfigDict(extra="ignore")

    bookIds: list[int] = Field(default_factory=list)
    shelvesToAssign: list[int] = Field(default_factory=list)
    shelvesToUnassign: list[int] = Field(default_factory=list)


class OfficialReadingSessionDTO(BaseModel):
    """Official reading session payload for POST /api/v1/reading-sessions."""

    model_config = ConfigDict(extra="ignore")

    bookId: int
    startTime: str
    endTime: str
    startProgress: float | None = None
    endProgress: float | None = None
    startPage: int | None = None
    endPage: int | None = None


class OfficialBookmarkDTO(BaseModel):
    """Official bookmark representation returned by /api/v1/bookmarks."""

    model_config = ConfigDict(extra="ignore")

    id: int
    bookId: int
    cfi: str | None = None
    pageNumber: int | None = None
    title: str | None = None
    notes: str | None = None
    color: str | None = None
    priority: int | None = 1
    positionMs: int | None = None
    trackIndex: int | None = None


class OfficialCreateBookmarkRequest(BaseModel):
    """Payload for POST /api/v1/bookmarks."""

    model_config = ConfigDict(extra="ignore")

    bookId: int
    cfi: str | None = None
    pageNumber: int | None = None
    title: str | None = None
    notes: str | None = None
    color: str | None = None
    priority: int | None = 1
    positionMs: int | None = None
    trackIndex: int | None = None


class OfficialUpdateBookmarkRequest(BaseModel):
    """Payload for PUT /api/v1/bookmarks/{bookmarkId}."""

    model_config = ConfigDict(extra="ignore")

    cfi: str | None = None
    pageNumber: int | None = None
    title: str | None = None
    notes: str | None = None
    color: str | None = None
    priority: int | None = 1
    positionMs: int | None = None
    trackIndex: int | None = None


class OfficialPersonalRatingRequest(BaseModel):
    """Payload for PUT /api/v1/books/personal-rating."""

    model_config = ConfigDict(extra="ignore")

    ids: list[int] = Field(default_factory=list)
    rating: int = Field(ge=1, le=5)

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
    """Official Grimmory BookFile representation."""

    model_config = ConfigDict(extra="ignore")

    id: int
    fileName: str | None = None
    originalFileName: str | None = None
    extension: str | None = None
    fileFormat: str | None = None
    fileSizeKb: int | None = None
    fileSize: int | None = None
    hash: str | None = None


class OfficialBookDTO(BaseModel):
    """Official Grimmory Book representation."""

    model_config = ConfigDict(extra="ignore")

    id: int
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    description: str | None = None
    readStatus: str | None = None
    personalRating: int | float | None = None
    primaryFile: OfficialBookFileDTO | None = None
    files: list[OfficialBookFileDTO] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None


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

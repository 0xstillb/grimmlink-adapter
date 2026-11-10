"""Version 2 client-facing request and response models."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class V2SessionResponse(BaseModel):
    accessToken: str
    tokenType: Literal["Bearer"] = "Bearer"
    expiresAt: datetime
    username: str
    userId: int


class V2BookMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operationId: UUID
    metadata: dict[str, Any]
    mergeCategories: bool = False
    replaceMode: str = "REPLACE_WHEN_PROVIDED"


class V2LocalPasswordLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class V2LocalMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operationId: UUID
    libraryId: int = Field(gt=0)
    rootIndex: int = Field(ge=0)
    sourcePath: str = Field(min_length=1, max_length=1024)
    bookPath: str = Field(min_length=1, max_length=1024)


class V2Device(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str | None = None


class V2ProgressPosition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: str
    cfi: str | None = None
    xpointer: str | None = None
    href: str | None = None
    page: int | None = Field(default=None, ge=1)
    totalPages: int | None = Field(default=None, ge=1)


class V2ProgressRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operationId: UUID
    source: Literal["koreader", "readest"]
    device: V2Device | None = None
    updatedAt: datetime
    fraction: float = Field(ge=0.0, le=1.0)
    position: V2ProgressPosition
    expectedUpdatedAt: int | None = None
    force: bool = False


class V2ProgressResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    bookHash: str | None = None
    fraction: float | None = None
    nativeSync: str | None = None
    readStatus: str | None = None
    projection: str | None = None
    updatedAt: datetime | None = None
    position: V2ProgressPosition | None = None

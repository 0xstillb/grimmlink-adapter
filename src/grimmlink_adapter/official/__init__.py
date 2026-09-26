"""Official Grimmory upstream HTTP integration package."""

from grimmlink_adapter.official.auth import (
    PUBLIC_PATHS,
    AuthMode,
    JWTAuth,
    KOReaderAuth,
    determine_auth_mode_for_path,
    parse_jwt_expiry,
    validate_auth_separation,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.endpoints import (
    OFFICIAL_AUTH_LOGIN,
    OFFICIAL_AUTH_REFRESH,
    OFFICIAL_BOOK_BY_ID,
    OFFICIAL_BOOK_DOWNLOAD,
    OFFICIAL_BOOKMARKS,
    OFFICIAL_BOOKS,
    OFFICIAL_HEALTHCHECK,
    OFFICIAL_KOREADER_AUTH,
    OFFICIAL_KOREADER_PROGRESS,
    OFFICIAL_MAGIC_SHELVES,
    OFFICIAL_READING_SESSIONS,
    OFFICIAL_SHELVES,
    OFFICIAL_USERS_ME,
)
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialClientError,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.security.masking import redact_headers

__all__ = [
    "OfficialGrimmoryClient",
    "AuthMode",
    "JWTAuth",
    "KOReaderAuth",
    "PUBLIC_PATHS",
    "determine_auth_mode_for_path",
    "parse_jwt_expiry",
    "validate_auth_separation",
    "redact_headers",
    "OfficialClientError",
    "OfficialAuthError",
    "OfficialPermissionError",
    "OfficialTimeoutError",
    "OfficialTransportError",
    "OfficialBadResponseError",
    "OFFICIAL_HEALTHCHECK",
    "OFFICIAL_AUTH_LOGIN",
    "OFFICIAL_AUTH_REFRESH",
    "OFFICIAL_USERS_ME",
    "OFFICIAL_BOOK_BY_ID",
    "OFFICIAL_BOOK_DOWNLOAD",
    "OFFICIAL_BOOKMARKS",
    "OFFICIAL_BOOKS",
    "OFFICIAL_KOREADER_AUTH",
    "OFFICIAL_KOREADER_PROGRESS",
    "OFFICIAL_MAGIC_SHELVES",
    "OFFICIAL_READING_SESSIONS",
    "OFFICIAL_SHELVES",
]

"""Endpoint constants and URI builders for Official Grimmory HTTP API."""

# Health & Status
OFFICIAL_HEALTHCHECK = "/api/v1/healthcheck"

# Authentication & Users
OFFICIAL_AUTH_LOGIN = "/api/v1/auth/login"
OFFICIAL_AUTH_REFRESH = "/api/v1/auth/refresh"
OFFICIAL_USERS_ME = "/api/v1/users/me"

# KOReader Native Endpoints (KOReader MD5 header auth)
OFFICIAL_KOREADER_AUTH = "/api/koreader/users/auth"
OFFICIAL_KOREADER_PROGRESS = "/api/koreader/syncs/progress"
OFFICIAL_KOREADER_PROGRESS_HASH = "/api/koreader/syncs/progress/{bookHash}"

# Books & Search
OFFICIAL_BOOKS = "/api/v1/books"
OFFICIAL_BOOKS_PAGE = "/api/v1/books/page"
OFFICIAL_BOOKS_APP = "/api/v1/app/books"
OFFICIAL_BOOKS_APP_SEARCH = "/api/v1/app/books/search"
OFFICIAL_BOOK_BY_ID = "/api/v1/books/{bookId}"
OFFICIAL_BOOK_DOWNLOAD = "/api/v1/books/{bookId}/download"
OFFICIAL_BOOK_METADATA = "/api/v1/books/{bookId}/metadata"
OFFICIAL_BOOK_SIDECAR_IMPORT = "/api/v1/books/{bookId}/sidecar/import"
OFFICIAL_APP_SETTINGS = "/api/v1/settings"

# Shelves
OFFICIAL_SHELVES = "/api/v1/shelves"
OFFICIAL_MAGIC_SHELVES = "/api/v1/app/shelves/magic"
OFFICIAL_MAGIC_SHELF_BOOKS = "/api/v1/app/shelves/magic/{shelfId}/books"
OFFICIAL_SHELVES_ASSIGN = "/api/v1/shelves/assign"

# Bookmarks, Annotations & Rating
OFFICIAL_BOOKMARKS = "/api/v1/bookmarks"
OFFICIAL_BOOKMARK_BY_ID = "/api/v1/bookmarks/{bookmarkId}"
OFFICIAL_RATINGS = "/api/v1/ratings"

# Reading Sessions
OFFICIAL_READING_SESSIONS = "/api/v1/reading-sessions"

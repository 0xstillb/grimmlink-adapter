"""Business logic, mapping, and synchronization services."""

from grimmlink_adapter.services.auth_service import AuthService
from grimmlink_adapter.services.book_service import BookService
from grimmlink_adapter.services.metadata_service import MetadataService, normalize_rating
from grimmlink_adapter.services.progress_service import (
    ProgressService,
    calculate_display_percentage,
    grimmlink_percent_to_official_fraction,
    normalize_grimmlink_percent,
    official_fraction_to_display_percent,
    snapshot_from_grimmlink,
    snapshot_from_official,
    snapshot_to_grimmlink,
)
from grimmlink_adapter.services.session_service import (
    SessionService,
    generate_session_idempotency_key,
)
from grimmlink_adapter.services.shelf_service import ShelfService

__all__ = [
    "AuthService",
    "BookService",
    "MetadataService",
    "ProgressService",
    "SessionService",
    "ShelfService",
    "calculate_display_percentage",
    "grimmlink_percent_to_official_fraction",
    "generate_session_idempotency_key",
    "normalize_grimmlink_percent",
    "official_fraction_to_display_percent",
    "normalize_rating",
    "snapshot_from_grimmlink",
    "snapshot_from_official",
    "snapshot_to_grimmlink",
]

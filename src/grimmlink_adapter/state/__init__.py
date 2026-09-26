"""State, database connection, cache, and outbox queue management."""

from grimmlink_adapter.state.cache import BookHashCache, ShelfOwnershipCache, TokenCache
from grimmlink_adapter.state.database import get_connection, get_db_path, set_db_path
from grimmlink_adapter.state.migrations import apply_migrations
from grimmlink_adapter.state.outbox import IdempotencyManager, OutboxManager

__all__ = [
    "apply_migrations",
    "get_connection",
    "get_db_path",
    "set_db_path",
    "BookHashCache",
    "TokenCache",
    "ShelfOwnershipCache",
    "IdempotencyManager",
    "OutboxManager",
]

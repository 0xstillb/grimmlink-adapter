"""Database migration runner for SQLite auxiliary tables."""

import logging
from pathlib import Path

from grimmlink_adapter.state.database import get_connection

logger = logging.getLogger(__name__)


async def apply_migrations(migrations_dir: Path | None = None) -> int:
    """Read and apply all SQL migrations in order."""
    if migrations_dir is None:
        migrations_dir = Path(__file__).resolve().parent.parent.parent.parent / "migrations"

    if not migrations_dir.exists():
        logger.warning("Migrations directory %s not found", migrations_dir)
        return 0

    migration_files = sorted(migrations_dir.glob("*.sql"))
    applied_count = 0

    async with get_connection() as conn:
        # Ensure schema_version table exists first
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.commit()

        # Query already applied versions
        async with conn.execute("SELECT version FROM schema_version") as cursor:
            applied_versions = {row[0] for row in await cursor.fetchall()}

        for file in migration_files:
            # Assumes format: 001_initial_schema.sql -> version 1
            version_str = file.stem.split("_")[0]
            try:
                version = int(version_str)
            except ValueError:
                continue

            if version in applied_versions:
                continue

            logger.info("Applying migration version %d: %s", version, file.name)
            sql_content = file.read_text(encoding="utf-8")
            await conn.executescript(sql_content)
            await conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (version,))
            await conn.commit()
            applied_count += 1

    return applied_count

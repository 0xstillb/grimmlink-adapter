"""Configuration and environment settings for GrimmLink Adapter."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server binding
    ADAPTER_HOST: str = Field(default="0.0.0.0", description="Host to bind the adapter server")
    ADAPTER_PORT: int = Field(default=8085, description="Port to bind the adapter server")

    # Upstream Official Grimmory
    GRIMMORY_BASE_URL: str = Field(
        default="http://localhost:6060",
        description="Base URL of unmodified Official Grimmory server",
    )

    # Local SQLite Cache / Outbox DB
    SQLITE_DB_PATH: str = Field(
        default="data/grimmlink_adapter.db",
        description="Path to SQLite cache/outbox database file",
    )

    # Logging
    LOG_LEVEL: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Upstream HTTP Client options
    OFFICIAL_CLIENT_TIMEOUT: float = Field(
        default=15.0, description="HTTP client request timeout in seconds"
    )
    OFFICIAL_CLIENT_MAX_RETRIES: int = Field(
        default=3, description="Maximum retry count for idempotent upstream requests"
    )

    @property
    def sqlite_db_path_resolved(self) -> Path:
        """Return resolved Path to the SQLite DB, ensuring parent directory exists."""
        p = Path(self.SQLITE_DB_PATH).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()

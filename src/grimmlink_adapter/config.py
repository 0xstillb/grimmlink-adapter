"""Configuration and environment settings for GrimmLink Adapter."""

from pathlib import Path

from pydantic import Field, SecretStr
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
    GRIMMLINK_NATIVE_PROXY: bool = Field(
        default=False,
        description="Proxy MD5 GrimmLink book lookup directly to Official's native route",
    )

    # Local SQLite Cache / Outbox DB
    SQLITE_DB_PATH: str = Field(
        default="data/grimmlink_adapter.db",
        description="Path to SQLite cache/outbox database file",
    )

    # Optional read-only Grimmory MariaDB identity lookup. The account must be
    # provisioned with SELECT-only grants outside this application.
    GRIMMORY_DB_ENABLED: bool = Field(
        default=False, description="Enable exact-hash lookup against Grimmory MariaDB"
    )
    GRIMMORY_DB_HOST: str | None = Field(default=None, description="MariaDB host on a restricted network")
    GRIMMORY_DB_PORT: int = Field(default=3306, description="MariaDB TCP port")
    GRIMMORY_DB_NAME: str = Field(default="grimmory", description="Grimmory MariaDB schema")
    GRIMMORY_DB_USER: str | None = Field(default=None, description="Dedicated SELECT-only account")
    GRIMMORY_DB_PASSWORD: SecretStr | None = Field(
        default=None, description="Password for the dedicated SELECT-only account"
    )
    GRIMMORY_DB_SSL_CA: str | None = Field(
        default=None, description="CA bundle to verify MariaDB TLS when using a remote network"
    )
    GRIMMORY_DB_CONNECT_TIMEOUT: float = Field(
        default=5.0, description="MariaDB connection timeout in seconds"
    )
    GRIMMORY_DB_QUERY_TIMEOUT: float = Field(
        default=15.0, description="MariaDB identity query timeout in seconds"
    )

    # Logging
    LOG_LEVEL: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Upstream HTTP Client options
    OFFICIAL_CLIENT_TIMEOUT: float = Field(
        default=15.0, description="HTTP client request timeout in seconds"
    )
    OFFICIAL_CLIENT_CONNECT_TIMEOUT: float = Field(
        default=5.0, description="HTTP client connect timeout in seconds"
    )
    OFFICIAL_CLIENT_MAX_RETRIES: int = Field(
        default=3, description="Maximum retry count for idempotent upstream requests"
    )
    OFFICIAL_CLIENT_BACKOFF_FACTOR: float = Field(
        default=0.5, description="Initial backoff multiplier in seconds for retryable errors"
    )
    OFFICIAL_CLIENT_VERIFY_SSL: bool = Field(
        default=True, description="Enforce SSL/TLS certificate verification"
    )
    OFFICIAL_CLIENT_CA_BUNDLE: str | None = Field(
        default=None,
        description="Path to custom CA certificate bundle for self-signed certificates",
    )

    # Shelf mutation and local cleanup safety
    SHELF_CLEANUP_ENABLED: bool = Field(
        default=False,
        description="Allow deletion of adapter-managed local files after verified shelf removal",
    )
    SHELF_MANAGED_FILE_ROOT: str | None = Field(
        default=None,
        description="Optional root directory that managed-file cleanup must remain inside",
    )

    # OPF Ingestion & Fallback options (Session 03A)
    METADATA_INGESTION_MODE: str = Field(
        default="api_preferred",
        description="Metadata ingestion mode: api_only, api_preferred, sidecar_only",
    )
    METADATA_FALLBACK: str = Field(
        default="sidecar",
        description="Metadata fallback strategy: sidecar or none",
    )

    # Migrations directory
    MIGRATIONS_DIR: str = Field(
        default="migrations",
        description="Path to SQL migrations directory",
    )

    @property
    def sqlite_db_path_resolved(self) -> Path:
        """Return resolved Path to the SQLite DB, ensuring parent directory exists."""
        p = Path(self.SQLITE_DB_PATH).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def migrations_dir_resolved(self) -> Path:
        """Resolve the migrations directory path reliably across local dev and containerized runs."""
        candidates = [
            Path(self.MIGRATIONS_DIR).resolve(),
            Path.cwd() / self.MIGRATIONS_DIR,
            Path("/app/migrations"),
            Path(__file__).resolve().parent.parent.parent.parent / "migrations",
            Path(__file__).resolve().parent.parent.parent / "migrations",
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate
        return Path(self.MIGRATIONS_DIR).resolve()


settings = Settings()

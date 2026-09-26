"""Shared pytest fixtures and test configuration."""

from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from grimmlink_adapter.main import create_app
from grimmlink_adapter.state.database import set_db_path
from grimmlink_adapter.state.migrations import apply_migrations


@pytest.fixture(scope="session", autouse=True)
def configure_test_environment(tmp_path_factory: pytest.TempPathFactory) -> Generator[None, None, None]:
    """Point database to a temporary location for testing."""
    test_dir = tmp_path_factory.mktemp("grimmlink_test_data")
    db_file = test_dir / "test_grimmlink.db"
    set_db_path(db_file)
    yield


@pytest_asyncio.fixture(autouse=True)
async def initialize_test_database() -> AsyncGenerator[None, None]:
    """Ensure database migrations are applied before each test."""
    await apply_migrations()
    yield


@pytest_asyncio.fixture
async def test_client() -> AsyncGenerator[AsyncClient, None]:
    """Asynchronous test client bound to the FastAPI application."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

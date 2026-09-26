"""Integration tests for application lifecycle, healthcheck, and startup failures."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from grimmlink_adapter.main import create_app


@pytest.mark.asyncio
async def test_healthcheck_endpoint(test_client: AsyncClient) -> None:
    resp = await test_client.get("/healthcheck")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_openapi_schema_generated(test_client: AsyncClient) -> None:
    resp = await test_client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["info"]["title"] == "GrimmLink Adapter"
    assert "/api/grimmlink/v1/auth" in data["paths"]
    assert "/api/grimmlink/v1/capabilities" in data["paths"]
    assert "/healthcheck" in data["paths"]


@pytest.mark.asyncio
async def test_startup_fails_when_migrations_fail() -> None:
    """Issue 4 fix verification: Application lifespan must fail startup if migrations fail."""
    app = create_app()
    with patch(
        "grimmlink_adapter.main.apply_migrations",
        side_effect=FileNotFoundError("Migrations directory not found"),
    ):
        with pytest.raises(FileNotFoundError, match="Migrations directory not found"):
            async with app.router.lifespan_context(app):
                pass

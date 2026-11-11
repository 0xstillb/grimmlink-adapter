"""Integration tests for application lifecycle, healthcheck, and startup failures."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from grimmlink_adapter.main import create_app
from grimmlink_adapter.official.exceptions import OfficialTransportError


@pytest.mark.asyncio
async def test_healthcheck_endpoint(test_client: AsyncClient) -> None:
    resp = await test_client.get("/healthcheck")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_readiness_requires_official_grimmory_but_liveness_stays_up(
    test_client: AsyncClient,
) -> None:
    class UnavailableClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def check_health(self):
            raise OfficialTransportError("connection refused")

    with patch("grimmlink_adapter.main.OfficialGrimmoryClient", return_value=UnavailableClient()):
        ready = await test_client.get("/readyz")
        live = await test_client.get("/healthcheck")

    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert ready.json()["upstream"] == "grimmory_unavailable"
    assert live.status_code == 200


@pytest.mark.asyncio
async def test_readiness_returns_ready_when_official_grimmory_responds(
    test_client: AsyncClient,
) -> None:
    class AvailableClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def check_health(self):
            return {"status": "ok"}

    with patch("grimmlink_adapter.main.OfficialGrimmoryClient", return_value=AvailableClient()):
        ready = await test_client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["upstream"] == "ok"


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

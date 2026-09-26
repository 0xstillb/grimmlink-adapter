"""Integration tests for application lifecycle and healthcheck."""

import pytest
from httpx import AsyncClient


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

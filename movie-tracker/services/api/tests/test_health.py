from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.database.session import get_database_session
from app.main import app


@pytest.mark.anyio
async def test_health_returns_service_status(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "Movie Tracker API"


@pytest.mark.anyio
async def test_readiness_checks_database(client: AsyncClient) -> None:
    session = AsyncMock()

    async def override_session():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_database_session] = override_session
    try:
        response = await client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}
    session.execute.assert_awaited_once()

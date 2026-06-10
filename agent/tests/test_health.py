import httpx
import pytest

from app.main import app


@pytest.mark.anyio
async def test_health():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["models_loaded"] is False


@pytest.fixture
def anyio_backend():
    return "asyncio"

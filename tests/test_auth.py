import httpx
import pytest

from alves_ia.main import app


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/me", "/v1/clinic"])
async def test_missing_token_is_rejected_before_database_access(path: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

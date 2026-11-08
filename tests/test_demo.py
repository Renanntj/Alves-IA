from collections.abc import Iterator

import httpx
import pytest

from alves_ia import demo
from alves_ia.config import get_settings


@pytest.fixture(autouse=True)
def demo_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_demo_reply_uses_history(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_generate(prompt: str) -> str:
        assert '"content": "Olá"' in prompt
        return "Olá! Sou Alves-Agent."

    monkeypatch.setattr(demo, "generate_reply", fake_generate)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=demo.app), base_url="http://localhost"
    ) as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert "Alves-Agent" in page.text
        assert "test-only" not in page.text
        response = await client.post(
            "/chat",
            headers={"X-Demo-Request": "1"},
            json={"messages": [{"role": "user", "content": "Olá"}]},
        )
        assert response.status_code == 200
        assert response.json()["reply"] == "Olá! Sou Alves-Agent."


@pytest.mark.asyncio
async def test_demo_blocks_remote_clients() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=demo.app, client=("192.0.2.1", 123)),
        base_url="http://localhost",
    ) as client:
        assert (await client.get("/")).status_code == 403


@pytest.mark.asyncio
async def test_demo_blocks_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=demo.app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/")).status_code == 403


@pytest.mark.asyncio
async def test_demo_sanitizes_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(prompt: str) -> str:
        raise RuntimeError("private-api-key")

    monkeypatch.setattr(demo, "generate_reply", broken)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=demo.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/chat",
            headers={"X-Demo-Request": "1"},
            json={"messages": [{"role": "user", "content": "Olá"}]},
        )
        assert response.status_code == 502
        assert "private-api-key" not in response.text

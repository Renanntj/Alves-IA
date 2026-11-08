import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from alves_ia.config import get_settings
from alves_ia.main import app
from alves_ia.whatsapp.client import send_text
from alves_ia.whatsapp.protocol import parse_events, valid_signature, window_open


def test_signature_covers_exact_body() -> None:
    body = b'{"test": true}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert valid_signature(body, signature, "secret")
    assert not valid_signature(body + b" ", signature, "secret")
    assert not valid_signature(body, signature, "")


def test_message_replay_uses_same_event_key() -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "456"},
                            "messages": [
                                {
                                    "id": "wamid.abc",
                                    "from": "5581999999999",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "Olá"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    assert (
        parse_events(json.dumps(payload).encode())[0].event_key
        == parse_events(json.dumps(payload, indent=2).encode())[0].event_key
    )


@pytest.mark.parametrize(
    "hours, expected", [(0, True), (23.99, True), (24, False), (25, False), (-1, False)]
)
def test_customer_service_window(hours: float, expected: bool) -> None:
    now = datetime.now(UTC)
    assert window_open(now - timedelta(hours=hours), now) is expected


@pytest.mark.parametrize(
    "status,state", [(200, "done"), (400, "failed"), (429, "pending"), (503, "unknown")]
)
def test_send_response_classification(status: int, state: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.facebook.com"
        assert request.headers["authorization"] == "Bearer test-token"
        assert json.loads(request.content)["text"]["body"] == "Olá"
        return httpx.Response(status, json={"messages": [{"id": "wamid.out"}]})

    result = send_text(
        "456", "5581999999999", "Olá", "test-token", "v23.0", httpx.MockTransport(handle)
    )
    assert result.state == state


def test_timeout_is_not_blindly_retried() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("uncertain", request=request)

    result = send_text(
        "456", "5581999999999", "Olá", "test-token", "v23.0", httpx.MockTransport(handle)
    )
    assert result.state == "unknown"


@pytest.mark.asyncio
async def test_webhook_verification_and_signature_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-test")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret-test")
    get_settings.cache_clear()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/webhooks/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "verify-test",
                    "hub.challenge": "1234",
                },
            )
            assert response.status_code == 200
            assert response.text == "1234"
            rejected = await client.post("/webhooks/whatsapp", content=b"{}")
            assert rejected.status_code == 401
            body = b"not-json"
            signature = "sha256=" + hmac.new(b"secret-test", body, hashlib.sha256).hexdigest()
            malformed = await client.post(
                "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": signature}
            )
            assert malformed.status_code == 400
    finally:
        get_settings.cache_clear()

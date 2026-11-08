import re
from typing import Any

import httpx


class SendOutcome:
    def __init__(
        self, state: str, provider_id: str | None = None, error_code: str | None = None
    ) -> None:
        self.state = state
        self.provider_id = provider_id
        self.error_code = error_code


def send_text(
    phone_id: str,
    contact: str,
    body: str,
    token: str,
    version: str,
    transport: httpx.BaseTransport | None = None,
) -> SendOutcome:
    if not token or not re.fullmatch(r"v\d+\.\d+", version):
        return SendOutcome("failed", error_code="configuration")
    if not re.fullmatch(r"\d{1,64}", phone_id):
        return SendOutcome("failed", error_code="invalid_phone_id")
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            response = client.post(
                f"https://graph.facebook.com/{version}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": contact,
                    "type": "text",
                    "text": {"body": body},
                },
            )
        if response.status_code == 429:
            return SendOutcome("pending", error_code="rate_limited")
        if response.status_code >= 500:
            return SendOutcome("unknown", error_code="provider_error")
        if not response.is_success:
            return SendOutcome("failed", error_code=f"http_{response.status_code}")
        data: dict[str, Any] = response.json()
        provider_id = data["messages"][0]["id"]
        if not isinstance(provider_id, str) or len(provider_id) > 256:
            raise ValueError("Invalid provider id")
        return SendOutcome("done", provider_id=provider_id)
    except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError):
        return SendOutcome("unknown", error_code="uncertain_response")

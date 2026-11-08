import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    phone_id: str = Field(pattern=r"^\d{1,64}$")
    waba_id: str = Field(pattern=r"^\d{1,64}$")
    event_key: str
    payload: dict[str, Any]


def valid_signature(body: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.encode(), expected.encode())


def parse_events(body: bytes) -> list[Event]:
    data = json.loads(body)
    if data.get("object") != "whatsapp_business_account":
        raise ValueError("Unsupported webhook object")
    events = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change["value"]
            phone = value["metadata"]["phone_number_id"]
            for kind in ("messages", "statuses"):
                for item in value.get(kind, []):
                    if not isinstance(item["id"], str) or not 1 <= len(item["id"]) <= 256:
                        raise ValueError("Invalid message id")
                    # Delivery statuses are separate events; message retries share one key.
                    parts = [phone, kind, item["id"]]
                    if kind == "statuses":
                        if not isinstance(item["status"], str) or len(item["status"]) > 32:
                            raise ValueError("Invalid status")
                        parts += [item["status"], item["timestamp"]]
                    else:
                        sender = str(item["from"])
                        if not sender.isascii() or not sender.isdigit() or len(sender) > 32:
                            raise ValueError("Invalid sender")
                    datetime.fromtimestamp(int(item["timestamp"]), UTC)
                    key = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
                    events.append(
                        Event(
                            phone_id=phone,
                            waba_id=entry["id"],
                            event_key=key,
                            payload={"kind": kind, "data": item},
                        )
                    )
    return events


def window_open(last_inbound: datetime, now: datetime) -> bool:
    return timedelta(0) <= now - last_inbound < timedelta(hours=24)

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from alves_ia.auth import Database, current_identity
from alves_ia.config import get_settings
from alves_ia.security import Identity

from .models import Conversation, Outbox
from .protocol import Event, parse_events, valid_signature
from .service import enqueue_text, receive

router = APIRouter(tags=["whatsapp"])
Authenticated = Annotated[Identity, Depends(current_identity)]


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
async def verify(
    mode: Annotated[str, Query(alias="hub.mode")],
    token: Annotated[str, Query(alias="hub.verify_token")],
    challenge: Annotated[str, Query(alias="hub.challenge", max_length=1024)],
) -> str:
    expected = get_settings().whatsapp_verify_token.get_secret_value()
    if not expected:
        raise HTTPException(503, "Webhook not configured")
    if mode != "subscribe" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, "Invalid verification token")
    return challenge


async def signed_events(request: Request) -> list[Event]:
    secret = get_settings().whatsapp_app_secret.get_secret_value()
    if not secret:
        raise HTTPException(503, "Webhook not configured")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise HTTPException(413, "Webhook too large")
    raw = bytes(body)
    if not valid_signature(raw, request.headers.get("x-hub-signature-256", ""), secret):
        raise HTTPException(401, "Invalid signature")
    try:
        return parse_events(raw)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError) as exc:
        raise HTTPException(400, "Invalid webhook payload") from exc


@router.post("/webhooks/whatsapp")
def webhook(
    events: Annotated[list[Event], Depends(signed_events)], session: Database
) -> dict[str, bool]:
    try:
        receive(session, events)
        # The acknowledgement must follow the commit, not dependency teardown.
        session.commit()
    except LookupError as exc:
        raise HTTPException(503, "Channel not configured or disabled") from exc
    return {"received": True}


class TextRequest(BaseModel):
    conversation_id: UUID
    body: str = Field(min_length=1, max_length=4096)


@router.get("/v1/whatsapp/conversations")
def conversations(identity: Authenticated, session: Database) -> list[dict[str, str]]:
    rows = session.scalars(
        select(Conversation)
        .where(
            Conversation.clinic_id == identity.clinic_id,
        )
        .order_by(Conversation.last_inbound.desc())
        .limit(100)
    )
    return [
        {"id": str(row.id), "contact": row.contact, "last_inbound": row.last_inbound.isoformat()}
        for row in rows
    ]


@router.post("/v1/whatsapp/messages", status_code=202)
def send(
    body: TextRequest,
    identity: Authenticated,
    session: Database,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> dict[str, str]:
    try:
        job = enqueue_text(
            session, identity.clinic_id, body.conversation_id, body.body, idempotency_key
        )
        result = {"id": str(job.id), "state": job.state}
        session.commit()
        return result
    except LookupError as exc:
        raise HTTPException(404, "Conversation not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/v1/whatsapp/messages/{message_id}")
def message(message_id: UUID, identity: Authenticated, session: Database) -> dict[str, str | None]:
    job = session.get(Outbox, (identity.clinic_id, message_id))
    if not job or job.kind != "send":
        raise HTTPException(404, "Message not found")
    return {
        "id": str(job.id),
        "state": job.state,
        "delivery_status": job.delivery_status,
        "error_code": job.error_code,
    }

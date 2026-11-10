import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from alves_ia.config import get_settings
from alves_ia.database import get_session, set_clinic
from alves_ia.main import app as api_app
from alves_ia.models import Clinic, User
from alves_ia.whatsapp import processing
from alves_ia.whatsapp.client import SendOutcome
from alves_ia.whatsapp.handoff import HandoffAction
from alves_ia.whatsapp.models import (
    ChannelRoute,
    Conversation,
    ConversationEvent,
    ConversationMessage,
    Inbox,
    Outbox,
)
from alves_ia.whatsapp.protocol import Event
from alves_ia.whatsapp.service import (
    enqueue_text,
    ingest,
    receive,
    transition_conversation,
)


@pytest.mark.asyncio
async def test_durable_receive_deduplication_window_and_tenant_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_url, app_url = os.getenv("TEST_OWNER_DATABASE_URL"), os.getenv("TEST_DATABASE_URL")
    if not owner_url or not app_url:
        pytest.skip("Dedicated PostgreSQL test URLs required")
    owner, app = create_engine(owner_url), create_engine(app_url)
    clinic_id, other_id = uuid4(), uuid4()
    user_id = uuid4()
    phone = str(uuid4().int)
    event = Event(
        phone_id=phone,
        waba_id="123",
        event_key="a" * 64,
        payload={
            "kind": "messages",
            "data": {
                "id": "wamid.test",
                "from": "5581999999999",
                "timestamp": str(int(datetime.now(UTC).timestamp())),
                "type": "text",
            },
        },
    )
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "integration-secret")
    monkeypatch.setenv("AI_LIVE_ENABLED", "false")
    get_settings.cache_clear()

    def session_override() -> Iterator[Session]:
        with Session(app) as session:
            yield session

    api_app.dependency_overrides[get_session] = session_override
    try:
        with Session(owner) as session, session.begin():
            session.add_all([Clinic(id=clinic_id, name="A"), Clinic(id=other_id, name="B")])
            session.add(
                User(id=user_id, email=f"{user_id}@test.invalid", password_hash="integration-only")
            )
            session.flush()
            session.add(ChannelRoute(phone_id=phone, waba_id="123", clinic_id=clinic_id))
        raw = json.dumps(
            {
                "object": "whatsapp_business_account",
                "entry": [
                    {
                        "id": "123",
                        "changes": [
                            {
                                "field": "messages",
                                "value": {
                                    "metadata": {"phone_number_id": phone},
                                    "messages": [event.payload["data"]],
                                },
                            }
                        ],
                    }
                ],
            }
        ).encode()
        signature = "sha256=" + hmac.new(b"integration-secret", raw, hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app),
            base_url="http://test",
        ) as client:
            for _ in range(2):
                response = await client.post(
                    "/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": signature}
                )
                assert response.status_code == 200
        with Session(app) as session, session.begin():
            set_clinic(session, clinic_id)
            assert session.scalar(select(func.count()).select_from(Inbox)) == 1
            job = session.scalar(select(Outbox))
            assert job is not None
            ingest(session, job)
        with Session(app) as session:
            set_clinic(session, clinic_id)
            conversation = session.scalar(select(Conversation))
            assert conversation is not None
            conversation_id = conversation.id
            transition_conversation(
                session, clinic_id, conversation_id, user_id, HandoffAction.REQUEST
            )
            transition_conversation(
                session, clinic_id, conversation_id, user_id, HandoffAction.CLAIM
            )
            with pytest.raises(PermissionError):
                enqueue_text(
                    session,
                    clinic_id,
                    conversation.id,
                    "Resposta do bot",
                    "bot-request",
                    uuid4(),
                    origin="agent",
                )
            first = enqueue_text(session, clinic_id, conversation.id, "Olá", "request-1", user_id)
            again = enqueue_text(session, clinic_id, conversation.id, "Olá", "request-1", user_id)
            assert first.id == again.id
            message_id = first.id
            with pytest.raises(ValueError):
                enqueue_text(session, clinic_id, conversation.id, "Diferente", "request-1", user_id)
            session.commit()
        calls: list[str] = []

        def fake_send(*args: object) -> SendOutcome:
            calls.append("sent")
            return SendOutcome("done", provider_id="wamid.sent")

        monkeypatch.setattr(processing, "get_engine", lambda: app)
        monkeypatch.setattr(processing, "send_text", fake_send)
        processing.process_job(str(clinic_id), str(message_id))
        processing.process_job(str(clinic_id), str(message_id))
        assert calls == ["sent"]
        with Session(app) as session:
            set_clinic(session, clinic_id)
            sent = session.get(Outbox, (clinic_id, message_id))
            assert sent is not None and sent.state == "done"
            assert sent.provider_id == "wamid.sent"
            session.commit()
            transition_conversation(
                session, clinic_id, conversation_id, user_id, HandoffAction.RESUME_BOT
            )
            set_clinic(session, clinic_id)
            bot_job = enqueue_text(
                session,
                clinic_id,
                conversation_id,
                "Resposta do bot",
                "bot-request",
                uuid4(),
                origin="agent",
            )
            bot_message_id = bot_job.id
            session.commit()
        processing.process_job(str(clinic_id), str(bot_message_id))
        assert calls == ["sent", "sent"]
        if os.getenv("TEST_QUEUE") == "1":
            from alves_ia.whatsapp import tasks

            queued = event.model_copy(update={"event_key": "b" * 64})
            with Session(app) as session, session.begin():
                receive(session, [queued])
            monkeypatch.setattr(tasks, "get_engine", lambda: app)
            tasks.dispatch()
            deadline = time.monotonic() + 30
            while True:
                with Session(app) as session, session.begin():
                    set_clinic(session, clinic_id)
                    received = session.scalar(select(Inbox).where(Inbox.event_key == "b" * 64))
                    if received is not None and received.processed:
                        break
                assert time.monotonic() < deadline, "Celery did not process durable event in 30s"
                time.sleep(0.2)
        with Session(app) as session, session.begin():
            set_clinic(session, other_id)
            assert session.scalars(select(Inbox)).all() == []
            assert session.scalars(select(Conversation)).all() == []
            assert session.scalars(select(Outbox)).all() == []
    finally:
        api_app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()
        with owner.begin() as connection:
            for model in (
                ConversationMessage,
                ConversationEvent,
                Outbox,
                Inbox,
                Conversation,
                ChannelRoute,
            ):
                connection.execute(delete(model).where(model.clinic_id == clinic_id))
            connection.execute(delete(User).where(User.id == user_id))
            connection.execute(delete(Clinic).where(Clinic.id.in_([clinic_id, other_id])))
        app.dispose()
        owner.dispose()

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from alves_ia.config import get_settings
from alves_ia.database import set_clinic
from alves_ia.models import Clinic

from .handoff import ConversationState, HandoffAction, next_state
from .models import (
    ChannelRoute,
    Conversation,
    ConversationEvent,
    ConversationMessage,
    Inbox,
    Outbox,
)
from .protocol import Event, window_open


@contextmanager
def conversation_lock(session: Session, clinic_id: UUID, conversation_id: UUID) -> Iterator[None]:
    lock_id = int.from_bytes(
        hashlib.blake2b(f"{clinic_id}:{conversation_id}".encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )
    session.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": lock_id})
    session.commit()  # Session-scoped lock survives this commit.
    try:
        yield
    finally:
        session.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})


def transition_conversation(
    session: Session,
    clinic_id: UUID,
    conversation_id: UUID,
    actor_id: UUID | None,
    action: HandoffAction,
    reason: str | None = None,
) -> tuple[str, UUID | None]:
    set_clinic(session, clinic_id)
    with conversation_lock(session, clinic_id, conversation_id):
        set_clinic(session, clinic_id)
        conversation = session.get(Conversation, (clinic_id, conversation_id))
        if conversation is None:
            raise LookupError("Conversation not found")
        state = ConversationState(conversation.state)
        target = next_state(state, action)
        if action == HandoffAction.CLAIM and actor_id is None:
            raise ValueError("A user is required to claim a conversation")
        if action == HandoffAction.CLAIM and conversation.assigned_user_id:
            raise ValueError("Conversation already assigned")
        if action == HandoffAction.RESUME_BOT and (
            actor_id is None or conversation.assigned_user_id != actor_id
        ):
            from alves_ia.models import Membership

            membership = (
                session.get(Membership, (clinic_id, actor_id)) if actor_id is not None else None
            )
            if not membership or membership.role not in {"admin", "manager"}:
                raise PermissionError("Only the assigned attendant or a manager can resume the bot")
        if action in {HandoffAction.BOT_REPLY, HandoffAction.HUMAN_REPLY}:
            return conversation.state, conversation.assigned_user_id
        previous = conversation.state
        conversation.state = target.value
        conversation.assigned_user_id = (
            actor_id
            if action == HandoffAction.CLAIM and actor_id is not None
            else None
            if action in {HandoffAction.RESUME_BOT, HandoffAction.CLOSE}
            else conversation.assigned_user_id
        )
        if (
            action
            in {
                HandoffAction.REQUEST,
                HandoffAction.CLAIM,
                HandoffAction.RESUME_BOT,
                HandoffAction.CLOSE,
            }
            and previous != target.value
        ):
            session.add(
                ConversationEvent(
                    clinic_id=clinic_id,
                    conversation_id=conversation_id,
                    actor_user_id=actor_id,
                    action=action.value,
                    from_state=previous,
                    to_state=target.value,
                    reason=reason,
                )
            )
        # Commit before releasing the session-level advisory lock. A queued bot
        # worker therefore observes HUMAN_PENDING before it can start a reply.
        result = (conversation.state, conversation.assigned_user_id)
        session.commit()
        return result


def receive(session: Session, events: list[Event]) -> None:
    for event in events:
        route = session.get(ChannelRoute, event.phone_id)
        if not route or not route.active or route.waba_id != event.waba_id:
            raise LookupError("Channel not configured")
        set_clinic(session, route.clinic_id)
        clinic = session.get(Clinic, route.clinic_id)
        if not clinic or not clinic.active:
            raise LookupError("Clinic disabled")
        event_id = uuid4()
        result = session.scalar(
            insert(Inbox)
            .values(
                clinic_id=route.clinic_id,
                id=event_id,
                event_key=event.event_key,
                phone_id=event.phone_id,
                payload=event.payload,
                processed=False,
            )
            .on_conflict_do_nothing(index_elements=["clinic_id", "event_key"])
            .returning(Inbox.id)
        )
        if result is not None:
            session.add(
                Outbox(
                    clinic_id=route.clinic_id,
                    kind="ingest",
                    key=f"ingest:{event_id}",
                    payload={"inbox_id": str(event_id)},
                )
            )
            # Flush before another tenant context is selected in this batch.
            session.flush()


def ingest(session: Session, job: Outbox) -> None:
    event = session.get(Inbox, (job.clinic_id, UUID(job.payload["inbox_id"])))
    if event is None:
        raise ValueError("Missing inbox event")
    item = event.payload["data"]
    if event.payload["kind"] == "messages":
        timestamp = min(datetime.fromtimestamp(int(item["timestamp"]), UTC), datetime.now(UTC))
        statement = insert(Conversation).values(
            clinic_id=job.clinic_id,
            id=uuid4(),
            phone_id=event.phone_id,
            contact=item["from"],
            last_inbound=timestamp,
        )
        result = session.execute(
            statement.on_conflict_do_update(
                index_elements=["clinic_id", "phone_id", "contact"],
                set_={"last_inbound": func.greatest(Conversation.last_inbound, timestamp)},
            ).returning(Conversation.id, Conversation.state)
        ).one()
        if item.get("type") == "text":
            content = str(item.get("text", {}).get("body", ""))[:4000]
            session.execute(
                insert(ConversationMessage)
                .values(
                    clinic_id=job.clinic_id,
                    id=uuid4(),
                    conversation_id=result.id,
                    source_inbox_id=event.id,
                    role="user",
                    content=content,
                )
                .on_conflict_do_nothing(index_elements=["clinic_id", "source_inbox_id"])
            )
            if (
                result.state == ConversationState.BOT_ACTIVE.value
                and content
                and get_settings().ai_live_enabled
            ):
                session.add(
                    Outbox(
                        clinic_id=job.clinic_id,
                        kind="agent_turn",
                        key=f"agent:{event.id}",
                        origin="agent",
                        payload={"conversation_id": str(result.id), "inbox_id": str(event.id)},
                    )
                )
    else:
        outgoing = session.scalar(
            select(Outbox)
            .where(
                Outbox.clinic_id == job.clinic_id,
                Outbox.provider_id == item["id"],
            )
            .with_for_update()
        )
        rank = {"failed": 0, "sent": 1, "delivered": 2, "read": 3}
        if outgoing is None and job.attempts < 5:
            # A status may beat the HTTP sender's commit. Keep the durable event
            # pending briefly; unmatched events remain available in the Inbox.
            job.attempts += 1
            job.available_at = datetime.now(UTC) + timedelta(seconds=60)
            return
        if outgoing and rank.get(item["status"], -1) > rank.get(outgoing.delivery_status or "", -1):
            outgoing.delivery_status = item["status"]
    event.processed = True
    job.state = "done"


def enqueue_text(
    session: Session,
    clinic_id: UUID,
    conversation_id: UUID,
    body: str,
    key: str,
    actor_id: UUID,
    origin: str = "human",
) -> Outbox:
    if origin not in {"human", "agent"}:
        raise ValueError("Invalid message origin")
    payload = {"conversation_id": str(conversation_id), "body": body, "actor_id": str(actor_id)}
    existing = session.scalar(
        select(Outbox).where(
            Outbox.clinic_id == clinic_id,
            Outbox.key == f"send:{key}",
        )
    )
    if existing:
        if existing.payload != payload:
            raise ValueError("Idempotency key reused with different content")
        return existing
    conversation = session.get(Conversation, (clinic_id, conversation_id))
    if not conversation:
        raise LookupError("Conversation not found")
    allowed = (
        conversation.state == ConversationState.BOT_ACTIVE.value
        if origin == "agent"
        else conversation.state == ConversationState.HUMAN_ACTIVE.value
        and conversation.assigned_user_id == actor_id
    )
    if not allowed:
        raise PermissionError("Conversation is not assigned to this message sender")
    if not window_open(conversation.last_inbound, datetime.now(UTC)):
        raise ValueError("Customer service window closed")
    session.execute(
        insert(Outbox)
        .values(
            clinic_id=clinic_id,
            id=uuid4(),
            key=f"send:{key}",
            kind="send",
            payload=payload,
            origin=origin,
        )
        .on_conflict_do_nothing(index_elements=["clinic_id", "key"])
    )
    job = session.scalar(
        select(Outbox).where(
            Outbox.clinic_id == clinic_id,
            Outbox.key == f"send:{key}",
        )
    )
    if job is None or job.payload != payload:
        raise ValueError("Idempotency key conflict")
    return job

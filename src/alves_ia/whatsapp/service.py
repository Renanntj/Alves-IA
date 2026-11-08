from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from alves_ia.database import set_clinic
from alves_ia.models import Clinic

from .models import ChannelRoute, Conversation, Inbox, Outbox
from .protocol import Event, window_open


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
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["clinic_id", "phone_id", "contact"],
                set_={"last_inbound": func.greatest(Conversation.last_inbound, timestamp)},
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
    session: Session, clinic_id: UUID, conversation_id: UUID, body: str, key: str
) -> Outbox:
    payload = {"conversation_id": str(conversation_id), "body": body}
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

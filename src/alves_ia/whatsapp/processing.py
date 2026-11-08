from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from alves_ia.config import get_settings
from alves_ia.database import get_engine, set_clinic
from alves_ia.models import Clinic

from .client import send_text
from .models import ChannelRoute, Conversation, Outbox
from .protocol import window_open
from .service import ingest


def process_job(clinic: str, job_id: str) -> None:
    clinic_id, identifier = UUID(clinic), UUID(job_id)
    with Session(get_engine()) as session, session.begin():
        set_clinic(session, clinic_id)
        job = session.scalar(
            select(Outbox)
            .where(
                Outbox.clinic_id == clinic_id,
                Outbox.id == identifier,
            )
            .with_for_update()
        )
        if not job or job.state != "pending":
            return
        if job.kind == "ingest":
            ingest(session, job)
            return
        conversation = session.get(Conversation, (clinic_id, UUID(job.payload["conversation_id"])))
        route = session.get(ChannelRoute, conversation.phone_id) if conversation else None
        tenant = session.get(Clinic, clinic_id)
        if not conversation or not route or not route.active or not tenant or not tenant.active:
            job.state, job.error_code = "failed", "channel_disabled"
            return
        if not window_open(conversation.last_inbound, datetime.now(UTC)):
            job.state, job.error_code = "failed", "window_closed"
            return
        phone_id, contact, body = conversation.phone_id, conversation.contact, job.payload["body"]
        job.state = "processing"
        job.attempts += 1
        job.available_at = datetime.now(UTC)
    # Persist the attempt before I/O. A crash cannot trigger a blind resend.
    settings = get_settings()
    outcome = send_text(
        phone_id,
        contact,
        body,
        settings.whatsapp_access_token.get_secret_value(),
        settings.whatsapp_api_version,
    )
    with Session(get_engine()) as session, session.begin():
        set_clinic(session, clinic_id)
        job = session.get(Outbox, (clinic_id, identifier), with_for_update=True)
        if job is None:
            return
        job.state = outcome.state
        job.provider_id = outcome.provider_id
        job.error_code = outcome.error_code
        if outcome.state == "pending":
            job.state = "pending" if job.attempts < 5 else "failed"
            job.available_at = datetime.now(UTC) + timedelta(seconds=30 * 2**job.attempts)

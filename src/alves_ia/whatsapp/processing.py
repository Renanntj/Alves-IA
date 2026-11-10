from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from alves_ia.ai import AIReply, create_provider
from alves_ia.config import get_settings
from alves_ia.database import get_engine, set_clinic
from alves_ia.models import Clinic

from .client import send_text
from .handoff import ConversationState, HandoffAction, next_state
from .models import ChannelRoute, Conversation, ConversationMessage, Inbox, Outbox
from .protocol import window_open
from .service import conversation_lock, ingest
from .urgency import is_urgent


class HandoffReason(StrEnum):
    CUSTOMER_REQUEST = "customer_request"
    URGENT_RISK = "urgent_risk"
    UNCLEAR_REQUEST = "unclear_request"
    OTHER = "other"


class HandoffArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: HandoffReason


def process_job(clinic: str, job_id: str) -> None:
    clinic_id, identifier = UUID(clinic), UUID(job_id)
    with Session(get_engine()) as session:
        set_clinic(session, clinic_id)
        initial = session.get(Outbox, (clinic_id, identifier))
        if not initial or initial.state != "pending":
            return
        if initial.kind == "ingest":
            ingest(session, initial)
            session.commit()
            return
        conversation_id = UUID(initial.payload["conversation_id"])
        session.commit()
        # A session advisory lock remains held across commits and provider I/O.
        # Handoff and sending therefore serialize on the same conversation.
        with conversation_lock(session, clinic_id, conversation_id):
            set_clinic(session, clinic_id)
            queued = session.get(Outbox, (clinic_id, identifier))
            queued_conversation = session.get(Conversation, (clinic_id, conversation_id))
            if not queued or queued.state != "pending":
                session.commit()
                return
            if queued and queued.kind == "agent_turn" and queued_conversation:
                _run_agent_turn(
                    session,
                    clinic_id,
                    queued,
                    queued_conversation,
                    UUID(queued.payload["inbox_id"]),
                )
                session.commit()
                return
            session.commit()
            with session.begin():
                set_clinic(session, clinic_id)
                job = session.get(Outbox, (clinic_id, identifier), with_for_update=True)
                conversation = session.get(Conversation, (clinic_id, conversation_id))
                if not job or job.state != "pending" or not conversation:
                    return
                if job.kind == "agent_turn":
                    _run_agent_turn(
                        session, clinic_id, job, conversation, UUID(job.payload["inbox_id"])
                    )
                    return
                action = (
                    HandoffAction.BOT_REPLY if job.origin == "agent" else HandoffAction.HUMAN_REPLY
                )
                try:
                    next_state(ConversationState(conversation.state), action)
                except ValueError:
                    job.state, job.error_code = "failed", "conversation_not_owned_by_sender"
                    return
                if job.origin == "human" and str(conversation.assigned_user_id) != str(
                    job.payload.get("actor_id")
                ):
                    job.state, job.error_code = "failed", "conversation_not_owned_by_sender"
                    return
                route = session.get(ChannelRoute, conversation.phone_id)
                tenant = session.get(Clinic, clinic_id)
                if not route or not route.active or not tenant or not tenant.active:
                    job.state, job.error_code = "failed", "channel_disabled"
                    return
                if not window_open(conversation.last_inbound, datetime.now(UTC)):
                    job.state, job.error_code = "failed", "window_closed"
                    return
                phone_id, contact, body = (
                    conversation.phone_id,
                    conversation.contact,
                    job.payload["body"],
                )
                job.state = "processing"
                job.attempts += 1
                job.available_at = datetime.now(UTC)
            settings = get_settings()
            outcome = send_text(
                phone_id,
                contact,
                body,
                settings.whatsapp_access_token.get_secret_value(),
                settings.whatsapp_api_version,
            )
            with session.begin():
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


def _run_agent_turn(
    session: Session, clinic_id: UUID, job: Outbox, conversation: Conversation, inbox_id: UUID
) -> None:
    if not get_settings().ai_live_enabled:
        job.state, job.error_code = "failed", "ai_disabled"
        return
    inbound = session.get(Inbox, (clinic_id, inbox_id))
    message = session.scalar(
        select(ConversationMessage).where(
            ConversationMessage.clinic_id == clinic_id,
            ConversationMessage.source_inbox_id == inbox_id,
            ConversationMessage.role == "user",
        )
    )
    if (
        inbound is None
        or message is None
        or conversation.state != ConversationState.BOT_ACTIVE.value
    ):
        job.state, job.error_code = "failed", "conversation_not_bot_active"
        return
    if is_urgent(message.content):
        from .service import transition_conversation

        transition_conversation(
            session, clinic_id, conversation.id, None, HandoffAction.REQUEST, "urgent_risk"
        )
        session.begin()
        set_clinic(session, clinic_id)
        completed_job = session.get(Outbox, (clinic_id, job.id))
        if completed_job:
            completed_job.state = "done"
        return
    history_rows = list(
        session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.clinic_id == clinic_id,
                ConversationMessage.conversation_id == conversation.id,
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(12)
        )
    )
    history = [{"role": row.role, "content": row.content} for row in reversed(history_rows)]
    session.commit()
    try:
        reply: AIReply = create_provider().reply(history)
    except Exception:
        session.begin()
        set_clinic(session, clinic_id)
        failed = session.get(Outbox, (clinic_id, job.id))
        if failed:
            failed.state, failed.error_code = "failed", "ai_provider_error"
        return
    if reply.tool_calls:
        if len(reply.tool_calls) != 1 or reply.tool_calls[0].name != "request_human":
            session.begin()
            set_clinic(session, clinic_id)
            failed = session.get(Outbox, (clinic_id, job.id))
            if failed:
                failed.state, failed.error_code = "failed", "invalid_tool_call"
            return
        try:
            arguments = HandoffArguments.model_validate(reply.tool_calls[0].arguments)
        except ValidationError:
            session.begin()
            set_clinic(session, clinic_id)
            failed = session.get(Outbox, (clinic_id, job.id))
            if failed:
                failed.state, failed.error_code = "failed", "invalid_tool_arguments"
            return
        from .service import transition_conversation

        transition_conversation(
            session,
            clinic_id,
            conversation.id,
            None,
            HandoffAction.REQUEST,
            arguments.reason.value,
        )
        session.begin()
        set_clinic(session, clinic_id)
        completed = session.get(Outbox, (clinic_id, job.id))
        if completed:
            completed.state = "done"
        return
    answer = reply.text.strip()[:4000]
    session.begin()
    set_clinic(session, clinic_id)
    latest = session.get(Conversation, (clinic_id, conversation.id))
    active_job = session.get(Outbox, (clinic_id, job.id))
    if not active_job:
        return
    if not latest or latest.state != ConversationState.BOT_ACTIVE.value:
        active_job.state, active_job.error_code = "failed", "handoff_interrupted"
        return
    if not answer:
        active_job.state, active_job.error_code = "failed", "empty_ai_reply"
        return
    session.add(
        ConversationMessage(
            clinic_id=clinic_id,
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
        )
    )
    session.add(
        Outbox(
            clinic_id=clinic_id,
            kind="send",
            origin="agent",
            key=f"reply:{inbox_id}",
            payload={"conversation_id": str(conversation.id), "body": answer, "actor_id": "system"},
        )
    )
    active_job.state = "done"

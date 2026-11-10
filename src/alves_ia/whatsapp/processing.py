from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from alves_ia.ai import AIReply, create_provider
from alves_ia.config import get_settings
from alves_ia.database import get_engine, set_clinic
from alves_ia.models import Clinic
from alves_ia.sheets.google_api import GoogleSheetsAPIError
from alves_ia.sheets.models import GoogleSheetsConnection
from alves_ia.sheets.service import search_sheet

from .audio import AudioFile, AudioMediaError, download_audio, transcribe_audio
from .client import send_text
from .handoff import ConversationState, HandoffAction, next_state
from .models import ChannelRoute, Conversation, ConversationMessage, Inbox, Outbox
from .protocol import window_open
from .service import conversation_lock, ingest, transition_conversation
from .urgency import is_urgent


class HandoffReason(StrEnum):
    CUSTOMER_REQUEST = "customer_request"
    URGENT_RISK = "urgent_risk"
    UNCLEAR_REQUEST = "unclear_request"
    OTHER = "other"


class HandoffArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: HandoffReason


class SheetSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=200)


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
            if queued_conversation and queued.kind in {"agent_turn", "transcribe_audio"}:
                if queued.kind == "agent_turn":
                    _run_agent_turn(
                        session,
                        clinic_id,
                        queued,
                        queued_conversation,
                        UUID(queued.payload["inbox_id"]),
                    )
                else:
                    _run_audio_transcription(
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
                if job.kind == "transcribe_audio":
                    _run_audio_transcription(
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
    job_id, conversation_id = job.id, conversation.id
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
        transition_conversation(
            session, clinic_id, conversation_id, None, HandoffAction.REQUEST, "urgent_risk"
        )
        session.begin()
        set_clinic(session, clinic_id)
        completed_job = session.get(Outbox, (clinic_id, job_id))
        if completed_job:
            completed_job.state = "done"
        return
    history_rows = list(
        session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.clinic_id == clinic_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(12)
        )
    )
    history = [{"role": row.role, "content": row.content} for row in reversed(history_rows)]
    session.commit()
    settings = get_settings()
    provider = create_provider()
    set_clinic(session, clinic_id)
    sheet_connection = session.get(GoogleSheetsConnection, clinic_id)
    sheet_available = bool(
        sheet_connection
        and sheet_connection.spreadsheet_id
        and sheet_connection.range_name
        and sheet_connection.allowed_columns
    )
    session.commit()
    try:
        if sheet_available:
            reply: AIReply = provider.reply(history, sheet_search=True)
        else:
            reply = provider.reply(history)
    except Exception:
        session.begin()
        set_clinic(session, clinic_id)
        failed = session.get(Outbox, (clinic_id, job_id))
        if failed:
            failed.state, failed.error_code = "failed", "ai_provider_error"
        return
    if (
        reply.tool_calls
        and len(reply.tool_calls) == 1
        and reply.tool_calls[0].name == "search_clinic_sheet"
    ):
        if not sheet_available:
            _finish_as_handoff(session, clinic_id, job_id, conversation_id, "other")
            return
        try:
            arguments = SheetSearchArguments.model_validate(reply.tool_calls[0].arguments)
        except ValidationError:
            session.begin()
            set_clinic(session, clinic_id)
            failed = session.get(Outbox, (clinic_id, job_id))
            if failed:
                failed.state, failed.error_code = "failed", "invalid_tool_arguments"
            return
        try:
            reference_data = search_sheet(session, clinic_id, arguments.query, settings)
            session.commit()
            if reference_data:
                reply = provider.reply(history, reference_data=reference_data)
            else:
                reply = AIReply(
                    "Não encontrei essa informação na planilha. "
                    "Posso encaminhar você para uma pessoa."
                )
        except (GoogleSheetsAPIError, LookupError, ValueError, RuntimeError):
            _finish_as_handoff(session, clinic_id, job_id, conversation_id, "other")
            return
        except Exception:
            _finish_as_handoff(session, clinic_id, job_id, conversation_id, "other")
            return
    if reply.tool_calls:
        if len(reply.tool_calls) != 1 or reply.tool_calls[0].name != "request_human":
            session.begin()
            set_clinic(session, clinic_id)
            failed = session.get(Outbox, (clinic_id, job_id))
            if failed:
                failed.state, failed.error_code = "failed", "invalid_tool_call"
            return
        try:
            handoff_arguments = HandoffArguments.model_validate(reply.tool_calls[0].arguments)
        except ValidationError:
            session.begin()
            set_clinic(session, clinic_id)
            failed = session.get(Outbox, (clinic_id, job_id))
            if failed:
                failed.state, failed.error_code = "failed", "invalid_tool_arguments"
            return
        transition_conversation(
            session,
            clinic_id,
            conversation_id,
            None,
            HandoffAction.REQUEST,
            handoff_arguments.reason.value,
        )
        session.begin()
        set_clinic(session, clinic_id)
        completed = session.get(Outbox, (clinic_id, job_id))
        if completed:
            completed.state = "done"
        return
    answer = reply.text.strip()[:4000]
    session.begin()
    set_clinic(session, clinic_id)
    latest = session.get(Conversation, (clinic_id, conversation_id))
    active_job = session.get(Outbox, (clinic_id, job_id))
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
            conversation_id=conversation_id,
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
            payload={"conversation_id": str(conversation_id), "body": answer, "actor_id": "system"},
        )
    )
    active_job.state = "done"


def _finish_as_handoff(
    session: Session,
    clinic_id: UUID,
    job_id: UUID,
    conversation_id: UUID,
    reason: str,
) -> None:
    transition_conversation(
        session, clinic_id, conversation_id, None, HandoffAction.REQUEST, reason
    )
    session.begin()
    set_clinic(session, clinic_id)
    completed = session.get(Outbox, (clinic_id, job_id))
    if completed:
        completed.state = "done"


def _run_audio_transcription(
    session: Session, clinic_id: UUID, job: Outbox, conversation: Conversation, inbox_id: UUID
) -> None:
    job_id, conversation_id = job.id, conversation.id
    settings = get_settings()
    if not settings.ai_live_enabled:
        job.state, job.error_code = "failed", "ai_disabled"
        return
    if conversation.state != ConversationState.BOT_ACTIVE.value:
        job.state, job.error_code = "failed", "conversation_not_bot_active"
        return
    inbound = session.get(Inbox, (clinic_id, inbox_id))
    if inbound is None or inbound.payload.get("kind") != "messages":
        _handoff_audio_failure(session, clinic_id, job_id, conversation_id, "invalid_audio_event")
        return
    item = inbound.payload.get("data")
    media = item.get("audio") if isinstance(item, dict) else None
    media_id = media.get("id") if isinstance(media, dict) else None
    if not isinstance(media_id, str):
        _handoff_audio_failure(session, clinic_id, job_id, conversation_id, "invalid_media_id")
        return
    session.commit()
    try:
        audio: AudioFile = download_audio(
            media_id,
            settings.whatsapp_access_token.get_secret_value(),
            settings.whatsapp_api_version,
        )
        transcript = transcribe_audio(audio, settings.openai_api_key)
    except AudioMediaError as exc:
        _handoff_audio_failure(session, clinic_id, job_id, conversation_id, exc.code)
        return
    except Exception:
        _handoff_audio_failure(session, clinic_id, job_id, conversation_id, "transcription_failed")
        return
    if not transcript.confident:
        _handoff_audio_failure(
            session,
            clinic_id,
            job_id,
            conversation_id,
            "low_confidence_transcription",
            reason="unclear_request",
            failed=False,
        )
        return
    session.begin()
    set_clinic(session, clinic_id)
    latest = session.get(Conversation, (clinic_id, conversation_id))
    active_job = session.get(Outbox, (clinic_id, job_id))
    if not active_job:
        return
    if not latest or latest.state != ConversationState.BOT_ACTIVE.value:
        active_job.state, active_job.error_code = "failed", "handoff_interrupted"
        return
    session.add(
        ConversationMessage(
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            source_inbox_id=inbox_id,
            role="user",
            content=transcript.text,
        )
    )
    session.add(
        Outbox(
            clinic_id=clinic_id,
            kind="agent_turn",
            key=f"agent:{inbox_id}",
            origin="agent",
            payload={"conversation_id": str(conversation_id), "inbox_id": str(inbox_id)},
        )
    )
    active_job.state = "done"


def _handoff_audio_failure(
    session: Session,
    clinic_id: UUID,
    job_id: UUID,
    conversation_id: UUID,
    error_code: str,
    reason: str = "other",
    failed: bool = True,
) -> None:
    transition_conversation(
        session,
        clinic_id,
        conversation_id,
        None,
        HandoffAction.REQUEST,
        reason,
    )
    session.begin()
    set_clinic(session, clinic_id)
    job = session.get(Outbox, (clinic_id, job_id))
    if job:
        job.state = "failed" if failed else "done"
        job.error_code = error_code

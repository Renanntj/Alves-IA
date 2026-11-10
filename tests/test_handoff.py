import pytest

from alves_ia.whatsapp.handoff import ConversationState, HandoffAction, next_state
from alves_ia.whatsapp.urgency import is_urgent


def test_customer_handoff_stops_bot_before_human_claims_conversation() -> None:
    assert next_state(ConversationState.BOT_ACTIVE, HandoffAction.REQUEST) == (
        ConversationState.HUMAN_PENDING
    )


def test_pending_handoff_cannot_be_claimed_by_bot() -> None:
    with pytest.raises(ValueError, match="transition"):
        next_state(ConversationState.HUMAN_PENDING, HandoffAction.BOT_REPLY)


def test_human_claim_suspends_bot_until_explicit_resume() -> None:
    assert next_state(ConversationState.HUMAN_PENDING, HandoffAction.CLAIM) == (
        ConversationState.HUMAN_ACTIVE
    )
    with pytest.raises(ValueError, match="transition"):
        next_state(ConversationState.HUMAN_ACTIVE, HandoffAction.BOT_REPLY)
    assert next_state(ConversationState.HUMAN_ACTIVE, HandoffAction.RESUME_BOT) == (
        ConversationState.BOT_ACTIVE
    )


def test_closed_conversation_cannot_resume_or_reply() -> None:
    assert next_state(ConversationState.BOT_ACTIVE, HandoffAction.CLOSE) == (
        ConversationState.CLOSED
    )
    with pytest.raises(ValueError, match="transition"):
        next_state(ConversationState.CLOSED, HandoffAction.RESUME_BOT)


@pytest.mark.parametrize(
    "text",
    [
        "Estou com dor forte no peito",
        "Estou com falta de ar",
        "A pessoa desmaiou",
        "risco de suicídio",
    ],
)
def test_high_risk_text_is_held_for_human(text: str) -> None:
    assert is_urgent(text)


def test_regular_requests_are_not_classified_as_urgent() -> None:
    assert not is_urgent("Quero marcar uma consulta para semana que vem")

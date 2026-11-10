from enum import StrEnum


class ConversationState(StrEnum):
    BOT_ACTIVE = "BOT_ACTIVE"
    HUMAN_PENDING = "HUMAN_PENDING"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"
    CLOSED = "CLOSED"


class HandoffAction(StrEnum):
    REQUEST = "request"
    CLAIM = "claim"
    RESUME_BOT = "resume_bot"
    CLOSE = "close"
    BOT_REPLY = "bot_reply"
    HUMAN_REPLY = "human_reply"


_TRANSITIONS = {
    (ConversationState.BOT_ACTIVE, HandoffAction.REQUEST): ConversationState.HUMAN_PENDING,
    (ConversationState.HUMAN_PENDING, HandoffAction.REQUEST): ConversationState.HUMAN_PENDING,
    (ConversationState.HUMAN_PENDING, HandoffAction.CLAIM): ConversationState.HUMAN_ACTIVE,
    (ConversationState.HUMAN_ACTIVE, HandoffAction.RESUME_BOT): ConversationState.BOT_ACTIVE,
    (ConversationState.BOT_ACTIVE, HandoffAction.CLOSE): ConversationState.CLOSED,
    (ConversationState.HUMAN_PENDING, HandoffAction.CLOSE): ConversationState.CLOSED,
    (ConversationState.HUMAN_ACTIVE, HandoffAction.CLOSE): ConversationState.CLOSED,
    (ConversationState.BOT_ACTIVE, HandoffAction.BOT_REPLY): ConversationState.BOT_ACTIVE,
    (ConversationState.HUMAN_ACTIVE, HandoffAction.HUMAN_REPLY): ConversationState.HUMAN_ACTIVE,
}


def next_state(state: ConversationState, action: HandoffAction) -> ConversationState:
    try:
        return _TRANSITIONS[(state, action)]
    except KeyError as exc:
        raise ValueError(f"Invalid conversation transition: {state} -> {action}") from exc

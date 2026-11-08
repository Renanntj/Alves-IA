from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from alves_ia.models import Base


def now() -> datetime:
    return datetime.now(UTC)


class ChannelRoute(Base):
    """Global routing index only; no messages, tokens or contact data."""

    __tablename__ = "channel_routes"
    phone_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    waba_id: Mapped[str] = mapped_column(String(64))
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), index=True)
    active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (UniqueConstraint("clinic_id", "phone_id"),)


class Inbox(Base):
    __tablename__ = "whatsapp_inbox"
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_key: Mapped[str] = mapped_column(String(64))
    phone_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    processed: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("clinic_id", "event_key"),
        ForeignKeyConstraint(
            ["clinic_id", "phone_id"], ["channel_routes.clinic_id", "channel_routes.phone_id"]
        ),
    )


class Conversation(Base):
    __tablename__ = "whatsapp_conversations"
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    phone_id: Mapped[str] = mapped_column(String(64))
    contact: Mapped[str] = mapped_column(String(32))
    last_inbound: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("clinic_id", "phone_id", "contact"),
        ForeignKeyConstraint(
            ["clinic_id", "phone_id"], ["channel_routes.clinic_id", "channel_routes.phone_id"]
        ),
    )


class Outbox(Base):
    __tablename__ = "whatsapp_outbox"
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    provider_id: Mapped[str | None] = mapped_column(String(256))
    delivery_status: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("clinic_id", "key"),)

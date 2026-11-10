from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from alves_ia.models import Base


def now() -> datetime:
    return datetime.now(UTC)


class GoogleSheetsConnection(Base):
    __tablename__ = "google_sheets_connections"
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), primary_key=True)
    spreadsheet_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    range_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    allowed_columns: Mapped[list[str]] = mapped_column(JSONB, default=list)
    refresh_token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GoogleSheetsOAuthState(Base):
    __tablename__ = "google_sheets_oauth_states"
    clinic_id: Mapped[UUID] = mapped_column(primary_key=True)
    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    code_verifier_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "user_id"], ["memberships.clinic_id", "memberships.user_id"]
        ),
    )

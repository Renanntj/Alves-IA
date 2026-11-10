"""Queue bounded WhatsApp audio transcription jobs."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE whatsapp_outbox DROP CONSTRAINT ck_outbox_kind")
    op.execute(
        "ALTER TABLE whatsapp_outbox ADD CONSTRAINT ck_outbox_kind "
        "CHECK (kind IN ('ingest', 'send', 'agent_turn', 'transcribe_audio'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE whatsapp_outbox DROP CONSTRAINT ck_outbox_kind")
    op.execute(
        "ALTER TABLE whatsapp_outbox ADD CONSTRAINT ck_outbox_kind "
        "CHECK (kind IN ('ingest', 'send', 'agent_turn'))"
    )

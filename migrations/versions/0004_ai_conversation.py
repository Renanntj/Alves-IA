"""Persist tenant-scoped conversation history and queue opt-in AI turns."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE whatsapp_outbox DROP CONSTRAINT whatsapp_outbox_kind_check")
    op.execute(
        "ALTER TABLE whatsapp_outbox ADD CONSTRAINT ck_outbox_kind "
        "CHECK (kind IN ('ingest', 'send', 'agent_turn'))"
    )
    op.execute("ALTER TABLE whatsapp_conversation_events ALTER COLUMN actor_user_id DROP NOT NULL")
    op.execute("ALTER TABLE whatsapp_conversation_events ADD COLUMN reason varchar(32)")
    op.execute(
        "CREATE TABLE whatsapp_conversation_messages ("
        "clinic_id uuid NOT NULL, id uuid NOT NULL, conversation_id uuid NOT NULL, "
        "source_inbox_id uuid, role varchar(16) NOT NULL, content text NOT NULL, "
        "created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(clinic_id, id), "
        "FOREIGN KEY(clinic_id, conversation_id) "
        "REFERENCES whatsapp_conversations(clinic_id, id), "
        "FOREIGN KEY(clinic_id, source_inbox_id) REFERENCES whatsapp_inbox(clinic_id, id), "
        "UNIQUE(clinic_id, source_inbox_id), "
        "CHECK(role IN ('user', 'assistant')), CHECK(length(content) <= 4000))"
    )
    op.execute(
        "CREATE INDEX ix_conversation_messages_history ON "
        "whatsapp_conversation_messages(clinic_id, conversation_id, created_at, id)"
    )
    op.execute("ALTER TABLE whatsapp_conversation_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE whatsapp_conversation_messages FORCE ROW LEVEL SECURITY")
    predicate = "clinic_id = nullif(current_setting('app.clinic_id', true), '')::uuid"
    op.execute(
        "CREATE POLICY tenant_isolation ON whatsapp_conversation_messages "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    op.execute("GRANT SELECT, INSERT ON whatsapp_conversation_messages TO alves_app")


def downgrade() -> None:
    op.drop_table("whatsapp_conversation_messages")
    op.execute("ALTER TABLE whatsapp_conversation_events DROP COLUMN reason")
    op.execute("ALTER TABLE whatsapp_conversation_events ALTER COLUMN actor_user_id SET NOT NULL")
    op.execute("ALTER TABLE whatsapp_outbox DROP CONSTRAINT ck_outbox_kind")
    op.execute(
        "ALTER TABLE whatsapp_outbox ADD CONSTRAINT whatsapp_outbox_kind_check "
        "CHECK (kind IN ('ingest', 'send'))"
    )

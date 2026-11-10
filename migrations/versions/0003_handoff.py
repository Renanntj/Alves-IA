"""Conversation ownership, state transitions and audit events."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE whatsapp_conversations
        ADD COLUMN state varchar(20) NOT NULL DEFAULT 'BOT_ACTIVE',
        ADD COLUMN assigned_user_id uuid REFERENCES users(id),
        ADD CONSTRAINT ck_conversation_state CHECK
          (state IN ('BOT_ACTIVE', 'HUMAN_PENDING', 'HUMAN_ACTIVE', 'CLOSED'))""")
    op.execute("ALTER TABLE whatsapp_outbox ADD COLUMN origin varchar(16) NOT NULL DEFAULT 'human'")
    op.execute("""ALTER TABLE whatsapp_outbox ADD CONSTRAINT ck_outbox_origin
        CHECK (origin IN ('human', 'agent'))""")
    op.execute("""CREATE TABLE whatsapp_conversation_events (
        clinic_id uuid NOT NULL REFERENCES clinics(id), id uuid NOT NULL,
        conversation_id uuid NOT NULL, actor_user_id uuid NOT NULL REFERENCES users(id),
        action varchar(24) NOT NULL, from_state varchar(20) NOT NULL,
        to_state varchar(20) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(clinic_id, id),
        FOREIGN KEY(clinic_id, conversation_id)
          REFERENCES whatsapp_conversations(clinic_id, id))""")
    op.execute("""CREATE INDEX ix_conversation_events_history
        ON whatsapp_conversation_events(clinic_id, conversation_id, created_at)""")
    op.execute("ALTER TABLE whatsapp_conversation_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE whatsapp_conversation_events FORCE ROW LEVEL SECURITY")
    predicate = "clinic_id = nullif(current_setting('app.clinic_id', true), '')::uuid"
    op.execute(
        "CREATE POLICY tenant_isolation ON whatsapp_conversation_events "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    op.execute("GRANT SELECT, INSERT ON whatsapp_conversation_events TO alves_app")


def downgrade() -> None:
    op.drop_table("whatsapp_conversation_events")
    op.execute("ALTER TABLE whatsapp_outbox DROP CONSTRAINT ck_outbox_origin")
    op.execute("ALTER TABLE whatsapp_outbox DROP COLUMN origin")
    op.execute("ALTER TABLE whatsapp_conversations DROP CONSTRAINT ck_conversation_state")
    op.execute("ALTER TABLE whatsapp_conversations DROP COLUMN assigned_user_id")
    op.execute("ALTER TABLE whatsapp_conversations DROP COLUMN state")

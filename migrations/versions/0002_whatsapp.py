"""WhatsApp routing, Inbox and Outbox with tenant isolation."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE channel_routes (
        phone_id varchar(64) PRIMARY KEY, waba_id varchar(64) NOT NULL,
        clinic_id uuid NOT NULL REFERENCES clinics(id), active boolean NOT NULL,
        UNIQUE(clinic_id, phone_id))""")
    op.execute("CREATE INDEX ix_channel_routes_clinic_id ON channel_routes(clinic_id)")
    op.execute("""CREATE TABLE whatsapp_inbox (
        clinic_id uuid NOT NULL REFERENCES clinics(id), id uuid NOT NULL,
        event_key varchar(64) NOT NULL, phone_id varchar(64) NOT NULL,
        payload jsonb NOT NULL, processed boolean NOT NULL,
        PRIMARY KEY(clinic_id, id), UNIQUE(clinic_id, event_key),
        FOREIGN KEY(clinic_id, phone_id) REFERENCES channel_routes(clinic_id, phone_id))""")
    op.execute("""CREATE TABLE whatsapp_conversations (
        clinic_id uuid NOT NULL REFERENCES clinics(id), id uuid NOT NULL,
        phone_id varchar(64) NOT NULL, contact varchar(32) NOT NULL,
        last_inbound timestamptz NOT NULL,
        PRIMARY KEY(clinic_id, id), UNIQUE(clinic_id, phone_id, contact),
        FOREIGN KEY(clinic_id, phone_id) REFERENCES channel_routes(clinic_id, phone_id))""")
    op.execute("""CREATE TABLE whatsapp_outbox (
        clinic_id uuid NOT NULL REFERENCES clinics(id), id uuid NOT NULL,
        key varchar(160) NOT NULL, kind varchar(16) NOT NULL,
        payload jsonb NOT NULL, state varchar(16) NOT NULL,
        attempts integer NOT NULL, available_at timestamptz NOT NULL,
        provider_id varchar(256), delivery_status varchar(32), error_code varchar(64),
        PRIMARY KEY(clinic_id, id), UNIQUE(clinic_id, key),
        CHECK(kind IN ('ingest', 'send')),
        CHECK(state IN ('pending', 'processing', 'done', 'failed', 'unknown')),
        CHECK(attempts >= 0))""")
    op.execute("CREATE INDEX ix_outbox_pending ON whatsapp_outbox(clinic_id, state, available_at)")
    op.execute("CREATE INDEX ix_outbox_provider ON whatsapp_outbox(clinic_id, provider_id)")
    op.execute("GRANT SELECT ON channel_routes TO alves_app")
    for table in ("whatsapp_inbox", "whatsapp_conversations", "whatsapp_outbox"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        predicate = "clinic_id = nullif(current_setting('app.clinic_id', true), '')::uuid"
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO alves_app")


def downgrade() -> None:
    for table in ("whatsapp_outbox", "whatsapp_conversations", "whatsapp_inbox", "channel_routes"):
        op.drop_table(table)

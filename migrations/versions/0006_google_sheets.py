"""Tenant-isolated Google Sheets connections and single-use OAuth states."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE google_sheets_connections ("
        "clinic_id uuid PRIMARY KEY REFERENCES clinics(id), "
        "spreadsheet_id varchar(256), range_name varchar(256), "
        "allowed_columns jsonb NOT NULL DEFAULT '[]'::jsonb, "
        "refresh_token_ciphertext bytea NOT NULL, "
        "connected_at timestamptz NOT NULL DEFAULT now(), "
        "CHECK ((spreadsheet_id IS NULL AND range_name IS NULL) OR "
        "(spreadsheet_id IS NOT NULL AND range_name IS NOT NULL)), "
        "CHECK (jsonb_typeof(allowed_columns) = 'array'))"
    )
    op.execute(
        "CREATE TABLE google_sheets_oauth_states ("
        "clinic_id uuid NOT NULL, nonce_hash varchar(64) NOT NULL, user_id uuid NOT NULL, "
        "code_verifier_ciphertext bytea NOT NULL, "
        "expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), "
        "PRIMARY KEY(clinic_id, nonce_hash), "
        "FOREIGN KEY(clinic_id, user_id) REFERENCES memberships(clinic_id, user_id))"
    )
    predicate = "clinic_id = nullif(current_setting('app.clinic_id', true), '')::uuid"
    for table in ("google_sheets_connections", "google_sheets_oauth_states"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON google_sheets_connections TO alves_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON google_sheets_oauth_states TO alves_app")


def downgrade() -> None:
    op.drop_table("google_sheets_oauth_states")
    op.drop_table("google_sheets_connections")
    op.drop_table("google_sheets_connections")

"""Identity tables and tenant row security."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clinics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "memberships",
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'manager', 'attendant')"),
    )
    for table, column in (("clinics", "id"), ("memberships", "clinic_id")):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        predicate = f"{column} = nullif(current_setting('app.clinic_id', true), '')::uuid"
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    op.execute("GRANT USAGE ON SCHEMA public TO alves_app")
    op.execute("GRANT SELECT ON users, clinics, memberships TO alves_app")


def downgrade() -> None:
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("clinics")

"""Foundation: account, session, project, attestation, scope_entry, audit_event.

The application role must exist before this migration runs. It is created
outside the migrations because its password is a secret.

Revision ID: 0001
Revises:
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ndm.core.settings import Settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tables the application role reads and writes in full.
APP_TABLES = ["account", "session", "project", "attestation", "scope_entry"]


def _timestamp(name: str, *, nullable: bool = False, default_now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True)


def upgrade() -> None:
    # The name is checked by the settings (letters, digits, underscore).
    app_role = f'"{Settings().app_db_role}"'

    project_state = postgresql.ENUM("active", "archived", name="project_state", create_type=False)
    scope_entry_kind = postgresql.ENUM(
        "address", "cidr", "range", name="scope_entry_kind", create_type=False
    )
    project_state.create(op.get_bind())
    scope_entry_kind.create(op.get_bind())

    op.create_table(
        "account",
        _id(),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        _timestamp("created_at", default_now=True),
        sa.UniqueConstraint("username", name="uq_account_username"),
    )

    op.create_table(
        "session",
        sa.Column("token_hash", sa.LargeBinary, primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger,
            sa.ForeignKey("account.id", name="fk_session_account_id_account", ondelete="CASCADE"),
            nullable=False,
        ),
        _timestamp("created_at", default_now=True),
        _timestamp("last_seen_at"),
        _timestamp("expires_at"),
    )
    op.create_index("ix_session_account_id", "session", ["account_id"])

    op.create_table(
        "project",
        _id(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("state", project_state, nullable=False, server_default="active"),
        sa.Column("current_attestation_id", sa.BigInteger, nullable=True),
        _timestamp("created_at", default_now=True),
        _timestamp("deleted_at", nullable=True),
    )

    op.create_table(
        "attestation",
        _id(),
        sa.Column(
            "project_id",
            sa.BigInteger,
            sa.ForeignKey(
                "project.id", name="fk_attestation_project_id_project", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.BigInteger,
            sa.ForeignKey("account.id", name="fk_attestation_account_id_account"),
            nullable=False,
        ),
        _timestamp("created_at", default_now=True),
    )
    op.create_index("ix_attestation_project_id", "attestation", ["project_id"])

    # project and attestation refer to each other, so this key is added last.
    op.create_foreign_key(
        "fk_project_current_attestation_id_attestation",
        "project",
        "attestation",
        ["current_attestation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "scope_entry",
        _id(),
        sa.Column(
            "attestation_id",
            sa.BigInteger,
            sa.ForeignKey(
                "attestation.id",
                name="fk_scope_entry_attestation_id_attestation",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("kind", scope_entry_kind, nullable=False),
        sa.Column("original_text", sa.Text, nullable=False),
        sa.Column("start_address", postgresql.INET, nullable=False),
        sa.Column("end_address", postgresql.INET, nullable=False),
    )
    op.create_index("ix_scope_entry_attestation_id", "scope_entry", ["attestation_id"])

    # No foreign keys: audit entries survive the deletion of what they describe.
    op.create_table(
        "audit_event",
        _id(),
        _timestamp("occurred_at", default_now=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("object_type", sa.Text, nullable=False),
        sa.Column("object_id", sa.Text, nullable=True),
        sa.Column(
            "details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"])

    # --- Privileges of the application role ---
    for table in APP_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")

    # The audit log is append-only (FR-AUD-3). First barrier: the application
    # role may read and add entries and nothing else.
    op.execute(f"GRANT SELECT, INSERT ON audit_event TO {app_role}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM {app_role}")

    # Second barrier: triggers that reject the same for every role, the owner included.
    op.execute(
        """
        CREATE FUNCTION audit_event_reject_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION audit_event_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_truncate
        BEFORE TRUNCATE ON audit_event
        FOR EACH STATEMENT EXECUTE FUNCTION audit_event_reject_change()
        """
    )


def downgrade() -> None:
    op.drop_table("audit_event")
    op.execute("DROP FUNCTION audit_event_reject_change()")
    op.drop_table("scope_entry")
    op.drop_constraint("fk_project_current_attestation_id_attestation", "project")
    op.drop_table("attestation")
    op.drop_table("project")
    op.drop_table("session")
    op.drop_table("account")
    op.execute("DROP TYPE scope_entry_kind")
    op.execute("DROP TYPE project_state")

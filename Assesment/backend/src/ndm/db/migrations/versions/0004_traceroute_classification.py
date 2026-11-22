"""Traceroute and classification: traceroute_run, traceroute_probe,
traceroute_responder, classification_result; the analyst's override, tags and
notes on project_host; traceroute runs as owners of enrichment snapshots.

Revision ID: 0004
Revises: 0003
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ndm.core.settings import Settings

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_TABLES = ["traceroute_run", "traceroute_probe", "traceroute_responder", "classification_result"]


def _timestamp(name: str, *, default_now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=not default_now,
        server_default=sa.func.now() if default_now else None,
    )


def _reference(name: str, target: str, table: str, **options: Any) -> sa.Column[Any]:
    referred = target.split(".")[0]
    nullable = options.pop("nullable", False)
    primary_key = options.pop("primary_key", False)
    return sa.Column(
        name,
        sa.BigInteger,
        sa.ForeignKey(target, name=f"fk_{table}_{name}_{referred}"),
        nullable=nullable,
        primary_key=primary_key,
    )


def _text_array(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
    )


def upgrade() -> None:
    app_role = f'"{Settings().app_db_role}"'
    state = postgresql.ENUM(
        "queued", "running", "completed", "failed", name="traceroute_state", create_type=False
    )
    state.create(op.get_bind())

    op.create_table(
        "traceroute_run",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        _reference("project_id", "project.id", "traceroute_run"),
        _reference("attestation_id", "attestation.id", "traceroute_run"),
        _reference("vantage_point_id", "vantage_point.id", "traceroute_run", nullable=True),
        sa.Column("target", postgresql.INET, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("parameters", postgresql.JSONB, nullable=False),
        sa.Column("state", state, nullable=False, server_default="queued"),
        sa.Column("reached", sa.Boolean, nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        _timestamp("created_at", default_now=True),
        _timestamp("dispatched_at"),
        _timestamp("finished_at"),
    )
    op.create_index(
        "ix_traceroute_run_project_id_target", "traceroute_run", ["project_id", "target"]
    )
    op.create_index("ix_traceroute_run_state", "traceroute_run", ["state"])

    op.create_table(
        "traceroute_probe",
        _reference("run_id", "traceroute_run.id", "traceroute_probe", primary_key=True),
        sa.Column("ttl", sa.Integer, primary_key=True),
        sa.Column("probe", sa.Integer, primary_key=True),
        sa.Column("responder", postgresql.INET, nullable=True),
        sa.Column("rtt_ms", sa.Float, nullable=True),
        sa.Column("reply_kind", sa.Text, nullable=True),
    )
    op.create_index("ix_traceroute_probe_responder", "traceroute_probe", ["responder"])

    op.create_table(
        "traceroute_responder",
        _reference("run_id", "traceroute_run.id", "traceroute_responder", primary_key=True),
        sa.Column("address", postgresql.INET, primary_key=True),
        sa.Column("unlocatable", sa.Boolean, nullable=False),
        _text_array("ptr_names"),
        sa.Column("ptr_status", sa.Text, nullable=True),
    )

    op.create_table(
        "classification_result",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        _reference("scan_id", "scan.id", "classification_result"),
        sa.Column("address", postgresql.INET, nullable=False),
        sa.Column("run", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("latest", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("device_type", sa.Text, nullable=False),
        sa.Column("sub_role", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("rules", postgresql.JSONB, nullable=False),
        sa.Column("ruleset_version", sa.Text, nullable=False),
        _timestamp("created_at", default_now=True),
        sa.UniqueConstraint("scan_id", "address", "run", name="uq_classification_result_run"),
    )
    op.create_index(
        "uq_classification_result_latest",
        "classification_result",
        ["scan_id", "address"],
        unique=True,
        postgresql_where=sa.text("latest"),
    )

    # A snapshot belongs to a scan or to a traceroute run.
    op.alter_column("enrichment_snapshot", "scan_id", nullable=True)
    op.add_column(
        "enrichment_snapshot",
        _reference("traceroute_run_id", "traceroute_run.id", "enrichment_snapshot", nullable=True),
    )
    op.create_check_constraint(
        "ck_enrichment_snapshot_one_owner",
        "enrichment_snapshot",
        "(scan_id IS NULL) <> (traceroute_run_id IS NULL)",
    )
    op.create_unique_constraint(
        "uq_enrichment_snapshot_traceroute_run_id",
        "enrichment_snapshot",
        ["traceroute_run_id", "address"],
    )

    op.add_column("scan", _timestamp("classification_requested_at"))

    op.add_column("project_host", sa.Column("type_override", sa.Text, nullable=True))
    op.add_column("project_host", _timestamp("override_at"))
    op.add_column("project_host", _text_array("tags"))
    op.add_column("project_host", sa.Column("notes", sa.Text, nullable=True))

    for table in APP_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")


def downgrade() -> None:
    for column in ("notes", "tags", "override_at", "type_override"):
        op.drop_column("project_host", column)
    op.drop_column("scan", "classification_requested_at")
    op.drop_constraint(
        "uq_enrichment_snapshot_traceroute_run_id", "enrichment_snapshot", type_="unique"
    )
    op.drop_constraint("ck_enrichment_snapshot_one_owner", "enrichment_snapshot", type_="check")
    # Snapshots of traceroute runs cannot exist without the runs.
    op.execute("DELETE FROM enrichment_snapshot WHERE traceroute_run_id IS NOT NULL")
    op.drop_column("enrichment_snapshot", "traceroute_run_id")
    op.alter_column("enrichment_snapshot", "scan_id", nullable=False)
    op.drop_table("classification_result")
    op.drop_table("traceroute_responder")
    op.drop_table("traceroute_probe")
    op.drop_table("traceroute_run")
    op.execute("DROP TYPE traceroute_state")

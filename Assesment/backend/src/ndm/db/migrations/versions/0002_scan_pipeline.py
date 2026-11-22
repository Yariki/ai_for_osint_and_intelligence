"""Scan pipeline: vantage_point, scan, scan_chunk, host, project_host,
host_observation, port_observation.

The two observation tables are hash-partitioned by scan into 16 partitions
(AD8). Hash partitions need no maintenance job, and every hot query selects by
scan and touches one partition.

Revision ID: 0002
Revises: 0001
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ndm.core.settings import Settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PARTITIONS = 16
PARTITIONED = ["host_observation", "port_observation"]
# Tables the application role reads and writes. Partitions take the privileges
# of their parent when they are reached through it.
APP_TABLES = [
    "vantage_point",
    "scan",
    "scan_chunk",
    "host",
    "project_host",
    "host_observation",
    "port_observation",
]


def _timestamp(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True)


def _jsonb(name: str) -> sa.Column[Any]:
    return sa.Column(name, postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def _reference(name: str, target: str, table: str, **options: Any) -> sa.Column[Any]:
    """A bigint foreign-key column with the name the models' naming convention gives it."""
    referred = target.split(".")[0]
    nullable = options.pop("nullable", False)
    primary_key = options.pop("primary_key", False)
    return sa.Column(
        name,
        sa.BigInteger,
        sa.ForeignKey(target, name=f"fk_{table}_{name}_{referred}", **options),
        nullable=nullable,
        primary_key=primary_key,
    )


def upgrade() -> None:
    app_role = f'"{Settings().app_db_role}"'

    scan_state = postgresql.ENUM(
        "queued", "running", "completed", "cancelled", "failed", "interrupted",
        name="scan_state", create_type=False,
    )  # fmt: skip
    scan_phase = postgresql.ENUM(
        "discovery", "port_scan", "enrichment", "classification", "diff",
        name="scan_phase", create_type=False,
    )  # fmt: skip
    chunk_state = postgresql.ENUM(
        "pending", "dispatched", "done", name="chunk_state", create_type=False
    )
    for enum in (scan_state, scan_phase, chunk_state):
        enum.create(op.get_bind())

    op.create_table(
        "vantage_point",
        _id(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("egress_address", postgresql.INET, nullable=True),
        _jsonb("capabilities"),
        _jsonb("counters"),
        _timestamp("last_heartbeat_at"),
        sa.UniqueConstraint("name", name="uq_vantage_point_name"),
    )

    op.create_table(
        "scan",
        _id(),
        _reference("project_id", "project.id", "scan"),
        _reference("attestation_id", "attestation.id", "scan"),
        _reference("vantage_point_id", "vantage_point.id", "scan", nullable=True),
        sa.Column("state", scan_state, nullable=False, server_default="queued"),
        sa.Column("phase", scan_phase, nullable=True),
        sa.Column("targets", postgresql.ARRAY(postgresql.INET), nullable=False),
        sa.Column("probed_ports", postgresql.INT4MULTIRANGE, nullable=False),
        sa.Column("parameters", postgresql.JSONB, nullable=False),
        sa.Column("technique_requested", sa.Text, nullable=False),
        sa.Column("technique_used", sa.Text, nullable=True),
        sa.Column("seed", sa.BigInteger, nullable=False),
        _jsonb("counters"),
        sa.Column("partial", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("failure_reason", sa.Text, nullable=True),
        _timestamp("created_at", nullable=False, default_now=True),
        _timestamp("started_at"),
        _timestamp("finished_at"),
        _timestamp("cancel_requested_at"),
        _timestamp("deleted_at"),
    )
    op.create_index("ix_scan_project_id", "scan", ["project_id"])
    op.create_index("ix_scan_state", "scan", ["state"])

    op.create_table(
        "scan_chunk",
        _reference("scan_id", "scan.id", "scan_chunk", ondelete="CASCADE", primary_key=True),
        sa.Column("chunk_seq", sa.Integer, primary_key=True),
        sa.Column("phase", scan_phase, nullable=False),
        sa.Column("spec", postgresql.JSONB, nullable=False),
        sa.Column("state", chunk_state, nullable=False, server_default="pending"),
        _timestamp("dispatched_at"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("result", postgresql.JSONB, nullable=True),
    )

    op.create_table(
        "host",
        sa.Column("address", postgresql.INET, primary_key=True),
        _timestamp("first_seen_at", nullable=False),
        _timestamp("last_seen_at", nullable=False),
        sa.Column("is_public", sa.Boolean, nullable=False),
    )

    op.create_table(
        "project_host",
        _reference(
            "project_id", "project.id", "project_host", ondelete="CASCADE", primary_key=True
        ),
        sa.Column("address", postgresql.INET, primary_key=True),
        _timestamp("first_seen_at"),
        _timestamp("last_seen_at"),
        _reference("latest_scan_id", "scan.id", "project_host", ondelete="SET NULL", nullable=True),
    )

    op.create_table(
        "host_observation",
        _reference("scan_id", "scan.id", "host_observation", primary_key=True),
        sa.Column("address", postgresql.INET, primary_key=True),
        _reference("vantage_point_id", "vantage_point.id", "host_observation", nullable=True),
        sa.Column("reachable", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _jsonb("evidence"),
        sa.Column("closed_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("filtered_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        postgresql_partition_by="HASH (scan_id)",
    )
    # The one secondary index on the large tables: host timeline and global host view.
    op.create_index(
        "ix_host_observation_address_scan_id", "host_observation", ["address", "scan_id"]
    )

    op.create_table(
        "port_observation",
        _reference("scan_id", "scan.id", "port_observation", primary_key=True),
        sa.Column("address", postgresql.INET, primary_key=True),
        sa.Column("port", sa.Integer, primary_key=True),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_port_observation_port_range"),
        postgresql_partition_by="HASH (scan_id)",
    )

    for table in PARTITIONED:
        for remainder in range(PARTITIONS):
            op.execute(
                f"CREATE TABLE {table}_p{remainder:02d} PARTITION OF {table} "
                f"FOR VALUES WITH (MODULUS {PARTITIONS}, REMAINDER {remainder})"
            )

    # The protections on audit_event are not touched.
    for table in APP_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")


def downgrade() -> None:
    # Dropping a partitioned table drops its partitions.
    op.drop_table("port_observation")
    op.drop_table("host_observation")
    op.drop_table("project_host")
    op.drop_table("host")
    op.drop_table("scan_chunk")
    op.drop_table("scan")
    op.drop_table("vantage_point")
    op.execute("DROP TYPE chunk_state")
    op.execute("DROP TYPE scan_phase")
    op.execute("DROP TYPE scan_state")

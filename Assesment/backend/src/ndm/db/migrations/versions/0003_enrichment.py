"""Enrichment and reference datasets: reference_dataset, rir_inetnum,
rir_delegation, enrichment_snapshot; reverse DNS on host_observation; dataset
versions and the analyst's requests on scan.

The location of a snapshot is a PostGIS point. PostGIS is part of the database
image and is normally there already; a database that lacks it gets it here.

Revision ID: 0003
Revises: 0002
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ndm.core.settings import Settings

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_TABLES = ["reference_dataset", "rir_inetnum", "rir_delegation", "enrichment_snapshot"]
# Marks an extension this migration installed, so that reverting removes it
# and leaves alone one that was there before.
INSTALLED_HERE = "installed by ndm migration 0003"


def _timestamp(name: str, *, default_now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=not default_now,
        server_default=sa.func.now() if default_now else None,
    )


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True)


def _jsonb(name: str) -> sa.Column[Any]:
    return sa.Column(name, postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def _text_array(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
    )


def upgrade() -> None:
    app_role = f'"{Settings().app_db_role}"'
    scan_phase = postgresql.ENUM(name="scan_phase", create_type=False)

    present = op.get_bind().scalar(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'"))
    if not present:
        op.execute("CREATE EXTENSION postgis")
        op.execute(f"COMMENT ON EXTENSION postgis IS '{INSTALLED_HERE}'")

    op.create_table(
        "reference_dataset",
        _id(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="missing"),
        sa.Column("detail", sa.Text, nullable=True),
        _timestamp("loaded_at"),
        _timestamp("checked_at"),
        _timestamp("update_requested_at"),
        sa.UniqueConstraint("name", name="uq_reference_dataset_name"),
    )

    op.create_table(
        "rir_inetnum",
        _id(),
        sa.Column("rir", sa.Text, nullable=False),
        sa.Column("ip_range", postgresql.INT8RANGE, nullable=False),
        sa.Column("network_name", sa.Text, nullable=True),
        sa.Column("organization", sa.Text, nullable=True),
        sa.Column("country", sa.Text, nullable=True),
    )
    # "Which ranges cover this address": the lookup of every enriched host.
    op.create_index("ix_rir_inetnum_ip_range", "rir_inetnum", ["ip_range"], postgresql_using="gist")

    op.create_table(
        "rir_delegation",
        _id(),
        sa.Column("rir", sa.Text, nullable=False),
        sa.Column("ip_range", postgresql.INT8RANGE, nullable=False),
        sa.Column("country", sa.Text, nullable=True),
        sa.Column("delegated_on", sa.Date, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_rir_delegation_ip_range", "rir_delegation", ["ip_range"], postgresql_using="gist"
    )

    op.create_table(
        "enrichment_snapshot",
        _id(),
        sa.Column(
            "scan_id",
            sa.BigInteger,
            sa.ForeignKey("scan.id", name="fk_enrichment_snapshot_scan_id_scan"),
            nullable=False,
        ),
        sa.Column("address", postgresql.INET, nullable=False),
        sa.Column("run", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("latest", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("asn", sa.BigInteger, nullable=True),
        sa.Column("as_holder", sa.Text, nullable=True),
        sa.Column("network_name", sa.Text, nullable=True),
        sa.Column("organization", sa.Text, nullable=True),
        sa.Column("organization_source", sa.Text, nullable=True),
        sa.Column("rir", sa.Text, nullable=True),
        sa.Column("registration_country", sa.Text, nullable=True),
        sa.Column("country", sa.Text, nullable=True),
        sa.Column("city", sa.Text, nullable=True),
        sa.Column("accuracy_radius_km", sa.Integer, nullable=True),
        _text_array("unavailable"),
        _jsonb("dataset_versions"),
        _timestamp("created_at", default_now=True),
        sa.UniqueConstraint("scan_id", "address", "run", name="uq_enrichment_snapshot_run"),
    )
    # The point, with no spatial index: the map loads points by scan, not by area.
    op.execute("ALTER TABLE enrichment_snapshot ADD COLUMN location geography(Point,4326)")
    op.create_index(
        "uq_enrichment_snapshot_latest",
        "enrichment_snapshot",
        ["scan_id", "address"],
        unique=True,
        postgresql_where=sa.text("latest"),
    )

    op.add_column("host_observation", _text_array("ptr_names"))
    op.add_column("host_observation", sa.Column("ptr_status", sa.Text, nullable=True))

    op.add_column("scan", _jsonb("dataset_versions"))
    op.add_column("scan", sa.Column("failed_phase", scan_phase, nullable=True))
    op.add_column("scan", _timestamp("retry_requested_at"))
    op.add_column("scan", _timestamp("enrichment_requested_at"))

    for table in APP_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")


def downgrade() -> None:
    for column in ("enrichment_requested_at", "retry_requested_at", "failed_phase"):
        op.drop_column("scan", column)
    op.drop_column("scan", "dataset_versions")
    op.drop_column("host_observation", "ptr_status")
    op.drop_column("host_observation", "ptr_names")
    op.drop_table("enrichment_snapshot")
    op.drop_table("rir_delegation")
    op.drop_table("rir_inetnum")
    op.drop_table("reference_dataset")

    installed_here = op.get_bind().scalar(
        sa.text(
            "SELECT obj_description(oid, 'pg_extension') = :mark "
            "FROM pg_extension WHERE extname = 'postgis'"
        ),
        {"mark": INSTALLED_HERE},
    )
    if installed_here:
        op.execute("DROP EXTENSION postgis")

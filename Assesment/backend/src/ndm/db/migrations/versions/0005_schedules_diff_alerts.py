"""Schedules, comparison and alerts: scan_profile, schedule, schedule_run,
scan_diff, change_event, alert; the schedule of a scan.

Revision ID: 0005
Revises: 0004
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ndm.core.settings import Settings

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_TABLES = ["scan_profile", "schedule", "schedule_run", "scan_diff", "change_event", "alert"]


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True)


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
    return sa.Column(
        name,
        sa.BigInteger,
        sa.ForeignKey(target, name=f"fk_{table}_{name}_{referred}", **options),
        nullable=nullable,
    )


def upgrade() -> None:
    app_role = f'"{Settings().app_db_role}"'

    op.create_table(
        "scan_profile",
        _id(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("parameters", postgresql.JSONB, nullable=False),
        _timestamp("created_at", default_now=True),
        _timestamp("updated_at", default_now=True),
        sa.UniqueConstraint("name", name="uq_scan_profile_name"),
    )

    op.create_table(
        "schedule",
        _id(),
        _reference("project_id", "project.id", "schedule"),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("targets", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("parameters", postgresql.JSONB, nullable=False),
        sa.Column("interval_seconds", sa.Integer, nullable=True),
        sa.Column("cron", sa.Text, nullable=True),
        sa.Column("time_zone", sa.Text, nullable=False, server_default="UTC"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("flag_reason", sa.Text, nullable=True),
        _timestamp("next_run_at"),
        _timestamp("last_run_at"),
        sa.Column("last_outcome", sa.Text, nullable=True),
        _timestamp("created_at", default_now=True),
        sa.CheckConstraint(
            "(interval_seconds IS NULL) <> (cron IS NULL)", name="ck_schedule_interval_or_cron"
        ),
    )
    op.alter_column("schedule", "next_run_at", nullable=False)
    op.create_index("ix_schedule_project_id", "schedule", ["project_id"])
    op.create_index(
        "ix_schedule_next_run_at", "schedule", ["next_run_at"], postgresql_where=sa.text("enabled")
    )

    op.add_column(
        "scan",
        _reference("schedule_id", "schedule.id", "scan", nullable=True, ondelete="SET NULL"),
    )
    op.create_index("ix_scan_schedule_id", "scan", ["schedule_id"])

    op.create_table(
        "schedule_run",
        _id(),
        _reference("schedule_id", "schedule.id", "schedule_run", ondelete="CASCADE"),
        _timestamp("planned_at"),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        _reference("scan_id", "scan.id", "schedule_run", nullable=True, ondelete="SET NULL"),
        _timestamp("created_at", default_now=True),
    )
    op.alter_column("schedule_run", "planned_at", nullable=False)
    op.create_index("ix_schedule_run_schedule_id", "schedule_run", ["schedule_id"])

    op.create_table(
        "scan_diff",
        _id(),
        _reference("base_scan_id", "scan.id", "scan_diff"),
        _reference("target_scan_id", "scan.id", "scan_diff"),
        sa.Column("compared_ports", postgresql.INT4MULTIRANGE, nullable=False),
        sa.Column("not_comparable", postgresql.ARRAY(postgresql.INET), nullable=False),
        sa.Column("summary", postgresql.JSONB, nullable=False),
        _timestamp("created_at", default_now=True),
    )
    op.create_index("ix_scan_diff_base_scan_id", "scan_diff", ["base_scan_id"])
    op.create_index("ix_scan_diff_target_scan_id", "scan_diff", ["target_scan_id"])

    op.create_table(
        "change_event",
        _id(),
        _reference("diff_id", "scan_diff.id", "change_event", ondelete="CASCADE"),
        sa.Column("address", postgresql.INET, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("port", sa.Integer, nullable=True),
        sa.Column("field", sa.Text, nullable=True),
        sa.Column("before", postgresql.JSONB, nullable=True),
        sa.Column("after", postgresql.JSONB, nullable=True),
        sa.Column("dataset_change", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_change_event_diff_id", "change_event", ["diff_id"])

    op.create_table(
        "alert",
        _id(),
        _reference("project_id", "project.id", "alert", nullable=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("link", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _timestamp("created_at", default_now=True),
        _timestamp("read_at"),
    )
    op.create_index("ix_alert_project_id", "alert", ["project_id"])
    op.create_index(
        "ix_alert_unread", "alert", ["created_at"], postgresql_where=sa.text("read_at IS NULL")
    )

    for table in APP_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")


def downgrade() -> None:
    op.drop_table("alert")
    op.drop_table("change_event")
    op.drop_table("scan_diff")
    op.drop_table("schedule_run")
    op.drop_index("ix_scan_schedule_id", "scan")
    op.drop_column("scan", "schedule_id")
    op.drop_table("schedule")
    op.drop_table("scan_profile")

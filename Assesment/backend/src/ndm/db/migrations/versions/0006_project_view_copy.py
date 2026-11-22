"""The project-wide view reads `project_host` alone: a copy of what the view
displays of each address's latest observation (TASK-078, architecture 10.2).

The copy is filled here from the existing observations. From then on every
write that changes it refreshes it (`ndm.db.project_view`). The statement is
repeated here rather than imported, so that this migration stays as it is.

Revision ID: 0006
Revises: 0005
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

COLUMNS: list[sa.Column[Any]] = [
    sa.Column("reachable", sa.Boolean),
    sa.Column("open_ports", postgresql.ARRAY(sa.Integer)),
    sa.Column("closed_count", sa.Integer),
    sa.Column("filtered_count", sa.Integer),
    sa.Column("ptr_names", postgresql.ARRAY(sa.Text)),
    sa.Column("asn", sa.BigInteger),
    sa.Column("as_holder", sa.Text),
    sa.Column("network_name", sa.Text),
    sa.Column("organization", sa.Text),
    sa.Column("rir", sa.Text),
    sa.Column("registration_country", sa.Text),
    sa.Column("country", sa.Text),
    sa.Column("city", sa.Text),
    # location: geography, added below as in 0003.
    sa.Column("accuracy_radius_km", sa.Integer),
    sa.Column("device_type", sa.Text),
    sa.Column("sub_role", sa.Text),
    sa.Column("confidence", sa.Float),
]

FILL = """
UPDATE project_host AS h
SET (reachable, open_ports, closed_count, filtered_count, ptr_names,
     asn, as_holder, network_name, organization, rir, registration_country,
     country, city, location, accuracy_radius_km,
     device_type, sub_role, confidence) = (
    SELECT o.reachable,
           coalesce((SELECT array_agg(p.port ORDER BY p.port) FROM port_observation AS p
                     WHERE p.scan_id = o.scan_id AND p.address = o.address), '{}'),
           o.closed_count, o.filtered_count, o.ptr_names,
           e.asn, e.as_holder, e.network_name, e.organization, e.rir, e.registration_country,
           e.country, e.city, e.location, e.accuracy_radius_km,
           c.device_type, c.sub_role, c.confidence
    FROM host_observation AS o
    LEFT JOIN enrichment_snapshot AS e
        ON e.scan_id = o.scan_id AND e.address = o.address AND e.latest
    LEFT JOIN classification_result AS c
        ON c.scan_id = o.scan_id AND c.address = o.address AND c.latest
    WHERE o.scan_id = h.latest_scan_id AND o.address = h.address
)
WHERE h.latest_scan_id IS NOT NULL
"""


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("project_host", column)
    op.execute("ALTER TABLE project_host ADD COLUMN location geography(Point,4326)")
    op.execute(FILL)


def downgrade() -> None:
    op.drop_column("project_host", "location")
    for column in reversed(COLUMNS):
        op.drop_column("project_host", column.name)

"""A scan records that ICMP discovery was asked for and the scanner could not
send echo requests (TASK-079, the product owner's decision on check 1B.11).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan",
        sa.Column(
            "icmp_discovery_unavailable",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scan", "icmp_discovery_unavailable")

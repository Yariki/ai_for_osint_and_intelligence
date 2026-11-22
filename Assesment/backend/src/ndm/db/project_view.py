"""The copy of each address's latest observation on `project_host` (FR-MAP-5).

The project-wide view shows every address of a project as the latest scan that
targeted it saw it. Joining `project_host` to the observation tables for that
is too slow at the sizing envelope: the planner does not see that each address
has exactly one observation in its latest scan and reads every observation of
the installation (capacity report, check 2.13). So what the view displays is
copied onto `project_host`, and the view reads that table alone (the fallback
of architecture section 10.2).

The copy is a cache, never a source: every write that changes what it holds
calls `refresh` in its own transaction, and `refresh` recomputes the copy from
the observation, its open ports, its latest enrichment snapshot and its latest
classification result. An address whose latest scan is gone has no copy.
"""

from collections.abc import Sequence
from ipaddress import IPv4Address

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# The columns of the copy, in the order of the SELECT below.
COPIED = (
    "reachable",
    "open_ports",
    "closed_count",
    "filtered_count",
    "ptr_names",
    "asn",
    "as_holder",
    "network_name",
    "organization",
    "rir",
    "registration_country",
    "country",
    "city",
    "location",
    "accuracy_radius_km",
    "device_type",
    "sub_role",
    "confidence",
)

# One lookup per row through the primary keys; no row when there is no observation.
LATEST = """
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
"""


async def refresh(
    db: AsyncSession | AsyncConnection,
    *,
    scan_id: int | None = None,
    project_id: int | None = None,
    addresses: Sequence[IPv4Address] | None = None,
) -> None:
    """Recompute the copy of the rows whose latest scan is ``scan_id``, or of
    the rows of ``project_id``; only those of ``addresses`` if given.

    The rows are locked first, in address order, and the copy is computed in a
    second statement: its snapshot then includes every write committed by a
    transaction that held one of the rows. Does not commit.
    """
    if scan_id is None and project_id is None:
        raise ValueError("a scan or a project is needed")
    conditions = []
    params: dict[str, object] = {}
    if scan_id is not None:
        conditions.append("h.latest_scan_id = :scan_id")
        params["scan_id"] = scan_id
    if project_id is not None:
        conditions.append("h.project_id = :project_id")
        params["project_id"] = project_id
    if addresses is not None:
        if not addresses:
            return
        conditions.append("h.address = ANY(CAST(:addresses AS inet[]))")
        params["addresses"] = sorted({str(address) for address in addresses})
    where = " AND ".join(conditions)

    await db.execute(
        text(
            f"SELECT 1 FROM project_host AS h WHERE {where} "
            "ORDER BY h.project_id, h.address FOR UPDATE"
        ),
        params,
    )
    await db.execute(
        text(f"UPDATE project_host AS h SET ({', '.join(COPIED)}) = ({LATEST}) WHERE {where}"),
        params,
    )

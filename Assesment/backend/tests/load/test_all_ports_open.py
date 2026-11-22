"""Check 5.5 (question Q1): a /24 of hosts that answer on every port, scanned
with the top-1000 profile. The write rate and the storage growth are measured
and go to the product owner with Q1.

The lab's allopen target (deploy/lab/allopen) plays 10.77.4.0/24. The scan
stores 256,000 open ports. The growth is measured on the stack's database
with psql in the postgres container, before and after the scan.
"""

# Fixtures imported by name are used as arguments below.
# ruff: noqa: F811

import time

import pytest

from tests.lab.conftest import Lab, compose, lab, manifest  # noqa: F401
from tests.load.conftest import report

NETWORK = "10.77.4.0/24"
PORTS = 1000
A7_PORT_ROWS = 250_000_000
# Both observation tables are partitioned: a parent's own size is 0, so the
# sizes are summed over the partitions.
SIZE_QUERY = (
    "SELECT (SELECT sum(pg_total_relation_size(relid)) "
    "FROM pg_partition_tree('port_observation')), "
    "(SELECT sum(pg_total_relation_size(relid)) "
    "FROM pg_partition_tree('host_observation')), "
    "pg_database_size(current_database()), (SELECT count(*) FROM port_observation)"
)
# What the scan's rows hold, without indexes and without dead row versions.
LIVE_QUERY = (
    "SELECT (SELECT sum(pg_column_size(p.*)) FROM port_observation p WHERE scan_id = {scan}), "
    "(SELECT sum(pg_column_size(h.*)) FROM host_observation h WHERE scan_id = {scan})"
)


def query(sql: str) -> list[int]:
    """One row of numbers from the stack's database, read inside its container."""
    result = compose(
        "exec", "-T", "postgres", "sh", "-c",
        f'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -At -F " " -c "{sql}"',
    )  # fmt: skip
    return [int(value) for value in result.stdout.split()]


def database_sizes() -> tuple[int, int, int, int]:
    """On-disk sizes: port and host observations, the database; and the port rows."""
    ports, hosts, database, rows = query(SIZE_QUERY)
    return ports, hosts, database, rows


@pytest.fixture
def allopen(lab: Lab) -> Lab:
    """A project of its own whose scope is the all-ports-open network."""
    project = lab.client.post("/api/v1/projects", json={"name": f"Q1 {time.time():.0f}"}).json()
    scope = lab.client.put(
        f"/api/v1/projects/{project['id']}/scope",
        json={"entries": [NETWORK], "authorized": True},
    )
    assert scope.status_code == 200, scope.text
    return Lab(lab.client, project["id"])


def test_a_network_open_on_every_port_is_measured(allopen: Lab) -> None:
    ceiling = allopen.client.get("/api/v1/config").json()["limits"]["pps"]["max"]
    before = database_sizes()
    started = time.monotonic()
    scan_id = allopen.launch(targets=[NETWORK], port_profile="top-1000", pps=ceiling)
    scan = allopen.wait(scan_id, timeout=3600)
    seconds = time.monotonic() - started
    after = database_sizes()

    rows = after[3] - before[3]
    growth = after[2] - before[2]
    per_row = (after[0] - before[0]) / rows if rows else 0
    live_ports, live_hosts = query(LIVE_QUERY.format(scan=int(scan_id)))
    hourly_rows_per_day = rows * 24
    values = {
        "scan_id": scan_id,
        "state": scan["state"],
        "pps": ceiling,
        "seconds": round(seconds, 1),
        "open_port_rows": rows,
        "rows_per_second": round(rows / seconds),
        "port_observation_growth_bytes": after[0] - before[0],
        "host_observation_growth_bytes": after[1] - before[1],
        "database_growth_bytes": growth,
        "port_observation_bytes_per_row": round(per_row, 1),
        # The rows that stay: a host's evidence is written again with every
        # findings message, so the on-disk growth right after the scan also
        # holds dead row versions, which vacuum makes reusable.
        "live_port_rows_bytes": live_ports,
        "live_host_rows_bytes": live_hosts,
        "live_bytes_per_day_hourly": (live_ports + live_hosts) * 24,
        # Q1's case: the same /24 scanned every hour.
        "hourly_rows_per_day": hourly_rows_per_day,
        "hourly_bytes_per_day": round(growth * 24),
        "days_until_a7_port_rows": round(A7_PORT_ROWS / hourly_rows_per_day, 1),
    }
    report("all-ports-open", values)

    assert (scan["state"], scan["partial"]) == ("completed", False)
    assert rows == 256 * PORTS
    hosts = allopen.hosts(scan_id)
    assert len(hosts) == 256
    assert all(host["open_port_count"] == PORTS for host in hosts)

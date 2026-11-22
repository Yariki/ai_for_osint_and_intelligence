"""Check 4.2 on stored scans: with different port sets, a port change is
reported only for ports probed in both scans, and the compared ports are
stated; a host not port-scanned in one scan gives a host event and no port
event. Also: the diff is computed from two scan identifiers, and only for
scans of one project."""

from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import Scan, ScanState
from ndm.orchestrator.diff import NotComparable, compute_diff
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import (
    TARGETS,
    Outbox,
    ScanFactory,
    run_to_end,
    scanner_reports,
)

BEFORE = {
    "8.8.8.1": {"ping": True, "open_ports": [22, 443], "closed": "reset"},
    "8.8.8.2": {"ping": False, "open_ports": [443], "closed": "drop"},
    "8.8.8.3": {"ping": True, "open_ports": [8080], "closed": "reset"},
}
AFTER = {
    # 22 closed, 80 and 8080 opened.
    "8.8.8.1": {"ping": True, "open_ports": [80, 443, 8080], "closed": "reset"},
    "8.8.8.2": {"ping": False, "open_ports": [443], "closed": "drop"},
    "8.8.8.3": {"ping": True, "open_ports": [8080], "closed": "reset"},
}


def parameters(discovery_ports: list[int]) -> dict[str, Any]:
    return {
        "discovery": {"icmp_echo": True, "tcp_ports": discovery_ports},
        "scan_all_targets": False,
        "pps": 1000,
        "timeout_ms": 1500,
        "retries": 1,
        "hosts_in_parallel": 64,
    }


async def scan(
    orchestrator: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
    world: dict[str, Any],
    ports: list[int],
    discovery_ports: list[int],
    **values: Any,
) -> Scan:
    outbox.chunks.clear()
    created = await make_scan(
        state=ScanState.QUEUED,
        probed_ports=[Range(port, port + 1) for port in ports],
        parameters=parameters(discovery_ports),
        **values,
    )
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, world)
    return created


@pytest.fixture
async def pair(
    orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
) -> tuple[Scan, Scan]:
    """A small profile, then a larger one, in one project."""
    first = await scan(orchestrator, outbox, make_scan, BEFORE, [22, 80, 443], [80])
    second = await scan(
        orchestrator,
        outbox,
        make_scan,
        AFTER,
        [22, 80, 443, 3389, 8080],
        [80, 443],
        project_id=first.project_id,
        attestation_id=first.attestation_id,
    )
    return first, second


async def test_port_changes_only_for_ports_probed_in_both_scans(
    db_session: AsyncSession, pair: tuple[Scan, Scan]
) -> None:
    first, second = pair

    diff = await compute_diff(db_session, first.id, second.id)

    assert diff.compared_ports == [(22, 22), (80, 80), (443, 443)]
    events = [(str(c.address), c.kind, c.port) for c in diff.changes]
    # 8080 opened on both 8.8.8.1 and 8.8.8.3, but the first scan did not probe it.
    assert ("8.8.8.1", "port_opened", 80) in events
    assert ("8.8.8.1", "port_closed", 22) in events
    assert not [e for e in events if e[2] == 8080]


async def test_host_not_port_scanned_in_one_scan_gives_a_host_event_only(
    db_session: AsyncSession, pair: tuple[Scan, Scan]
) -> None:
    first, second = pair

    diff = await compute_diff(db_session, first.id, second.id)

    # 8.8.8.2 answers neither ping nor port 80: the first scan's discovery missed it,
    # so its ports were not scanned there. The second scan's discovery tried 443.
    of_host = [(c.kind, c.port) for c in diff.changes if c.address == IPv4Address("8.8.8.2")]
    assert of_host == [("host_appeared", None)]
    assert diff.not_comparable == []
    assert diff.summary["compared_addresses"] == len(TARGETS)


async def test_addresses_targeted_by_one_scan_only_are_not_comparable(
    db_session: AsyncSession, orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
) -> None:
    first = await scan(orchestrator, outbox, make_scan, BEFORE, [22, 80, 443], [80, 443])
    second = await scan(
        orchestrator,
        outbox,
        make_scan,
        BEFORE,
        [22, 80, 443],
        [80, 443],
        targets=[IPv4Address("8.8.8.1"), IPv4Address("8.8.8.9")],
        project_id=first.project_id,
        attestation_id=first.attestation_id,
    )

    diff = await compute_diff(db_session, first.id, second.id)

    assert [str(a) for a in diff.not_comparable] == ["8.8.8.2", "8.8.8.3", "8.8.8.4", "8.8.8.9"]
    assert diff.changes == []
    assert diff.summary["compared_addresses"] == 1


async def test_scans_of_different_projects_are_not_compared(
    db_session: AsyncSession, orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
) -> None:
    first = await scan(orchestrator, outbox, make_scan, BEFORE, [22], [80])
    other = await scan(orchestrator, outbox, make_scan, BEFORE, [22], [80])

    with pytest.raises(NotComparable, match="same project"):
        await compute_diff(db_session, first.id, other.id)
    with pytest.raises(NotComparable, match="does not exist"):
        await compute_diff(db_session, first.id, 999999)

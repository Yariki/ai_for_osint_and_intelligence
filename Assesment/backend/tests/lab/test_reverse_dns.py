"""Check 2.7 (FR-ENR-4): PTR names from the lab resolver are stored with the
scan; with the switch off, a capture shows no DNS query.

The lab resolver (deploy/lab/resolver) knows the lab hosts and forwards
nothing. ``lab.env`` points the product at it, on 10.77.0.1:1053.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.lab.conftest import (
    Lab,
    capture,
    compose,
    orchestrator_egress_address,
    packets,
    wait_healthy,
)
from tests.tools.lab_zone import ptr_records

# Queries to the lab resolver, as published on the host and as it sees them.
TO_RESOLVER = "(dst host 10.77.0.1 and dst port 1053) or (dst host 10.77.0.53 and dst port 53)"


def other_dns() -> str:
    """A DNS query from the orchestrator to anything but the lab resolver.

    Only the orchestrator does reverse DNS, and it is the only service with a
    network to the outside. Filtering on its address leaves out what the lab
    host does for itself (its own resolver on 127.0.0.53, desktop programs).
    """
    return f"src host {orchestrator_egress_address()} and dst port 53 and not ({TO_RESOLVER})"


def stored_names(lab: Lab, scan_id: int) -> dict[str, tuple[list[str], str | None]]:
    stored = {}
    for row in lab.hosts(scan_id):
        if row["reachable"]:
            reverse = lab.host(scan_id, row["address"])["reverse_dns"]
            stored[row["address"]] = (reverse["names"], reverse["status"])
    return stored


def test_names_from_the_lab_resolver_are_stored_with_the_scan(
    lab: Lab, manifest: dict[str, Any], tmp_path: Path
) -> None:
    pcap = tmp_path / "dns-on.pcap"
    expected = {
        str(address): [name.removesuffix(".") for name in names]
        for address, names in ptr_records().items()
    }

    with capture(pcap, "port 53 or port 1053"):
        scan_id = lab.launch(targets=[manifest["scanned_network"]], port_profile="top-100")
        scan = lab.wait(scan_id)

    assert scan["state"] == "completed"
    stored = stored_names(lab, scan_id)
    # Every discovered host was looked up. A host of the zone has its names...
    assert set(stored) == {host["address"] for host in manifest["hosts"]}
    for address, names in expected.items():
        assert stored[address] == (names, "ok"), address
    # ...and a host that the zone does not know has none, which is not an error.
    for address in set(stored) - set(expected):
        assert stored[address] == ([], "no_name"), address
    # The queries went to the configured resolver and nowhere else.
    assert len(packets(pcap, TO_RESOLVER)) >= len(stored)
    assert packets(pcap, other_dns()) == []
    # Only discovered hosts were looked up: targets that did not answer are not sent to it.
    assert len(packets(pcap, "dst host 10.77.0.53 and dst port 53")) <= 3 * len(stored)


@pytest.fixture
def reverse_dns_switched_off() -> Iterator[None]:
    """The orchestrator restarted with reverse DNS off; restored afterwards."""
    compose("up", "-d", "orchestrator", settings={"NDM_PTR_ENABLED": "false"})
    wait_healthy()
    try:
        yield
    finally:
        compose("up", "-d", "orchestrator")
        wait_healthy()


def test_with_the_switch_off_a_capture_shows_no_dns_query(
    lab: Lab, manifest: dict[str, Any], tmp_path: Path, reverse_dns_switched_off: None
) -> None:
    pcap = tmp_path / "dns-off.pcap"

    with capture(pcap, "port 53 or port 1053"):
        scan_id = lab.launch(targets=[manifest["scanned_network"]], port_profile="top-100")
        scan = lab.wait(scan_id)

    assert scan["state"] == "completed"
    assert {status for _, status in stored_names(lab, scan_id).values()} == {"disabled"}
    # Not one query about a lab address left the orchestrator.
    lab_queries = [
        line
        for line in packets(pcap, "port 53 or port 1053")
        if "in-addr.arpa" in line and ".3.77.10." in line
    ]
    assert lab_queries == []
    assert packets(pcap, TO_RESOLVER) == []

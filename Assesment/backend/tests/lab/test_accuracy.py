"""Checks 1B.10 and 1B.11 (AC 2): exact open ports in SYN and in connect mode.

Without NET_RAW the scanner cannot send echo requests, so connect-mode
discovery rests on the TCP probes. As the product owner decided (TASK-079,
release checklist 2a), check 1B.11 compares against the hosts that those
probes can find, and the scan must say that ICMP discovery was unavailable.
"""

import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.lab.conftest import TOP_1000, Lab, compose, expected_open, wait_healthy


def assert_exact(lab: Lab, scan_id: int, manifest: dict[str, Any]) -> None:
    """The scan reports exactly the manifest's open ports, and its counts add up."""
    assert lab.open_ports(scan_id) == expected_open(manifest, TOP_1000)

    behaviour = {host["address"]: host for host in manifest["hosts"]}
    for row in lab.hosts(scan_id):
        if not row["reachable"]:
            continue
        not_open = 1000 - row["open_port_count"]
        if behaviour[row["address"]]["closed_ports"] == "reset":
            assert (row["closed_count"], row["filtered_count"]) == (not_open, 0), row
        else:
            assert (row["closed_count"], row["filtered_count"]) == (0, not_open), row


def test_syn_scan_of_the_24_reports_exactly_the_manifests_open_ports(
    lab: Lab, manifest: dict[str, Any]
) -> None:
    """Check 1B.10."""
    scan_id = lab.launch(targets=[manifest["scanned_network"]], port_profile="top-1000")

    scan = lab.wait(scan_id)

    assert (scan["state"], scan["partial"]) == ("completed", False)
    assert scan["technique_used"] == "syn"
    assert scan["icmp_discovery_unavailable"] is False
    assert scan["receive_drops"] == 0
    assert_exact(lab, scan_id, manifest)
    # Every manifest host that can be found by discovery was found.
    found = {row["address"] for row in lab.hosts(scan_id) if row["reachable"]}
    assert found == {host["address"] for host in manifest["hosts"]}


def found_by_tcp_discovery(manifest: dict[str, Any], tcp_ports: list[int]) -> set[str]:
    """The manifest hosts that answer a TCP discovery probe: with a reset from
    a closed port, or a SYN/ACK from an open one."""
    return {
        host["address"]
        for host in manifest["hosts"]
        if host["closed_ports"] == "reset" or set(host["open_ports"]) & set(tcp_ports)
    }


@pytest.fixture
def scanner_without_the_capability() -> Iterator[None]:
    """The scanner started without NET_RAW; the normal scanner is restored afterwards."""
    compose("stop", "scanner")
    started = compose(
        "run", "-d", "--cap-drop", "NET_RAW", "--name", "ndm-scanner-nocap", "scanner"
    )
    assert started.returncode == 0, started.stderr
    try:
        yield
    finally:
        compose("rm", "-f", "-s", "scanner", check=False)
        subprocess.run(["docker", "rm", "-f", "ndm-scanner-nocap"], check=False)
        compose("up", "-d", "scanner")
        wait_healthy()


def test_without_the_capability_the_same_scan_uses_connect(
    lab: Lab, manifest: dict[str, Any], scanner_without_the_capability: None
) -> None:
    """Check 1B.11."""

    def scanner_reports_no_syn() -> bool:
        scanner = lab.client.get("/api/v1/config").json()["scanner"]
        return bool(scanner["alive"]) and not scanner["syn_capable"]

    deadline = time.monotonic() + 60
    while not scanner_reports_no_syn():
        assert time.monotonic() < deadline, "the scanner without the capability did not report"
        time.sleep(1)

    # A forced SYN scan is rejected at creation.
    forced = lab.client.post(
        f"/api/v1/projects/{lab.project_id}/scans",
        json={"targets": [manifest["scanned_network"]], "technique": "syn"},
    )
    assert forced.status_code == 409
    assert forced.json()["error"]["code"] == "syn_unavailable"

    # The automatic technique falls back to connect. Discovery has no echo
    # requests, and the scan says so; every host the TCP probes can find is
    # found, with the same open ports as in the SYN scan.
    scan_id = lab.launch(targets=[manifest["scanned_network"]], port_profile="top-1000")
    scan = lab.wait(scan_id, timeout=1800)

    assert scan["state"] == "completed"
    assert scan["technique_used"] == "connect"
    assert scan["icmp_discovery_unavailable"] is True
    findable = found_by_tcp_discovery(manifest, scan["parameters"]["discovery"]["tcp_ports"])
    found = {row["address"] for row in lab.hosts(scan_id) if row["reachable"]}
    assert found == findable
    expected = expected_open(manifest, TOP_1000)
    assert lab.open_ports(scan_id) == {a: ports for a, ports in expected.items() if a in findable}
    # The lab has such a host on purpose: it answers ping only.
    assert set(expected) - findable == {"10.77.3.21"}

"""Check 1B.17: with the production Compose file and scanner-netinit, the
accuracy check passes and the connection-tracking count stays flat.

The suite runs against the production Compose file already; the lab settings
change only the test switch. What this module adds is the look at the host:
the two rules are there, and probing does not fill the connection-tracking
table.
"""

import threading
import time
from typing import Any

from tests.lab.conftest import TOP_1000, Lab, compose, conntrack_count, expected_open


def test_scanner_netinit_ran_and_exited_cleanly() -> None:
    state = compose("ps", "-a", "--format", "{{.Service}} {{.State}} {{.ExitCode}}").stdout
    assert "scanner-netinit exited 0" in state


def test_the_two_rules_are_installed_once() -> None:
    # scanner-netinit's own listing: the host's raw table, with NET_ADMIN and no sudo.
    rules = compose("run", "--rm", "--no-deps", "scanner-netinit", "status", check=False)
    assert rules.returncode == 0, rules.stderr
    ours = [line for line in rules.stdout.splitlines() if "ndm-scanner-notrack" in line]

    assert len(ours) == 2
    assert sum("-A OUTPUT" in line and "--sport" in line for line in ours) == 1
    assert sum("-A PREROUTING" in line and "--dport" in line for line in ours) == 1


def test_accuracy_holds_and_connection_tracking_stays_flat(
    lab: Lab, manifest: dict[str, Any]
) -> None:
    network = manifest["scanned_network"]
    limits = lab.client.get("/api/v1/config").json()["limits"]
    samples: list[int] = []
    stop = threading.Event()

    def sample() -> None:
        while not stop.wait(1):
            samples.append(conntrack_count())

    before = conntrack_count()
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        # "All targets": most of the /24 does not answer, which is what would
        # leave a tracked entry per probe for two minutes without the rules.
        scan_id = lab.launch(
            targets=[network],
            port_profile="top-1000",
            scan_all_targets=True,
            technique="syn",
            pps=limits["pps"]["max"],
        )
        scan = lab.wait(scan_id)
    finally:
        stop.set()
        sampler.join(timeout=5)
    time.sleep(2)

    # Check 1B.10 again, on this configuration.
    assert (scan["state"], scan["technique_used"], scan["receive_drops"]) == ("completed", "syn", 0)
    assert lab.open_ports(scan_id) == expected_open(manifest, TOP_1000)

    # More than 250,000 probes went out, most of them never answered. The table
    # grew by far less than that: only by what else the host was doing.
    assert scan["probes_sent"] >= 256_000
    growth = max(samples) - before
    assert growth < 2_000, f"the connection-tracking table grew by {growth} entries"

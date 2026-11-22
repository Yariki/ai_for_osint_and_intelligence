"""Check 5.2: a full-range scan of a /24 (256 x 65,535 = 16.8 million probes)
at the global ceiling, with one restart of the whole stack in the middle,
completes with exact results and zero receive drops.

On the Compose lab with the release candidate (NDM_LAB=1, as tests/lab). At
the default ceiling of 5,000 packets per second it takes about 3 h 20 min:
most lab addresses never answer, so nearly every probe is retried, and each
chunk waits out the reply timeout after each round (capacity report, 5.2).
"""

# Fixtures imported by name are used as arguments below.
# ruff: noqa: F811

import time
from typing import Any

from ndm.core.ports import FIRST_PORT, LAST_PORT
from tests.lab.conftest import (  # noqa: F401
    Lab,
    compose,
    expected_open,
    lab,
    manifest,
    wait_healthy,
)
from tests.load.conftest import report

ALL_PORTS = set(range(FIRST_PORT, LAST_PORT + 1))
TIMEOUT_SECONDS = 4 * 3600


def test_a_full_range_scan_survives_a_restart_with_exact_results_and_no_drops(
    lab: Lab, manifest: dict[str, Any]
) -> None:
    ceiling = lab.client.get("/api/v1/config").json()["limits"]["pps"]["max"]
    started = time.monotonic()
    scan_id = lab.launch(
        targets=[manifest["scanned_network"]],
        port_profile="full",
        pps=ceiling,
        scan_all_targets=True,
    )

    # Halfway through, the whole stack is restarted; nothing is done by hand after it.
    lab.wait_until(
        scan_id,
        lambda scan: scan["chunks_total"] > 1 and scan["chunks_done"] >= scan["chunks_total"] / 2,
        timeout=TIMEOUT_SECONDS,
    )
    restarted_at = time.monotonic() - started
    compose("restart")
    wait_healthy()
    scan = lab.wait(scan_id, timeout=TIMEOUT_SECONDS)
    seconds = time.monotonic() - started

    report(
        "endurance",
        {
            "scan_id": scan_id,
            "pps": ceiling,
            "seconds": round(seconds),
            "restart_after_seconds": round(restarted_at),
            "probes_sent": scan["probes_sent"],
            "receive_drops": scan["receive_drops"],
            "state": scan["state"],
        },
    )
    assert (scan["state"], scan["partial"]) == ("completed", False)
    assert scan["port_count"] == len(ALL_PORTS)
    assert scan["probes_sent"] >= 256 * len(ALL_PORTS)
    assert scan["receive_drops"] == 0
    # Exact results: every open port of the manifest, and no other.
    assert lab.open_ports(scan_id) == expected_open(manifest, ALL_PORTS)

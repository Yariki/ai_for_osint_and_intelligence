"""Check 2.8 (AC 5, FR-ENR-2): during a replayed scan, with dataset updates
paused, the only traffic that leaves the host from the orchestrator is DNS to
the configured resolver.

The orchestrator is the only service with a network to the outside, the
`egress` network. Its address there is looked up, and a capture on every
interface of the host keeps what that address sends. Traffic on the internal
network (database, broker) never leaves the host and is not on that address.
"""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.lab.conftest import (
    Lab,
    capture,
    compose,
    orchestrator_egress_address,
    packets,
    replaying,
    wait_healthy,
)

# lab.env points reverse DNS at the lab resolver, published on the transit network.
RESOLVER = "dst host 10.77.0.1 and dst port 1053"


def first_updates_done(lab: Lab) -> bool:
    """Every dataset was checked, or is not configured.

    A dataset that is not configured (no MaxMind key, a registry not opted in)
    is never fetched; the dataset manager puts the reason in its detail. One
    that is configured and was never checked has neither.
    """
    datasets = lab.client.get("/api/v1/datasets").json()
    return bool(datasets) and all(d["checked_at"] or d["detail"] for d in datasets)


@pytest.fixture
def updates_paused(lab: Lab) -> Iterator[None]:
    """The orchestrator restarted with intervals so long that no dataset is due.

    A dataset that was never checked would be due at once, so the first
    updates must have run before the restart; on a fresh stack they take a
    minute or two.
    """
    deadline = time.monotonic() + 600
    while not first_updates_done(lab):
        assert time.monotonic() < deadline, "the first updates have not run"
        time.sleep(5)
    paused = {
        "NDM_GEOLITE_UPDATE_INTERVAL_HOURS": "100000",
        "NDM_RIR_UPDATE_INTERVAL_HOURS": "100000",
    }
    compose("up", "-d", "orchestrator", settings=paused)
    wait_healthy()
    try:
        yield
    finally:
        compose("up", "-d", "orchestrator")
        wait_healthy()


def test_only_dns_to_the_resolver_leaves_from_the_orchestrator(
    public_project: Lab, tmp_path: Path, updates_paused: None
) -> None:
    from tests.tools.replay_findings import load

    source = orchestrator_egress_address()
    pcap = tmp_path / "egress.pcap"

    with capture(pcap, f"src host {source}"), replaying():
        scan_id = public_project.launch(targets=load().scope, port_profile="top-100")
        scan = public_project.wait(scan_id)

    assert scan["state"] == "completed"
    everything = packets(pcap, f"src host {source}")
    to_resolver = packets(pcap, f"src host {source} and {RESOLVER}")
    # Reverse DNS was asked: once per discovered host, with retries at most.
    reachable = [row for row in public_project.hosts(scan_id) if row["reachable"]]
    assert len(reachable) <= len(to_resolver) <= 3 * len(reachable)
    # And nothing else left: no per-address request to any service (AC 5).
    others = packets(pcap, f"src host {source} and not ({RESOLVER})")
    assert others == [], "\n".join(others[:20])
    assert len(everything) == len(to_resolver)

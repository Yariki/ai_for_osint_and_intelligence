"""Check 1B.15: three concurrent scans and one queued share the global rate.

The send rate over any 10-second window stays at or below the global ceiling,
no scan exceeds its own rate, all three advance, and receive drops are zero.
The rate is measured on the wire, from a capture, not from the scanner's own
counters.
"""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from tests.lab.conftest import Lab, capture, packets

WINDOW = 10.0
# Every probe of a scan leaves from the source port of the chunk in flight.
# A capture on every interface sees a probe twice: leaving the host on the lab
# bridge, and again on the bridge's port to the router. Only the first counts.
LINE = re.compile(r"^(\d+\.\d+) br-\S+\s+Out\s+IP \S+\.(\d+) > ")


def most_in_any_window(times: list[float], window: float) -> int:
    most = first = 0
    for last, moment in enumerate(times):
        while moment - times[first] >= window:
            first += 1
        most = max(most, last - first + 1)
    return most


def test_concurrent_scans_share_the_global_rate(
    lab: Lab, manifest: dict[str, Any], tmp_path: Path
) -> None:
    limits = lab.client.get("/api/v1/config").json()["limits"]
    ceiling, concurrent = limits["pps"]["max"], limits["max_concurrent_scans"]
    assert concurrent == 3, "the check is written for the default of three concurrent scans"
    network = manifest["scanned_network"]
    # Each scan asks for the whole ceiling, so together they ask for three times as much.
    body = {
        "targets": [network],
        "port_profile": "top-1000",
        "pps": ceiling,
        "scan_all_targets": True,
        "technique": "syn",
        "retries": 0,
    }
    pcap = tmp_path / "rate.pcap"

    with capture(pcap, f"tcp[tcpflags] == tcp-syn and dst net {network}"):
        scans = [lab.launch(**body) for _ in range(4)]
        # Three run and the fourth waits for a slot.
        lab.wait_until(scans[2], lambda scan: scan["state"] == "running")
        assert lab.scan(scans[3])["state"] == "queued"
        # All three advance side by side.
        for scan_id in scans[:3]:
            lab.wait_until(scan_id, lambda scan: scan["chunks_done"] >= 3)
        assert {lab.scan(scan_id)["state"] for scan_id in scans[:3]} == {"running"}
        finished = [lab.wait(scan_id, timeout=1800) for scan_id in scans]

    assert {scan["state"] for scan in finished} == {"completed"}
    assert [scan["receive_drops"] for scan in finished] == [0, 0, 0, 0]

    # The rate on the wire.
    times: list[float] = []
    by_source_port: dict[int, list[float]] = defaultdict(list)
    for line in packets(pcap, "tcp"):
        match = LINE.match(line)
        if match:
            times.append(float(match.group(1)))
            by_source_port[int(match.group(2))].append(float(match.group(1)))
    assert len(times) > 4 * 256_000 * 0.99, "the capture missed part of the scans"
    times.sort()

    # Over any 10-second window, at or below the global ceiling.
    assert most_in_any_window(times, WINDOW) <= ceiling * WINDOW
    # A source port belongs to one chunk of one scan at a time, so no stream
    # of probes from one source port may exceed a scan's own rate either.
    for source_port, sent in by_source_port.items():
        assert most_in_any_window(sorted(sent), WINDOW) <= ceiling * WINDOW, source_port
    # While three scans ran, the ceiling was used and not just respected.
    assert most_in_any_window(times, WINDOW) >= 0.9 * ceiling * WINDOW

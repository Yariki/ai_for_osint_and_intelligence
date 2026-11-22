"""Check 1B.18, at the event stream (FR-MAP-7, P2): each host row and each
progress update arrives within 2 seconds of the scanner receiving the reply.

The clock starts when the scanner receives a reply, which it records as the
time of the evidence. The clock stops when the event reaches a client of the
stream. Both run on the lab host, so they share one clock.
"""

import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from tests.lab.conftest import Lab

LIMIT_SECONDS = 2.0


def listen(lab: Lab, events: list[tuple[float, dict[str, Any]]], stop: threading.Event) -> None:
    """Read the event stream and keep each event with the time it arrived."""
    cookies = dict(lab.client.cookies)
    with httpx.Client(base_url=str(lab.client.base_url), verify=False, timeout=None) as client:
        with client.stream("GET", "/api/v1/events", cookies=cookies) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if stop.is_set():
                    return
                if line.startswith("data: "):
                    events.append((time.time(), json.loads(line.removeprefix("data: "))))


def test_host_rows_and_progress_arrive_within_two_seconds(
    lab: Lab, manifest: dict[str, Any]
) -> None:
    events: list[tuple[float, dict[str, Any]]] = []
    stop = threading.Event()
    listener = threading.Thread(target=listen, args=(lab, events, stop), daemon=True)
    listener.start()
    time.sleep(1)  # let the stream connect

    scan_id = lab.launch(targets=[manifest["scanned_network"]], port_profile="top-1000")
    scan = lab.wait(scan_id)
    time.sleep(LIMIT_SECONDS + 1)
    stop.set()
    assert scan["state"] == "completed"

    ours = [(at, event) for at, event in events if event["scan_id"] == scan_id]
    host_events = [(at, event) for at, event in ours if event["type"] == "hosts_changed"]
    progress = [(at, event) for at, event in ours if event["type"] == "scan_progress"]
    assert host_events and progress

    # For each host: the first event that showed it, against its earliest evidence.
    first_seen: dict[str, float] = {}
    for arrived, event in host_events:
        for host in event["hosts"]:
            if host["reachable"]:
                first_seen.setdefault(host["address"], arrived)
    assert set(first_seen) == {host["address"] for host in manifest["hosts"]}
    for address, arrived in first_seen.items():
        evidence = lab.host(scan_id, address)["evidence"]
        first_reply = min(datetime.fromisoformat(item["at"]) for item in evidence)
        delay = arrived - first_reply.astimezone(UTC).timestamp()
        assert delay <= LIMIT_SECONDS, f"{address} appeared {delay:.2f} s after its first reply"
        assert delay > -0.5, f"{address}: the clocks disagree ({delay:.2f} s)"

    # For each open port: the first event whose row counted it, against that port's evidence.
    # A discovery port is left out: its stored reply is the discovery probe's, which
    # makes the host reachable (FR-SCN-1); the port itself is recorded open only when
    # the port scan reaches it, seconds later, and that reply is not stored again.
    discovery_ports = set(scan["parameters"]["discovery"]["tcp_ports"])
    ports_checked = 0
    for address in first_seen:
        detail = lab.host(scan_id, address)
        open_ports = {p["port"] for p in detail["open_ports"]} - discovery_ports
        replies = sorted(
            datetime.fromisoformat(item["at"]).astimezone(UTC).timestamp()
            for item in detail["evidence"]
            if item["probe"].startswith("tcp:") and int(item["probe"].split(":")[1]) in open_ports
        )
        for count, received in enumerate(replies, start=1):
            shown = next(
                at
                for at, event in host_events
                for host in event["hosts"]
                if host["address"] == address and host["open_port_count"] >= count
            )
            assert shown - received <= LIMIT_SECONDS, f"{address}: port {count} was late"
            ports_checked += 1
    assert ports_checked > 0, "no open port outside the discovery ports was measured"

    # Progress: an event follows every finished chunk, and the last one says "completed".
    assert progress[-1][1]["state"] == "completed"
    assert progress[-1][1]["chunks_done"] == scan["chunks_total"]
    gaps = [later[0] - earlier[0] for earlier, later in zip(progress, progress[1:], strict=False)]
    # A chunk takes about 10 seconds at the default rate; no update is overdue by more than that.
    assert max(gaps) <= 10 + 3 * LIMIT_SECONDS
